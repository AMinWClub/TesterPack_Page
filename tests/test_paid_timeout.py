"""Payment-age fallback: time boundaries, state guards and final-phase behavior."""

import copy
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace

from app.workers import tester_sms as worker
from tests.test_selections import settings
from tests.test_tester_sms_worker import FakeDatabase, order


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


class PaidTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = replace(settings(), sms_enabled=False, release_enabled=True)
        self.db = FakeDatabase([])
        self.collections = worker.worker_collections(self.db, self.settings)
        await worker.create_indexes(self.collections)
        self.clock = patch("tests.test_tester_sms_worker.mongo_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def release(self, **kwargs):
        return await worker.release_paid_timeout_orders(self.collections, self.settings, **kwargs)

    async def test_boundary_date_types_and_untouched_fields(self):
        old = NOW - timedelta(minutes=40)
        cases = [
            ({"date_paid": old}, True),
            ({"date_paid": old.replace(tzinfo=None)}, True),
            ({"date_paid": old.isoformat()}, True),
            ({"date_paid": old.replace(microsecond=511869).isoformat().replace("+00:00", "Z")}, False),
            ({"date_paid": (old - timedelta(minutes=1)).replace(microsecond=511869).isoformat().replace("+00:00", "Z")}, True),
            ({"date_paid": old.astimezone(timezone(timedelta(hours=3, minutes=30))).isoformat()}, True),
            ({"date_paid": old - timedelta(days=1), "sms_sent": False}, True),
            ({"date_paid": old + timedelta(milliseconds=1)}, False),
            ({"date_paid": NOW + timedelta(days=1)}, False),
            ({}, False),
            ({"date_paid": None}, False),
            ({"date_paid": "garbage"}, False),
            ({"date_paid": ""}, False),
            ({"date_paid": 0}, False),
            ({"date_paid": [old]}, False),
            ({"date_paid": {}, "state": "pending"}, False),
            ({"date_paid": old, "state": ["pending"]}, False),
            ({"date_paid": old, "state": "not_seen"}, False),
            ({"date_paid": old, "state": "processing"}, False),
        ]
        self.collections["orders"].documents = []
        for i, (changes, _) in enumerate(cases):
            document = order(str(i), recommended_products=["keep"], **changes)
            if "date_paid" not in changes:
                document.pop("date_paid", None)
            self.collections["orders"].documents.append(document)
        original = copy.deepcopy(self.collections["orders"].documents)
        result = await self.release()
        self.assertEqual(result["released_paid_timeout"], sum(expected for _, expected in cases))
        for before, after, (_, expected) in zip(original, self.collections["orders"].documents, cases):
            observed = copy.deepcopy(after)
            if before["state"] == "pending":
                self.assertIn("tester_paid_release_at", observed)
                observed.pop("tester_paid_release_at")
            self.assertEqual(observed, {**before, "state": "not_seen"} if expected else before)
        self.assertEqual((await self.release())["released_paid_timeout"], 0)

    async def test_dry_run_and_disabled_release_do_not_write(self):
        source = order(date_paid=NOW - timedelta(minutes=41))
        self.collections["orders"].documents.append(copy.deepcopy(source))
        result = await self.release(dry_run=True)
        self.assertEqual(result["paid_timeout_ready"], 1)
        self.assertEqual(result["released_paid_timeout"], 0)
        await worker.run_once(self.db, replace(self.settings, release_enabled=False))
        self.assertEqual(self.collections["orders"].documents, [source])

    async def test_fallback_runs_after_existing_release_and_catches_remaining_orders(self):
        from tests.test_tester_release import fixture
        source, notification, selected = fixture("submitted", age=10, now=NOW)
        source["date_paid"] = NOW - timedelta(minutes=45)
        self.collections["orders"].documents.extend([
            source, order("no-notification", date_paid=NOW - timedelta(minutes=40)),
        ])
        self.collections["notifications"].documents.append(notification)
        self.collections["selections"].documents.append(selected)
        result = await worker.run_once(self.db, self.settings)
        self.assertEqual(result["released_submitted"], 1)
        self.assertEqual((await self.release())["released_paid_timeout"], 1)
        self.assertEqual([item["state"] for item in self.collections["orders"].documents], ["not_seen", "not_seen"])

    async def test_database_failure_is_reported_as_release_error(self):
        self.collections["orders"].fail_update = lambda update: update.get("$set", {}).get("state") == "not_seen"
        self.collections["orders"].documents.append(order(date_paid=NOW - timedelta(minutes=41)))
        result = await self.release()
        self.assertEqual(result["paid_timeout_errors"], 1)

    async def test_standalone_dispatch_does_not_load_sms_settings(self):
        args = SimpleNamespace(release_paid_timeout=True, create_paid_index=False)
        with patch.object(worker.WorkerSettings, "from_environment", side_effect=AssertionError("SMS settings must not be read")), \
                patch.object(worker, "run_paid_command", return_value={"released_paid_timeout": 1}) as paid:
            self.assertEqual(await worker.run_command(args), {"released_paid_timeout": 1})
            paid.assert_awaited_once_with(args)

    async def test_backfill_is_bounded_resumable_and_catches_new_orders(self):
        from app.workers.paid_release import release_paid_orders
        self.collections["orders"].documents = [order(str(i), date_paid=NOW - timedelta(minutes=41)) for i in range(7)]
        for remaining in (5, 3, 1, 0):
            summary = await release_paid_orders(self.collections["orders"], limit=2)
            self.assertLessEqual(summary["normalized"], 2)
            self.assertLessEqual(summary["released_paid_timeout"], 2)
            self.assertEqual(sum(item["state"] == "pending" for item in self.collections["orders"].documents), remaining)
        self.collections["orders"].documents.append(order("new", date_paid=NOW - timedelta(minutes=50)))
        self.assertEqual((await release_paid_orders(self.collections["orders"], limit=2))["released_paid_timeout"], 1)

    async def test_backfill_only_never_releases_and_dry_run_never_normalizes(self):
        from app.workers.paid_release import release_paid_orders
        source = order(date_paid=NOW - timedelta(minutes=45))
        self.collections["orders"].documents.append(source)
        await release_paid_orders(self.collections["orders"], normalize_only=True, dry_run=True)
        self.assertNotIn("tester_paid_release_at", source)
        result = await release_paid_orders(self.collections["orders"], normalize_only=True)
        self.assertEqual((result["normalized"], result["released_paid_timeout"]), (1, 0))
        self.assertEqual(source["state"], "pending")
        self.assertEqual((await self.release())["released_paid_timeout"], 1)
