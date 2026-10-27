"""Payment-age fallback: time boundaries, state guards and final-phase behavior."""

import copy
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
        self.collections["orders"].documents = [
            order(str(i), recommended_products=["keep"], **changes) for i, (changes, _) in enumerate(cases)
        ]
        original = copy.deepcopy(self.collections["orders"].documents)
        result = await self.release()
        self.assertEqual(result["released_paid_timeout"], sum(expected for _, expected in cases))
        for before, after, (_, expected) in zip(original, self.collections["orders"].documents, cases):
            self.assertEqual(after, {**before, "state": "not_seen"} if expected else before)
        self.assertEqual((await self.release())["released_paid_timeout"], 0)

    async def test_dry_run_and_disabled_release_do_not_write(self):
        source = order(date_paid=NOW - timedelta(minutes=41))
        self.collections["orders"].documents.append(copy.deepcopy(source))
        result = await worker.run_once(self.db, self.settings, dry_run=True)
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
        self.assertEqual(result["released_paid_timeout"], 1)
        self.assertEqual([item["state"] for item in self.collections["orders"].documents], ["not_seen", "not_seen"])

    async def test_database_failure_is_reported_as_release_error(self):
        self.collections["orders"].fail_update = lambda update: update.get("$set", {}).get("state") == "not_seen"
        result = await worker.run_once(self.db, self.settings)
        self.assertEqual(result["paid_timeout_errors"], 1)
        self.assertEqual(result["release_errors"], 1)
