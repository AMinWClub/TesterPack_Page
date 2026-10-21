"""Opt-in release tests on a disposable MongoDB database; no provider HTTP allowed."""

import asyncio
import copy
import io
import json
import os
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import AutoReconnect
from fastapi import HTTPException

from app import selections as selection_api
from app.workers import tester_sms as worker
from app.services.sms import FarazSmsClient, FarazSmsConfig
from tests.test_selections import order, products, settings
from tests.test_tester_release import fixture, written_fields
from tests.test_tester_sms_worker import GENDER_CASES
from tests.test_sms_faraz import FakeHttpResponse


@unittest.skipUnless(os.getenv("TESTER_RUN_MONGO_INTEGRATION") == "1", "Set TESTER_RUN_MONGO_INTEGRATION=1 for isolated MongoDB tests")
class TesterReleaseMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.production_database = MONGODB_DB_NAME
        self.name = "tester_release_it_" + uuid4().hex
        self.owned = False
        self.client = AsyncMongoClient(MONGODB_URI, tz_aware=True, timeoutMS=MONGODB_TIMEOUT_MS,
                                       serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        self.addAsyncCleanup(self.cleanup)
        self.assertNotEqual(self.name, self.production_database)
        self.db = self.client[self.name]
        self.assertEqual(await self.db.list_collection_names(), [])
        self.owned = True
        self.settings = replace(settings(), release_enabled=True, sms_enabled=False)
        self.collections = worker.worker_collections(self.db, self.settings)
        await worker.create_indexes(self.collections)
        self.now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
        blocker = patch("app.services.sms.faraz.urlopen", side_effect=AssertionError("Real SMS forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    async def cleanup(self):
        try:
            if self.owned:
                assert self.name.startswith("tester_release_it_") and self.name != self.production_database
                await self.client.drop_database(self.name)
        finally:
            await self.client.close()

    async def add(self, status="pending", **kwargs):
        source, notification, selected = fixture(status, now=self.now, **kwargs)
        await self.collections["orders"].insert_one(copy.deepcopy(source))
        notification["_id"] = (await self.collections["notifications"].insert_one(copy.deepcopy(notification))).inserted_id
        if selected:
            await self.collections["selections"].insert_one(copy.deepcopy(selected))
        return source, notification, selected

    async def release(self, **kwargs):
        return await worker.release_resolved_tester_orders(self.collections, self.settings, **kwargs)

    async def stored(self, source):
        return await self.collections["orders"].find_one({"_id": source["_id"]})

    async def test_real_state_matrix_early_submission_and_idempotence(self):
        fixtures = []
        for status in ("pending", "submitting", "review_required", "submitted", None):
            for age in (10, 31):
                source, notification, selected = await self.add(status, age=age, written=status == "submitting")
                fixtures.append((source, notification, selected, age == 31 or status in {"submitted", "submitting"}))
        summary = await worker.run_once(self.db, self.settings)
        self.assertEqual((summary["released_expired"], summary["released_submitted"], summary["accepted"]), (3, 4, 0))
        for source, notification, selected, expected in fixtures:
            target = {**source, "state": "not_seen"} if expected else source
            if selected and selected["status"] in {"submitted", "submitting"}:
                target["tester_gender"] = "تستر انتخابی"
            self.assertEqual(await self.stored(source), target)
            self.assertEqual(await self.collections["notifications"].find_one({"order_id": source["_id"]}), notification)
            if selected:
                self.assertEqual(await self.collections["selections"].find_one({"order_id": source["_id"]}), selected)
        summary = await self.release()
        self.assertEqual((summary["released_expired"], summary["released_submitted"]), (0, 0))

    async def test_real_shipping_does_not_block_submitted_or_expired_release(self):
        originals = []
        for shipping in ("تحویل حضوری", "ارسال فوری"):
            for status, age in (("submitted", 10), ("pending", 31), ("submitting", 31), ("review_required", 31), (None, 31)):
                source, _, _ = await self.add(status, age=age, shipping_method=shipping)
                originals.append(source)
        summary = await worker.run_once(self.db, self.settings)
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (2, 8))
        for source in originals:
            target = {**source, "state": "not_seen"}
            if selection_api.ORDER_MARKER in source:
                target["tester_gender"] = "تستر انتخابی"
            self.assertEqual(await self.stored(source), target)

    async def test_real_invalid_timestamps_states_and_scope_do_not_release(self):
        sources = []
        for value in (None, "2026-01-01T00:00:00Z", True, 123, [self.now], self.now + timedelta(days=1)):
            source, _, _ = await self.add("submitted", age=31)
            sources.append(source)
            await self.collections["notifications"].update_one({"order_id": source["_id"]}, {"$set": {"accepted_at": value}})
        source, _, _ = await self.add(age=31)
        sources.append(source)
        await self.collections["notifications"].update_one({"order_id": source["_id"]}, {"$unset": {"accepted_at": ""}})
        for status in ("failed", "exhausted", "retry_pending", ["accepted"]):
            source, _, _ = await self.add(age=31)
            sources.append(source)
            await self.collections["notifications"].update_one({"order_id": source["_id"]}, {"$set": {"status": status}})
        for state in (None, "not_seen", "production", ["pending"]):
            source, _, _ = await self.add(age=31)
            source["state"] = state
            await self.collections["orders"].update_one({"_id": source["_id"]}, {"$set": {"state": state}})
            sources.append(source)
        source, _, _ = await self.add(age=31, date_created="2026-10-13T23:59:59.999999999Z")
        legacy = [source]
        for _, fields, _ in GENDER_CASES:
            source, _, _ = await self.add(age=31)
            source.pop("tester_gender")
            source.update(fields)
            await self.collections["orders"].replace_one({"_id": source["_id"]}, copy.deepcopy(source))
            legacy.append(source)
        self.assertEqual((await self.release())["released_expired"], len(legacy))
        for source in sources:
            self.assertEqual(await self.stored(source), source)
        for source in legacy:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen"})

    async def test_real_late_submission_changes_both_fields_and_preserves_receipt(self):
        source, notification, selected = await self.add("submitted", age=240, tester_gender=None, date_created="2001", shipping_method="تحویل حضوری")
        summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (1, 0))
        self.assertEqual(await self.stored(source), {**source, "state": "not_seen", "tester_gender": "تستر انتخابی"})
        self.assertEqual(await self.collections["selections"].find_one({"order_id": source["_id"]}), selected)
        self.assertEqual(await self.collections["notifications"].find_one({"order_id": source["_id"]}), notification)
        token = worker.token_for_order(source["_id"], self.settings.token_secret)
        self.assertEqual((await selection_api.context(self.db, self.settings, token))["selection"]["status"], "submitted")
        self.assertEqual((await self.release())["release_ready"], 0)

    async def test_real_expiry_preserves_missing_null_and_empty_gender(self):
        originals = []
        for state in ("pending", "submitting", "review_required", None, "submitted"):
            for gender in ({}, {"tester_gender": None}, {"tester_gender": ""}):
                source, _, selected = await self.add(state, age=31)
                source.pop("tester_gender")
                source.update(gender)
                await self.collections["orders"].replace_one({"_id": source["_id"]}, copy.deepcopy(source))
                if state == "submitted":
                    await self.collections["selections"].update_one({"_id": selected["_id"]}, {"$unset": {"submitted_at": ""}})
                originals.append(source)
        summary = await self.release()
        self.assertEqual((summary["released_expired"], summary["released_submitted"]), (len(originals), 0))
        for source in originals:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen"})

    async def test_real_submitted_update_is_atomic_and_database_failure_leaves_both_fields(self):
        source, _, _ = await self.add("submitted", age=31, tester_gender="")
        original = AsyncCollection.update_one
        writes = []
        async def fail_before_write(collection, query, update, *args, **kwargs):
            if collection.database.name == self.name and collection.name == self.settings.orders_collection:
                writes.append(copy.deepcopy(update))
                raise AutoReconnect("synthetic disconnect")
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=fail_before_write):
            self.assertEqual((await self.release())["release_errors"], 1)
        self.assertEqual(await self.stored(source), source)
        self.assertEqual(writes, [{"$set": {"state": "not_seen", "tester_gender": "تستر انتخابی"}}])
        self.assertEqual((await self.release())["released_submitted"], 1)
        self.assertEqual(await self.stored(source), {**source, "state": "not_seen", "tester_gender": "تستر انتخابی"})

    async def test_real_small_batches_concurrency_schema_filter_and_explain(self):
        self.settings = replace(self.settings, release_batch_size=2)
        for _ in range(12):
            await self.add("pending", age=5)
            source, _, selected = await self.add("submitted", age=5)
            await self.collections["selections"].update_one({"_id": selected["_id"]}, {"$set": {"selected_pids": ["duplicate"] * 10}})
        # A Unicode-only blank name must agree with Python's intent validation too.
        source, _, selected = await self.add("submitted", age=5)
        names = ["\u1680\x1c "] * 10
        await self.collections["selections"].update_one({"_id": selected["_id"]}, {"$set": {"selected_names": names}})
        await self.collections["orders"].update_one({"_id": source["_id"]}, {"$set": {"recommended_products": names}})
        ready = [(await self.add(status, age=age))[0] for status, age in
                 (("pending", 31), ("submitted", 10), ("review_required", 31), (None, 31), ("submitting", 31))]
        noise = [fixture(None, now=self.now, date_created="2026-01-01T00:00:00Z")[0] for _ in range(1500)]
        noise += [fixture(None, now=self.now)[0] | {"state": "production"} for _ in range(1500)]
        await self.collections["orders"].insert_many(noise)
        await worker.check_indexes(self.collections)
        explained = await self.db.command({"explain": {
            "aggregate": worker.NOTIFICATIONS, "pipeline": worker.release_pipeline(self.settings),
            "cursor": {}, "hint": worker.RELEASE_SOURCE_INDEX, "collation": {"locale": "simple"},
        }, "verbosity": "executionStats"})
        cursor = explained["stages"][0]["$cursor"]
        plan = json.dumps(cursor["queryPlanner"]["winningPlan"], default=str)
        self.assertIn("IXSCAN", plan)
        self.assertNotIn("COLLSCAN", plan)
        lookups = [stage for stage in explained["stages"] if "$lookup" in stage]
        self.assertEqual(len(lookups), 2)
        for lookup in lookups:
            self.assertEqual(lookup["collectionScans"], 0)
        self.assertIn("_id_", lookups[0]["indexesUsed"])
        self.assertIn("tester_selection_order_unique", lookups[1]["indexesUsed"])
        self.assertLess(cursor["executionStats"]["totalDocsExamined"], 100)
        report = {"fixture_orders": 3030, "source_collection": worker.NOTIFICATIONS,
                  "source_documents_examined": cursor["executionStats"]["totalDocsExamined"],
                  "source_keys_examined": cursor["executionStats"]["totalKeysExamined"],
                  "lookup_indexes": [lookup["indexesUsed"] for lookup in lookups],
                  "lookup_collection_scans": [lookup["collectionScans"] for lookup in lookups]}
        folder = Path(__file__).resolve().parents[1] / "test-artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "tester-release-explain.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("RELEASE EXPLAIN", json.dumps(report))
        summaries = await asyncio.gather(self.release(), self.release())
        total = sum(item["released_expired"] + item["released_submitted"] for item in summaries)
        for _ in range(3):
            item = await self.release()
            total += item["released_expired"] + item["released_submitted"]
        self.assertEqual(total, len(ready))
        for source in ready:
            self.assertEqual((await self.stored(source))["state"], "not_seen")

    async def test_real_deadline_rechecked_at_equality_and_crossing_during_write(self):
        source, _, _ = await self.add(age=31)
        original = AsyncCollection.update_one
        async def equality_guard(collection, query, update, *args, **kwargs):
            if collection.database.name == self.name and update == {"$set": {"state": "not_seen"}}:
                query = copy.deepcopy(query)
                # Both sides are the exact SAME server value, not a client sleep.
                query["$expr"]["$and"][-1] = {"$gte": ["$$NOW", "$$NOW"]}
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=equality_guard):
            self.assertEqual((await self.release())["released_expired"], 1)
        self.now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
        crossing, notification, _ = await self.add("submitted", age=29.9)
        attempted_early = False
        async def cross_deadline(collection, query, update, *args, **kwargs):
            nonlocal attempted_early
            if (collection.database.name == self.name and query.get("_id") == crossing["_id"]
                    and update == {"$set": {"state": "not_seen", "tester_gender": "تستر انتخابی"}}):
                attempted_early = True
                deadline = notification["accepted_at"] + timedelta(minutes=30)
                now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
                await asyncio.sleep(max(0, (deadline - now).total_seconds()) + .1)
            return await original(collection, query, update, *args, **kwargs)
        before = await self.stored(crossing)
        with patch.object(AsyncCollection, "update_one", new=cross_deadline):
            summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (1, 0))
        self.assertTrue(attempted_early)
        self.assertEqual(await self.stored(crossing), {**before, "state": "not_seen", "tester_gender": "تستر انتخابی"})

    async def test_real_on_time_customer_write_and_expired_release_can_overlap(self):
        self.now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
        source, notification, _ = await self.add(age=29.9)
        await self.collections["orders"].update_one({"_id": source["_id"]}, {"$set": {"sms_sent": True}})
        await self.db.products_api.insert_many(products())
        token = worker.token_for_order(source["_id"], self.settings.token_secret)
        written, resume = asyncio.Event(), asyncio.Event()
        original = AsyncCollection.find_one_and_update
        async def pause_final_receipt(collection, query, update, *args, **kwargs):
            if collection.database.name == self.name and update.get("$set", {}).get("status") == "submitted":
                written.set()
                await resume.wait()
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "find_one_and_update", new=pause_final_receipt):
            task = asyncio.create_task(selection_api.submit(self.db, self.settings, token, [str(i) for i in range(1, 11)]))
            try:
                await asyncio.wait_for(written.wait(), timeout=5)
                before = await self.stored(source)
                deadline = notification["accepted_at"] + timedelta(minutes=30)
                self.assertLess(before[selection_api.ORDER_WRITTEN_AT], deadline)
                now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
                await asyncio.sleep(max(0, (deadline - now).total_seconds()) + .1)
                self.assertEqual((await self.release())["released_submitted"], 1)
                self.assertEqual(await self.stored(source), {**before, "state": "not_seen", "tester_gender": "تستر انتخابی"})
            finally:
                resume.set()
                result = await task
        self.assertEqual(result["selection"]["status"], "submitted")
        self.assertEqual((await selection_api.context(self.db, self.settings, token))["selection"]["status"], "submitted")
        self.assertEqual(await self.stored(source), {**before, "state": "not_seen", "tester_gender": "تستر انتخابی"})

    async def test_real_release_preserves_on_time_recovery_and_review_after_expiry(self):
        for status in ("submitting", "review_required"):
            source, _, selected = await self.add(status, age=31, written=True)
            self.assertEqual((await self.release())["released_submitted"], 1)
            before = await self.stored(source)
            token = worker.token_for_order(source["_id"], self.settings.token_secret)
            result = await selection_api.context(self.db, self.settings, token)
            self.assertEqual(result["selection"]["status"], "submitted")
            self.assertEqual(await self.stored(source), before)
        source, _, selected = await self.add("review_required", age=31)
        self.assertEqual((await self.release())["released_expired"], 1)
        token = worker.token_for_order(source["_id"], self.settings.token_secret)
        self.assertEqual((await selection_api.context(self.db, self.settings, token))["selection"]["availability"], "review_required")
        with self.assertRaises(HTTPException) as caught:
            await selection_api.submit(self.db, self.settings, token, selected["selected_pids"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("recommended_products", await self.stored(source))

    async def test_real_written_partial_selection_releases_before_and_hours_after_deadline(self):
        records = []
        for status in ("submitting", "review_required"):
            for age in (10, 31, 240):
                records.append(await self.add(status, age=age, written=True, tester_gender=None))
        summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (len(records), 0))
        for source, notification, selected in records:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen", "tester_gender": "تستر انتخابی"})
            self.assertEqual(await self.collections["selections"].find_one({"order_id": source["_id"]}), selected)
            self.assertEqual(await self.collections["notifications"].find_one({"order_id": source["_id"]}), notification)

    async def test_real_partial_inconsistent_evidence_expires_without_gender_change(self):
        sources = []
        for status in ("submitting", "review_required"):
            for corruption in ("missing_choices", "marker", "late_write", "missing_time", "early_start", "duplicates", "pid_space"):
                source, notification, selected = await self.add(status, age=31, written=True, tester_gender=None)
                if corruption == "missing_choices":
                    source.pop("recommended_products")
                elif corruption == "marker":
                    source[selection_api.ORDER_MARKER] = "wrong"
                elif corruption == "late_write":
                    source[selection_api.ORDER_WRITTEN_AT] = selected["expires_at"]
                elif corruption == "missing_time":
                    source.pop(selection_api.ORDER_WRITTEN_AT)
                elif corruption == "early_start":
                    selected["submitting_at"] = notification["accepted_at"] - timedelta(milliseconds=1)
                elif corruption == "duplicates":
                    selected["selected_pids"] = ["same"] * 10
                else:
                    selected["selected_pids"][0] = " leading-space"
                await self.collections["orders"].replace_one({"_id": source["_id"]}, copy.deepcopy(source))
                await self.collections["selections"].replace_one({"_id": selected["_id"]}, copy.deepcopy(selected))
                sources.append(source)
        summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"]), (0, len(sources)))
        for source in sources:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen"})

    async def test_real_expired_snapshot_fences_missing_null_and_changed_proof(self):
        records, repairs = [], {}
        for field in ("recommended_products", selection_api.ORDER_MARKER, selection_api.ORDER_WRITTEN_AT):
            for missing in (True, False):
                source, notification, selected = await self.add("submitting", age=31, written=True)
                repairs[source["_id"]] = (field, copy.deepcopy(source[field]))
                change = {"$unset": {field: ""}} if missing else {"$set": {field: None}}
                await self.collections["orders"].update_one({"_id": source["_id"]}, change)
                records.append((source, selected))
        original = AsyncCollection.update_one
        calls = {}
        async def repair_before_atomic_release(collection, query, update, *args, **kwargs):
            identity = query.get("_id")
            if (collection.database.name == self.name and collection.name == self.settings.orders_collection
                    and identity in repairs and update.get("$set", {}).get("state") == "not_seen"):
                calls[identity] = calls.get(identity, 0) + 1
                if calls[identity] == 1:
                    field, value = repairs[identity]
                    # Owned fixture only: model a previously-started on-time write
                    # becoming visible between the worker's read and atomic update.
                    await original(collection, {"_id": identity}, {"$set": {field: value}})
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=repair_before_atomic_release):
            summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"], summary["release_errors"]), (len(records), 0, 0))
        self.assertTrue(all(value == 2 for value in calls.values()))
        for source, selected in records:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen", "tester_gender": "تستر انتخابی"})
            self.assertEqual(await self.collections["selections"].find_one({"order_id": source["_id"]}), selected)

    async def test_real_stale_pending_selection_cannot_hide_concurrent_order_write(self):
        source, notification, selected = await self.add("pending", age=31)
        _, _, intent = fixture("submitting", age=31, now=self.now)
        changes = {key: intent[key] for key in ("status", "submission_id", "selected_pids", "selected_names", "submitting_at", "expires_at")}
        expected = written_fields(changes, notification["accepted_at"])
        original = AsyncCollection.find_one
        reads = 0
        async def allocate_after_stale_read(collection, *args, **kwargs):
            nonlocal reads
            result = await original(collection, *args, **kwargs)
            if collection.database.name == self.name and collection.name == worker.SELECTIONS:
                reads += 1
                if reads == 1:
                    await collection.update_one({"_id": selected["_id"]}, {"$set": changes})
                    await self.collections["orders"].update_one({"_id": source["_id"]}, {"$set": expected})
            return result
        with patch.object(AsyncCollection, "find_one", new=allocate_after_stale_read):
            summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"], reads), (1, 0, 2))
        self.assertEqual(await self.stored(source), {**source, **expected, "state": "not_seen", "tester_gender": "تستر انتخابی"})

    async def test_real_changing_evidence_retries_are_bounded_and_next_order_releases(self):
        broken, _, _ = await self.add(age=31)
        good, _, _ = await self.add(age=31)
        self.settings = replace(self.settings, release_batch_size=1)
        original = AsyncCollection.update_one
        calls = 0
        async def keep_changing_proof(collection, query, update, *args, **kwargs):
            nonlocal calls
            if (collection.database.name == self.name and collection.name == self.settings.orders_collection
                    and query.get("_id") == broken["_id"] and update.get("$set", {}).get("state") == "not_seen"):
                calls += 1
                await original(collection, {"_id": broken["_id"]}, {"$set": {selection_api.ORDER_MARKER: str(calls)}})
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=keep_changing_proof), self.assertLogs(worker.logger.name, level="WARNING") as logs:
            summary = await self.release()
        self.assertEqual((calls, summary["release_errors"], summary["released_expired"]), (worker.RELEASE_DECISION_ATTEMPTS, 1, 1))
        self.assertEqual(((await self.stored(broken))["state"], (await self.stored(good))["state"]), ("pending", "not_seen"))
        self.assertNotIn(str(broken["_id"]), "".join(logs.output))
        self.assertEqual((await self.release())["released_expired"], 1)

    async def test_real_production_transition_during_release_is_not_overwritten(self):
        source, _, _ = await self.add("review_required", age=31, written=True)
        original = AsyncCollection.update_one
        async def enter_production(collection, query, update, *args, **kwargs):
            if (collection.database.name == self.name and query.get("_id") == source["_id"]
                    and update.get("$set", {}).get("state") == "not_seen"):
                await original(collection, {"_id": source["_id"]}, {"$set": {"state": "production", "tester_gender": "production gender"}})
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=enter_production):
            summary = await self.release()
        self.assertEqual((summary["released_submitted"], summary["released_expired"], summary["release_skipped"]), (0, 0, 1))
        self.assertEqual(await self.stored(source), {**source, "state": "production", "tester_gender": "production gender"})

    async def test_real_parallel_workers_and_api_finalization_keep_selected_gender(self):
        records = [await self.add(status, age=31, written=True) for status in ("submitting", "review_required")]
        token = worker.token_for_order(records[0][0]["_id"], self.settings.token_secret)
        first, second, receipt = await asyncio.gather(self.release(), self.release(), selection_api.context(self.db, self.settings, token))
        self.assertEqual(first["released_submitted"] + second["released_submitted"], 2)
        self.assertEqual(first["released_expired"] + second["released_expired"], 0)
        self.assertEqual(receipt["selection"]["status"], "submitted")
        for source, _, _ in records:
            self.assertEqual(await self.stored(source), {**source, "state": "not_seen", "tester_gender": "تستر انتخابی"})
        self.assertEqual((await self.release())["release_ready"], 0)

    async def test_real_readonly_audit_finds_previously_released_orders_without_writing(self):
        bad, _, _ = await self.add("submitting", age=31, written=True, tester_gender=None)
        healthy, _, _ = await self.add("submitted", age=31)
        incomplete, _, _ = await self.add("review_required", age=31)
        await self.add("review_required", age=31, written=True)
        for source in (bad, healthy, incomplete):
            values = {"state": "not_seen"}
            if source["_id"] == healthy["_id"]:
                values["tester_gender"] = "تستر انتخابی"
            await self.collections["orders"].update_one({"_id": source["_id"]}, {"$set": values})
        before = {name: await collection.find({}).to_list(length=None) for name, collection in self.collections.items()}
        pipeline = worker.release_pipeline(self.settings, limit=200)
        scope = next(stage["$match"] for stage in pipeline if stage.get("$match", {}).get("state") == "pending")
        scope["state"] = "not_seen"
        scope["$expr"] = {"$and": [scope["$expr"], {"$ne": [{"$ifNull": ["$tester_gender", None]}, "تستر انتخابی"]}]}
        pipeline.insert(-3, {"$match": {"_valid_receipt": True}})
        cursor = await self.collections["notifications"].aggregate(pipeline, hint=worker.RELEASE_SOURCE_INDEX, collation={"locale": "simple"})
        self.assertEqual([row["_id"] async for row in cursor], [bad["_id"]])
        self.assertEqual({name: await collection.find({}).to_list(length=None) for name, collection in self.collections.items()}, before)

    async def test_real_dry_run_is_read_only_and_failed_release_resumes(self):
        source, _, selected = await self.add(age=31)
        before = {name: await collection.find({}).to_list(length=None) for name, collection in self.collections.items()}
        summary = await worker.run_once(self.db, self.settings, dry_run=True)
        self.assertEqual((summary["release_ready"], summary["released_expired"], summary["read_only"]), (1, 0, True))
        self.assertEqual({name: await collection.find({}).to_list(length=None) for name, collection in self.collections.items()}, before)
        good, _, _ = await self.add(age=31)
        self.settings = replace(self.settings, release_batch_size=1)
        original = AsyncCollection.update_one
        async def fail_write(collection, query, update, *args, **kwargs):
            if (collection.database.name == self.name and query.get("_id") == source["_id"]
                    and update == {"$set": {"state": "not_seen"}}):
                raise AutoReconnect("simulated disconnect")
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=fail_write):
            summary = await self.release()
        self.assertEqual((summary["release_errors"], summary["released_expired"]), (1, 1))
        self.assertEqual((await self.stored(good))["state"], "not_seen")
        self.assertEqual((await self.stored(source))["state"], "pending")
        self.assertEqual((await self.release())["released_expired"], 1)

    async def test_release_failure_allows_mock_sms_then_cli_fails_and_next_run_succeeds(self):
        broken, _, _ = await self.add(age=31)
        new_order = order(state="pending")
        await self.collections["orders"].insert_one(copy.deepcopy(new_order))
        config = replace(self.settings, sms_enabled=True)
        sms = FarazSmsClient(FarazSmsConfig(api_key="synthetic-key", line_number="50001"))
        original = AsyncCollection.update_one
        async def fail_release(collection, query, update, *args, **kwargs):
            if collection.database.name == self.name and update == {"$set": {"state": "not_seen"}}:
                raise AutoReconnect("private synthetic database details")
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=fail_release), \
                patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as http:
            summary = await worker.run_once(self.db, config, sms=sms)
        self.assertEqual((summary["release_errors"], summary["accepted"]), (1, 1))
        self.assertEqual((await self.stored(broken))["state"], "pending")
        self.assertTrue((await self.stored(new_order))["sms_sent"])
        http.assert_called_once()
        output = io.StringIO()
        with patch("sys.argv", ["tester_sms", "--once"]), patch("sys.stdout", output), \
                patch.object(worker, "run_command", new=AsyncMock(return_value=summary)), \
                self.assertLogs(worker.logger.name, level="ERROR") as logs:
            self.assertEqual(await asyncio.to_thread(worker.main), 1)
        self.assertEqual(json.loads(output.getvalue()), summary)
        self.assertNotIn("private synthetic database details", "".join(logs.output))
        # Successful retry repairs the release only; an accepted SMS is not resent.
        with patch("app.services.sms.faraz.urlopen", side_effect=AssertionError("No resending accepted SMS")):
            repaired = await worker.run_once(self.db, config, sms=sms)
        self.assertEqual((repaired["release_errors"], repaired["released_expired"]), (0, 1))
        output = io.StringIO()
        with patch("sys.argv", ["tester_sms", "--once"]), patch("sys.stdout", output), \
                patch.object(worker, "run_command", new=AsyncMock(return_value=repaired)):
            self.assertEqual(await asyncio.to_thread(worker.main), 0)
        self.assertEqual(json.loads(output.getvalue()), repaired)


if __name__ == "__main__":
    unittest.main()
