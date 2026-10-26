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


def written_fields(selection, accepted):
    return {"recommended_products": selection["selected_names"].copy(),
            selection_api.ORDER_MARKER: selection["submission_id"],
            selection_api.ORDER_WRITTEN_AT: accepted + timedelta(minutes=2)}


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
                source.update(written_fields(selected, accepted))
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
        self.assertEqual(source, {**original[0], "state": "not_seen", "tester_gender": "تستر انتخابی"})
        self.assertEqual((notification, selected), original[1:])
        self.assertEqual((await self.release())["release_ready"], 0)

    async def test_shipping_does_not_block_accepted_order_release(self):
        original = []
        for shipping in ("تحویل حضوری", "ارسال فوری"):
            for status, age in (("submitted", 10), ("pending", 31), ("submitting", 31), ("review_required", 31), (None, 31)):
                source, _, _ = self.add(status, age=age, shipping_method=shipping)
                original.append(copy.deepcopy(source))
        summary = await worker.run_once(self.db, self.settings)
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (2, 8))
        for before, after in zip(original, self.collections["orders"].documents):
            expected = {**before, "state": "not_seen"}
            if selection_api.ORDER_MARKER in before:
                expected["tester_gender"] = "تستر انتخابی"
            self.assertEqual(after, expected)

    async def test_late_worker_prioritizes_valid_submission_without_old_eligibility(self):
        for age in (31, 240):
            source, notification, selected = self.add("submitted", age=age, date_created="2001", shipping_method="ارسال فوری")
            source.pop("tester_gender")
            before = copy.deepcopy((source, notification, selected))
            result = await self.release()
            self.assertEqual((result["released_submitted"], result["released_expired"]), (1, 0))
            self.assertEqual(source, {**before[0], "state": "not_seen", "tester_gender": "تستر انتخابی"})
            self.assertEqual((notification, selected), before[1:])

    async def test_expired_invalid_or_missing_receipts_preserve_every_gender_value(self):
        originals = []
        for status in ("pending", "submitting", "review_required", None, "submitted"):
            for _, fields, _ in GENDER_CASES:
                source, _, selected = self.add(status, age=31)
                source.pop("tester_gender")
                source.update(fields)
                if status == "submitted":
                    selected["submitted_at"] = selected["expires_at"]  # Invalid receipt proof.
                originals.append(copy.deepcopy(source))
        self.settings = replace(self.settings, release_batch_size=100)
        result = await self.release()
        self.assertEqual((result["released_expired"], result["released_submitted"]), (len(originals), 0))
        for before, after in zip(originals, self.collections["orders"].documents):
            self.assertEqual(after, {**before, "state": "not_seen"})

    async def test_submitted_state_and_gender_change_together_and_failure_is_retryable(self):
        source, _, _ = self.add("submitted", age=31, tester_gender=None)
        original = copy.deepcopy(source)
        orders = self.collections["orders"]
        orders.fail_update = lambda update: update.get("$set", {}).get("state") == "not_seen"
        self.assertEqual((await self.release())["release_errors"], 1)
        self.assertEqual(source, original)
        orders.fail_update = None
        with patch.object(orders, "update_one", wraps=orders.update_one) as update:
            self.assertEqual((await self.release())["released_submitted"], 1)
        self.assertEqual(update.call_args.args[1], {"$set": {"state": "not_seen", "tester_gender": "تستر انتخابی"}})
        source["state"] = "production"
        source["tester_gender"] = "production value"
        self.assertEqual((await self.release())["release_ready"], 0)
        self.assertEqual(source["tester_gender"], "production value")

    async def test_receipt_finalized_after_query_is_refreshed_before_release(self):
        source, _, selected = self.add("submitting", age=31, written=True)
        original = self.collections["selections"].find_one
        async def finalized(*args, **kwargs):
            selected.update(status="submitted", submitted_at=source[selection_api.ORDER_WRITTEN_AT])
            return await original(*args, **kwargs)
        with patch.object(self.collections["selections"], "find_one", side_effect=finalized):
            result = await self.release()
        self.assertEqual((result["released_submitted"], result["released_expired"]), (1, 0))
        self.assertEqual(source["tester_gender"], "تستر انتخابی")

    async def test_crossing_deadline_keeps_valid_submit_priority(self):
        source, _, _ = self.add("submitted", age=10)
        original = self.collections["orders"].update_one
        async def cross_deadline(*args, **kwargs):
            self.clock.return_value = NOW + timedelta(minutes=20)
            return await original(*args, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=cross_deadline):
            result = await self.release()
        self.assertEqual((result["released_submitted"], result["released_expired"]), (1, 0))
        self.assertEqual(source["tester_gender"], "تستر انتخابی")

    async def test_unfinished_or_inconsistent_receipts_do_not_release_early(self):
        for status in ("pending", "submitting", "review_required", None):
            self.add(status, age=10)
        for corruption in ("names", "marker", "duplicate", "blank", "late_write", "missing_time", "marker_newline", "missing_submitted_at"):
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
            elif corruption == "missing_submitted_at":
                selected.pop("submitted_at")
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

    async def test_configured_window_and_frozen_deadline_are_used_in_query_and_atomic_update(self):
        self.settings = replace(self.settings, selection_window_minutes=5)
        source, _, _ = self.add("pending", age=6)
        self.assertEqual((await self.release())["released_expired"], 1)
        self.assertEqual(source["state"], "not_seen")
        source, _, selected = self.add("submitting", age=6)  # Frozen at 30 minutes, no order write yet.
        self.assertEqual((await self.release())["release_ready"], 0)
        selected["expires_at"] = NOW - timedelta(seconds=1)
        self.settings = replace(self.settings, selection_window_minutes=60)
        self.assertEqual((await self.release())["released_expired"], 1)
        self.assertEqual(source["state"], "not_seen")

    async def test_expiration_ignores_selection_state_and_preserves_intent_and_choices(self):
        for status in ("pending", "submitting", "review_required", "submitted", None, "unknown"):
            self.add(status, age=31, written=status in {"submitting", "review_required"})
        original = copy.deepcopy([collection.documents for collection in self.collections.values()])
        summary = await self.release()
        self.assertEqual((summary["released_expired"], summary["released_submitted"]), (3, 3))
        for before, after in zip(original[0], self.collections["orders"].documents):
            expected = {**before, "state": "not_seen"}
            if selection_api.ORDER_MARKER in before:
                expected["tester_gender"] = "تستر انتخابی"
            self.assertEqual(after, expected)
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

    async def test_only_exact_pending_state_matters_and_legacy_fields_are_ignored(self):
        for state in (None, "not_seen", "production", ["pending"], 0):
            source, _, _ = self.add(age=31)
            source["state"] = state
        source, _, _ = self.add(age=31)
        source.pop("state")
        self.add(age=31, date_created="2026-10-13T23:59:59.999999999Z")
        expected = 1  # The old date no longer excludes this pending order.
        for _, fields, _ in GENDER_CASES:
            source, _, _ = self.add(age=31)
            source.pop("tester_gender")
            source.update(fields)
            expected += 1
        summary = await self.release()
        self.assertEqual(summary["released_expired"], expected)
        self.assertEqual(sum(row.get("state") == "not_seen" for row in self.collections["orders"].documents), expected + 1)

    async def test_parallel_workers_change_each_order_only_once(self):
        self.add("submitted", age=10)
        self.add("review_required", age=31)
        first, second = await asyncio.gather(self.release(), self.release())
        self.assertEqual(first["released_submitted"] + second["released_submitted"], 1)
        self.assertEqual(first["released_expired"] + second["released_expired"], 1)

    async def test_written_partial_selections_release_early_late_and_remain_recoverable(self):
        for status in ("submitting", "review_required"):
            for age in (10, 31, 240):
                source, notification, selected = self.add(status, age=age, written=True, tester_gender=None)
                before = copy.deepcopy((source, notification, selected))
                result = await self.release()
                self.assertEqual((result["released_submitted"], result["released_expired"]), (1, 0))
                self.assertEqual(source, {**before[0], "state": "not_seen", "tester_gender": "تستر انتخابی"})
                self.assertEqual((notification, selected), before[1:])

    async def test_partial_selection_requires_complete_consistent_on_time_order_evidence(self):
        for status in ("submitting", "review_required"):
            for corruption in ("missing_names", "marker", "missing_time", "late_time", "early_start", "late_start",
                               "missing_start", "duplicates", "pid_count", "names_count", "blank_name", "short_window",
                               "pid_space", "pid_control", "pid_length"):
                source, notification, selected = self.add(status, age=31, written=True)
                if corruption == "missing_names":
                    source.pop("recommended_products")
                elif corruption == "marker":
                    source[selection_api.ORDER_MARKER] = "wrong"
                elif corruption == "missing_time":
                    source.pop(selection_api.ORDER_WRITTEN_AT)
                elif corruption == "late_time":
                    source[selection_api.ORDER_WRITTEN_AT] = selected["expires_at"]
                elif corruption == "early_start":
                    selected["submitting_at"] = notification["accepted_at"] - timedelta(milliseconds=1)
                elif corruption == "late_start":
                    selected["submitting_at"] = source[selection_api.ORDER_WRITTEN_AT] + timedelta(milliseconds=1)
                elif corruption == "missing_start":
                    selected.pop("submitting_at")
                elif corruption == "duplicates":
                    selected["selected_pids"] = ["same"] * 10
                elif corruption == "pid_count":
                    selected["selected_pids"].pop()
                elif corruption == "names_count":
                    selected["selected_names"].pop()
                    source["recommended_products"] = selected["selected_names"].copy()
                elif corruption == "blank_name":
                    selected["selected_names"][0] = "\u1680\x1c "
                    source["recommended_products"] = selected["selected_names"].copy()
                elif corruption == "pid_space":
                    selected["selected_pids"][0] = " leading-space"
                elif corruption == "pid_control":
                    selected["selected_pids"][0] = "embedded\x1fcontrol"
                elif corruption == "pid_length":
                    selected["selected_pids"][0] = "x" * 129
                else:
                    selected["expires_at"] = source[selection_api.ORDER_WRITTEN_AT]
                before = copy.deepcopy((source, notification, selected))
                result = await self.release()
                self.assertEqual((result["released_expired"], result["released_submitted"]), (1, 0), corruption)
                self.assertEqual(source, {**before[0], "state": "not_seen"})
                self.assertEqual((notification, selected), before[1:])

    async def test_expired_update_racing_order_write_rereads_and_marks_selected(self):
        source, notification, selected = self.add("submitting", age=31)
        expected = written_fields(selected, notification["accepted_at"])
        original = self.collections["orders"].update_one
        writes = []
        async def order_write_between_read_and_update(query, update, **kwargs):
            writes.append(copy.deepcopy(update))
            if len(writes) == 1:
                source.update(copy.deepcopy(expected))
            return await original(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=order_write_between_read_and_update):
            result = await self.release()
        self.assertEqual((result["released_submitted"], result["released_expired"], result["release_errors"]), (1, 0, 0))
        self.assertEqual(writes, [{"$set": {"state": "not_seen"}},
                                  {"$set": {"state": "not_seen", "tester_gender": "تستر انتخابی"}}])
        self.assertEqual(source["tester_gender"], "تستر انتخابی")
        self.assertEqual({key: source[key] for key in expected}, expected)
        self.assertEqual(selected["status"], "submitting")

    async def test_missing_null_and_changed_proof_fields_are_all_fenced(self):
        for field in ("recommended_products", selection_api.ORDER_MARKER, selection_api.ORDER_WRITTEN_AT):
            for missing in (True, False):
                source, notification, selected = self.add("review_required", age=31, written=True)
                expected = copy.deepcopy(source[field])
                if missing:
                    source.pop(field)
                else:
                    source[field] = None
                original = self.collections["orders"].update_one
                calls = 0
                async def repair_field(query, update, **kwargs):
                    nonlocal calls
                    calls += 1
                    source[field] = copy.deepcopy(expected)
                    return await original(query, update, **kwargs)
                with patch.object(self.collections["orders"], "update_one", side_effect=repair_field):
                    result = await self.release()
                self.assertEqual((result["released_submitted"], result["released_expired"], calls), (1, 0, 2))
                self.assertEqual(source["tester_gender"], "تستر انتخابی")

    async def test_order_read_precedes_selection_to_avoid_a_stale_pending_intent(self):
        source, notification, selected = self.add("pending", age=31)
        _, _, intent = fixture("submitting", age=31)
        original = self.collections["selections"].find_one
        calls = 0
        async def allocate_and_write_before_returning_stale_selection(*args, **kwargs):
            nonlocal calls
            result = await original(*args, **kwargs)
            calls += 1
            if calls == 1:
                selected.update({key: intent[key] for key in ("status", "submission_id", "selected_pids", "selected_names", "submitting_at", "expires_at")})
                source.update(written_fields(selected, notification["accepted_at"]))
            return result
        with patch.object(self.collections["selections"], "find_one", side_effect=allocate_and_write_before_returning_stale_selection):
            result = await self.release()
        self.assertEqual((result["released_submitted"], result["released_expired"]), (1, 0))
        self.assertEqual(source["tester_gender"], "تستر انتخابی")
        self.assertEqual(calls, 2)

    async def test_conflicting_evidence_is_bounded_reported_and_does_not_starve_next_order(self):
        broken, _, _ = self.add(age=31)
        good, _, _ = self.add(age=31)
        self.settings = replace(self.settings, release_batch_size=1)
        original = self.collections["orders"].update_one
        calls = 0
        async def churn_proof(query, update, **kwargs):
            nonlocal calls
            if query["_id"] == broken["_id"]:
                calls += 1
                broken[selection_api.ORDER_MARKER] = str(calls)
            return await original(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=churn_proof), self.assertLogs(worker.logger.name, level="WARNING") as logs:
            result = await self.release()
        self.assertEqual((calls, result["release_errors"], result["released_expired"]), (worker.RELEASE_DECISION_ATTEMPTS, 1, 1))
        self.assertEqual((broken["state"], good["state"]), ("pending", "not_seen"))
        self.assertIn("evidence kept changing", "".join(logs.output))
        self.assertNotIn(str(broken["_id"]), "".join(logs.output))
        self.assertEqual((await self.release())["released_expired"], 1)

    async def test_production_state_entered_between_read_and_update_is_never_overwritten(self):
        source, _, _ = self.add("submitting", age=31, written=True)
        original = self.collections["orders"].update_one
        async def enter_production(query, update, **kwargs):
            source.update(state="production", tester_gender="production gender")
            return await original(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=enter_production):
            result = await self.release()
        self.assertEqual((result["released_submitted"], result["released_expired"], result["release_skipped"]), (0, 0, 1))
        self.assertEqual((source["state"], source["tester_gender"]), ("production", "production gender"))

    async def test_production_transition_on_last_retry_is_skipped_without_release_error(self):
        source, _, _ = self.add(age=31)
        original = self.collections["orders"].update_one
        calls = 0
        async def churn_then_enter_production(query, update, **kwargs):
            nonlocal calls
            calls += 1
            source[selection_api.ORDER_MARKER] = str(calls)
            if calls == worker.RELEASE_DECISION_ATTEMPTS:
                source.update(state="production", tester_gender="production gender")
            return await original(query, update, **kwargs)
        with patch.object(self.collections["orders"], "update_one", side_effect=churn_then_enter_production):
            result = await self.release()
        self.assertEqual((result["release_errors"], result["release_skipped"], calls), (0, 1, worker.RELEASE_DECISION_ATTEMPTS))
        self.assertEqual((source["state"], source["tester_gender"]), ("production", "production gender"))

    async def test_readonly_audit_finds_only_old_releases_with_proved_choices_and_wrong_gender(self):
        bad, _, _ = self.add("review_required", age=31, written=True, tester_gender=None)
        healthy, _, _ = self.add("submitted", age=31)
        incomplete, _, _ = self.add("submitting", age=31)
        malformed, _, selected = self.add("review_required", age=31, written=True)
        selected["selected_pids"] = ["duplicate"] * 10
        self.add("submitting", age=31, written=True)  # Pending is not a past release.
        bad["state"] = healthy["state"] = incomplete["state"] = malformed["state"] = "not_seen"
        healthy["tester_gender"] = "تستر انتخابی"
        before = copy.deepcopy([collection.documents for collection in self.collections.values()])
        pipeline = worker.release_pipeline(self.settings, limit=200)
        scope = next(stage["$match"] for stage in pipeline if stage.get("$match", {}).get("state") == "pending")
        scope["state"] = "not_seen"
        scope["$expr"] = {"$and": [scope["$expr"], {"$ne": [{"$ifNull": ["$tester_gender", None]}, "تستر انتخابی"]}]}
        valid_proof = pipeline[-4]["$match"]["$or"][1]
        pipeline.insert(-3, {"$match": valid_proof})
        cursor = await self.collections["notifications"].aggregate(pipeline)
        self.assertEqual([row["_id"] async for row in cursor], [bad["_id"]])
        self.assertEqual([collection.documents for collection in self.collections.values()], before)
        self.assertEqual(self.db.writes, 0)

    async def test_parallel_workers_release_partial_written_orders_once(self):
        for status in ("submitting", "review_required"):
            self.add(status, age=31, written=True)
        results = await asyncio.gather(self.release(), self.release())
        self.assertEqual(sum(row["released_submitted"] for row in results), 2)
        self.assertEqual(sum(row["released_expired"] for row in results), 0)
        self.assertTrue(all(row["tester_gender"] == "تستر انتخابی" for row in self.collections["orders"].documents))

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
