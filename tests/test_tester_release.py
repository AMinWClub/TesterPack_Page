"""Release-phase unit tests: synthetic orders, mocked MongoDB clock, no real SMS."""

import asyncio
import copy
import hashlib
import io
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from bson import ObjectId
from pymongo.errors import AutoReconnect

from app import selections as selection_api
from app.services.sms import FarazSmsClient, FarazSmsConfig
from app.workers import tester_sms as worker
from tests.test_selections import order, settings
from tests.test_tester_sms_worker import FakeDatabase, GENDER_CASES
from tests.test_sms_faraz import FakeHttpResponse

NOW = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)


def fixture(status="pending", *, age=30, written=False, now=NOW, **changes):
    """One consistent synthetic order + accepted notification + optional intent."""
    source = order(state="pending", sms_sent=True, **changes)
    accepted = now - timedelta(minutes=age)
    notification = {"order_id": source["_id"], "status": "accepted", "accepted_at": accepted}
    selected = None
    if status is not None:
        selected = {"_id": ObjectId(), "order_id": source["_id"], "status": status,
                    "token_version": worker.TOKEN_VERSION,
                    "token_hash": hashlib.sha256(worker.token_for_order(source["_id"], settings().token_secret).encode()).hexdigest()}
        if status in {"submitted", "submitting", "review_required"}:
            selected.update(submission_id=ObjectId().binary.hex() + "a" * 8,
                            selected_pids=[str(i) for i in range(10)],
                            selected_names=[f"$Exact source name {i}" for i in range(10)],
                            submitting_at=accepted + timedelta(minutes=1),
                            expires_at=accepted + timedelta(minutes=30))
            if status == "submitted" or written:
                source.update(recommended_products=selected["selected_names"].copy(),
                              tester_selection_submission_id=selected["submission_id"],
                              tester_selection_written_at=accepted + timedelta(minutes=2))
            if status == "submitted":
                selected["submitted_at"] = source[selection_api.ORDER_WRITTEN_AT]
    return source, notification, selected


class TesterReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase([])
        self.settings = replace(settings(), sms_enabled=False, release_enabled=True)
        self.collections = worker.worker_collections(self.db, self.settings)
        await worker.create_indexes(self.collections)
        self.db.writes = 0
        for target, options in (
            ("tests.test_tester_sms_worker.mongo_now", {"return_value": NOW}),
            ("socket.create_connection", {"side_effect": AssertionError("No network in unit tests")}),
            ("app.services.sms.faraz.urlopen", {"side_effect": AssertionError("No real SMS")}),
        ):
            patcher = patch(target, **options)
            patched = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith("mongo_now"):
                self.clock = patched

    def add(self, status="pending", **kwargs):
        source, notification, selected = fixture(status, **kwargs)
        self.collections["orders"].documents.append(source)
        self.collections["notifications"].documents.append(notification)
        if selected:
            self.collections["selections"].documents.append(selected)
        return source, notification, selected

    async def release(self, **kwargs):
        return await worker.release_resolved_tester_orders(self.collections, self.settings, **kwargs)

    async def test_submitted_releases_early_and_repeated_run_is_idempotent(self):
        source, notification, selected = self.add("submitted", age=10)
        original = copy.deepcopy((source, notification, selected))
        summary = await self.release()
        self.assertEqual(summary["released_submitted"], 1)
        self.assertEqual(summary["released_expired"], 0)
        self.assertEqual(source, {**original[0], "state": "not_seen"})
        self.assertEqual((notification, selected), original[1:])
        self.assertEqual((await self.release())["release_ready"], 0)

    async def test_sms_shipping_exclusion_does_not_block_accepted_order_release(self):
        original = []
        for shipping in ("تحویل حضوری", "ارسال فوری"):
            for status, age in (("submitted", 10), ("pending", 31), ("submitting", 31), ("review_required", 31), (None, 31)):
                source, _, _ = self.add(status, age=age, shipping_method=shipping)
                original.append(copy.deepcopy(source))
        summary = await worker.run_once(self.db, self.settings)
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (2, 8))
        for before, after in zip(original, self.collections["orders"].documents):
            self.assertEqual(after, {**before, "state": "not_seen"})

    async def test_unfinished_or_inconsistent_receipts_do_not_release_early(self):
        for status in ("pending", "submitting", "review_required", None):
            self.add(status, age=10, written=status in {"submitting", "review_required"})
        for corruption in ("names", "marker", "duplicate", "blank", "late_write", "missing_time", "marker_newline"):
            source, _, selected = self.add("submitted", age=10)
            if corruption == "names":
                source["recommended_products"] = ["different"] * 10
            elif corruption == "marker":
                source[selection_api.ORDER_MARKER] = "b" * 32
            elif corruption == "duplicate":
                selected["selected_pids"] = ["1"] * 10
            elif corruption == "blank":
                selected["selected_names"][0] = "\u1680\x1c "
                source["recommended_products"] = selected["selected_names"].copy()
            elif corruption == "late_write":
                source[selection_api.ORDER_WRITTEN_AT] = selected["expires_at"]
            elif corruption == "marker_newline":
                selected["submission_id"] += "\n"
                source[selection_api.ORDER_MARKER] = selected["submission_id"]
            else:
                source.pop(selection_api.ORDER_WRITTEN_AT)
        before = copy.deepcopy(self.collections["orders"].documents)
        self.assertEqual((await self.release())["release_ready"], 0)
        self.assertEqual(self.collections["orders"].documents, before)

    async def test_exact_deadline_is_inclusive_and_one_millisecond_before_is_not(self):
        source, _, _ = self.add()
        self.clock.return_value = NOW - timedelta(milliseconds=1)
        self.assertEqual((await self.release())["release_ready"], 0)
        self.clock.return_value = NOW
        self.assertEqual((await self.release())["released_expired"], 1)
        self.assertEqual(source["state"], "not_seen")

    async def test_expiration_ignores_selection_state_and_preserves_intent_and_choices(self):
        for status in ("pending", "submitting", "review_required", "submitted", None, "unknown"):
            self.add(status, age=31, written=status in {"submitting", "review_required"})
        original = copy.deepcopy([collection.documents for collection in self.collections.values()])
        summary = await self.release()
        self.assertEqual(summary["released_expired"], 6)
        for before, after in zip(original[0], self.collections["orders"].documents):
            self.assertEqual(after, {**before, "state": "not_seen"})
        self.assertEqual([collection.documents for collection in self.collections.values()][1:], original[1:])

    async def test_missing_invalid_future_or_nonaccepted_timestamp_has_no_deadline(self):
        self.add(age=31)
        self.collections["notifications"].documents.pop()
        for value in (None, "2026-01-01T00:00:00Z", 0, True, [], [NOW], NOW + timedelta(seconds=1)):
            _, notification, _ = self.add("submitted", age=31)
            notification["accepted_at"] = value
        _, notification, _ = self.add("submitted", age=31)
        notification.pop("accepted_at")
        for status in ("retry_pending", "failed", "exhausted", ["accepted"]):
            _, notification, _ = self.add("submitted", age=31)
            notification["status"] = status
        self.assertEqual((await self.release())["release_ready"], 0)

    async def test_only_exact_pending_state_changes_and_original_eligibility_is_preserved(self):
        for state in (None, "not_seen", "production", ["pending"], 0):
            source, _, _ = self.add(age=31)
            source["state"] = state
        source, _, _ = self.add(age=31)
        source.pop("state")
        self.add(age=31, date_created="2026-10-13T23:59:59.999999999Z")
        expected = 0
        for _, fields, eligible in GENDER_CASES:
            source, _, _ = self.add(age=31)
            source.pop("tester_gender")
            source.update(fields)
            expected += eligible
        summary = await self.release()
        self.assertEqual(summary["released_expired"], expected)
        self.assertEqual(sum(row.get("state") == "not_seen" for row in self.collections["orders"].documents), expected + 1)

    async def test_parallel_workers_change_each_order_only_once(self):
        self.add("submitted", age=10)
        self.add("review_required", age=31)
        first, second = await asyncio.gather(self.release(), self.release())
        self.assertEqual(first["released_submitted"] + second["released_submitted"], 1)
        self.assertEqual(first["released_expired"] + second["released_expired"], 1)

    async def test_atomic_write_rechecks_clock_and_receipt_and_handles_crossing_deadline(self):
        source, _, _ = self.add("submitted", age=10)
        original_update = self.collections["orders"].update_one
        async def changed_receipt(query, update, **kwargs):
            source["recommended_products"] = ["externally changed"] * 10
            return await original_update(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=changed_receipt):
            self.assertEqual((await self.release())["release_skipped"], 1)
        self.assertEqual(source["state"], "pending")
        source["recommended_products"] = self.collections["selections"].documents[0]["selected_names"].copy()
        async def crosses_deadline(query, update, **kwargs):
            source["recommended_products"] = ["externally changed"] * 10
            self.clock.return_value = NOW + timedelta(minutes=20)
            return await original_update(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=crosses_deadline):
            self.assertEqual((await self.release())["released_expired"], 1)
        self.assertEqual(source["recommended_products"], ["externally changed"] * 10)
        # A server clock moving backwards must also be rechecked in UPDATE.
        self.clock.return_value = NOW
        expired, _, _ = self.add(age=30)
        async def clock_rollback(query, update, **kwargs):
            self.clock.return_value = NOW - timedelta(milliseconds=1)
            return await original_update(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=clock_rollback):
            self.assertEqual((await self.release())["release_skipped"], 1)
        self.assertEqual(expired["state"], "pending")

    async def test_small_batches_do_not_starve_behind_unready_or_bad_receipts(self):
        self.settings = replace(self.settings, release_batch_size=1)
        for index in range(12):
            self.add("pending", age=5)
            source, _, selected = self.add("submitted", age=5)
            selected["selected_pids"] = ["duplicate"] * 10
        ready = [self.add(status, age=age)[0] for status, age in
                 (("pending", 31), ("submitted", 10), ("review_required", 31), (None, 31))]
        for _ in ready:
            summary = await self.release()
            self.assertEqual(sum(summary[key] for key in ("released_submitted", "released_expired")), 1)
        self.assertTrue(all(row["state"] == "not_seen" for row in ready))

    async def test_dry_run_and_sms_disabled_default_remain_read_only(self):
        self.add(age=31)
        before = copy.deepcopy([collection.documents for collection in self.collections.values()])
        with patch.object(worker, "FarazSmsClient", side_effect=AssertionError("Release needs no Faraz client")):
            summary = await worker.run_once(self.db, self.settings, dry_run=True)
        self.assertEqual((summary["release_ready"], summary["released_expired"], summary["read_only"]), (1, 0, True))
        summary = await worker.run_once(self.db, replace(self.settings, release_enabled=None))
        self.assertFalse(summary["release_enabled"])
        self.assertTrue(summary["read_only"])
        self.assertEqual([collection.documents for collection in self.collections.values()], before)
        self.assertEqual(self.db.writes, 0)

    async def test_release_runs_with_sms_disabled_or_broken_sms_configuration(self):
        first, _, _ = self.add(age=31)
        with patch.object(worker, "FarazSmsClient", side_effect=AssertionError("No SMS when disabled")):
            summary = await worker.run_once(self.db, self.settings)
        self.assertFalse(summary["read_only"])
        self.assertEqual(first["state"], "not_seen")
        second, _, _ = self.add(age=31)
        with self.assertRaises(worker.WorkerConfigurationError):
            await worker.run_once(self.db, replace(self.settings, sms_enabled=True, token_secret=""))
        self.assertEqual(second["state"], "not_seen")

    async def test_one_database_error_does_not_stop_remaining_release_orders(self):
        self.settings = replace(self.settings, release_batch_size=1)
        broken, _, _ = self.add(age=31)
        good, _, _ = self.add(age=31)
        original = self.collections["orders"].update_one
        async def fail_one(query, update, **kwargs):
            if query["_id"] == broken["_id"]:
                raise AutoReconnect("private database details")
            return await original(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=fail_one), \
                self.assertLogs(worker.logger.name, level="WARNING") as logs:
            summary = await self.release()
        self.assertEqual((summary["release_errors"], summary["released_expired"]), (1, 1))
        self.assertEqual(good["state"], "not_seen")
        self.assertNotIn("private database details", "".join(logs.output))

    async def test_faraz_failures_follow_release_and_do_not_share_its_batch_limit(self):
        for code in (401, 403, 429):
            for collection in self.collections.values():
                collection.documents.clear()
            released = [self.add(age=31)[0] for _ in range(3)]
            self.collections["orders"].documents.append(order(state="pending"))
            self.settings = replace(self.settings, sms_enabled=True, release_batch_size=3)
            sms = FarazSmsClient(FarazSmsConfig(api_key="test", line_number="50001"))
            error = HTTPError("https://provider.example", code, "synthetic error", {}, io.BytesIO(b"error"))
            with patch("app.services.sms.faraz.urlopen", side_effect=error):
                summary = await worker.run_once(self.db, self.settings, sms=sms, limit=1)
            self.assertEqual((summary["released_expired"], summary["halted_http_status"]), (3, code))
            self.assertTrue(all(row["state"] == "not_seen" for row in released))

    async def test_index_is_checked_and_never_created_by_regular_run(self):
        self.add(age=31)
        self.collections["notifications"].indexes.pop(worker.RELEASE_SOURCE_INDEX)
        with self.assertRaisesRegex(worker.WorkerConfigurationError, "create-indexes"):
            await self.release()
        self.assertEqual(self.db.writes, 0)

    async def test_release_query_error_does_not_block_sms_processing(self):
        source, _, _ = self.add(age=31)
        self.collections["orders"].documents.append(order(state="pending"))
        settings = replace(self.settings, sms_enabled=True)
        sms = FarazSmsClient(FarazSmsConfig(api_key="test", line_number="50001"))
        original = self.collections["notifications"].aggregate
        async def fail_release_query(pipeline, **kwargs):
            if kwargs.get("hint") == worker.RELEASE_SOURCE_INDEX:
                raise AutoReconnect("synthetic database interruption")
            return await original(pipeline, **kwargs)
        with patch.object(self.collections["notifications"], "aggregate", side_effect=fail_release_query), \
                patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()):
            summary = await worker.run_once(self.db, settings, sms=sms)
        self.assertEqual((summary["release_errors"], summary["accepted"]), (1, 1))
        self.assertEqual(source["state"], "pending")


if __name__ == "__main__":
    unittest.main()
