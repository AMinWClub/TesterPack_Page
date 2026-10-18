"""Opt-in 20,000-pending-order benchmark, restricted to an owned temporary DB."""

import copy
import hashlib
import json
import os
import statistics
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo import AsyncMongoClient

from app import selections as selection_api
from app.workers import tester_sms as worker
from tests.test_selections import settings
from tests.test_tester_release import fixture


@unittest.skipUnless(os.getenv("TESTER_RUN_RELEASE_PERFORMANCE") == "1", "Set TESTER_RUN_RELEASE_PERFORMANCE=1 for the isolated 20k benchmark")
class TesterReleasePerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.production_database = MONGODB_DB_NAME
        self.name = "tester_release_perf_" + uuid4().hex
        self.owned = False
        # A test-only budget permits measuring slow queries instead of hiding them
        # behind the application's normal timeout. Production settings stay intact.
        self.client = AsyncMongoClient(MONGODB_URI, tz_aware=True, timeoutMS=60000,
                                       serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        self.addAsyncCleanup(self.cleanup)
        self.assertNotEqual(self.name, self.production_database)
        self.db = self.client[self.name]
        self.assertEqual(await self.db.list_collection_names(), [])
        self.owned = True
        self.settings = replace(settings(), release_enabled=True, sms_enabled=False, release_batch_size=50)
        self.collections = worker.worker_collections(self.db, self.settings)
        await worker.create_indexes(self.collections)
        blocker = patch("app.services.sms.faraz.urlopen", side_effect=AssertionError("No SMS in performance tests"))
        blocker.start()
        self.addCleanup(blocker.stop)

    async def cleanup(self):
        try:
            if self.owned:
                assert self.name.startswith("tester_release_perf_") and self.name != self.production_database
                await self.client.drop_database(self.name)
        finally:
            await self.client.close()

    async def measure(self, expected_ids):
        pipeline = worker.release_pipeline(self.settings)
        options = {"hint": worker.RELEASE_SOURCE_INDEX, "collation": {"locale": "simple"}, "maxTimeMS": 45000}
        elapsed = []
        for _ in range(3):
            start = time.perf_counter()
            cursor = await self.collections["notifications"].aggregate(pipeline, batchSize=50, **options)
            rows = await cursor.to_list(length=None)
            elapsed.append(round((time.perf_counter() - start) * 1000, 2))
            self.assertEqual([row["_id"] for row in rows], expected_ids)
        explained = await self.db.command({"explain": {
            "aggregate": worker.NOTIFICATIONS, "pipeline": pipeline, "cursor": {}, **options,
        }, "verbosity": "executionStats"})
        folder = Path(__file__).resolve().parents[1] / "test-artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "tester-release-performance-explain.json").write_text(json.dumps(explained, default=str), encoding="utf-8")
        source = explained["stages"][0]["$cursor"]
        source_stats = source["executionStats"]
        plan = json.dumps(source["queryPlanner"]["winningPlan"], default=str)
        self.assertIn("IXSCAN", plan)
        self.assertNotIn("COLLSCAN", plan)
        lookups = []
        for stage in explained["stages"]:
            if "$lookup" not in stage:
                continue
            self.assertEqual(stage["collectionScans"], 0)
            lookups.append({
                "collection": stage["$lookup"]["from"],
                # $lookup preserves one output row per input here (no unwind).
                # nReturned counts logical row probes, not network requests.
                "row_probes": stage["nReturned"],
                "totalDocsExamined": stage["totalDocsExamined"],
                "totalKeysExamined": stage["totalKeysExamined"],
                "indexes": stage["indexesUsed"], "collection_scans": stage["collectionScans"],
            })
        self.assertEqual(len(lookups), 2)
        return {
            "query_wall_ms": elapsed, "query_wall_median_ms": statistics.median(elapsed),
            "explain_cursor_execution_ms": source_stats["executionTimeMillis"],
            "source_collection": worker.NOTIFICATIONS,
            "source_docs_examined": source_stats["totalDocsExamined"],
            "source_keys_examined": source_stats["totalKeysExamined"],
            "totalDocsExamined": source_stats["totalDocsExamined"] + sum(row["totalDocsExamined"] for row in lookups),
            "totalKeysExamined": source_stats["totalKeysExamined"] + sum(row["totalKeysExamined"] for row in lookups),
            "lookup_stages": len(lookups), "lookup_row_probes": sum(row["row_probes"] for row in lookups),
            "lookups": lookups, "returned_orders": len(expected_ids),
        }

    async def test_twenty_thousand_pending_orders_mixed_and_empty_ready_queue(self):
        now = selection_api.utc_date((await self.db.command("hello"))["localTime"])
        orders, notifications, selections, ready_ids = [], [], [], []
        for index in range(20000):
            source = {"_id": ObjectId(), "state": "pending", "tester_gender": "female",
                      "date_created": "2026-10-14T00:00:00Z"}
            if index >= 19900:
                status = "submitted" if index % 2 else "review_required"
                age = 5 if status == "submitted" else 31
                source, notification, selected = fixture(status, age=age, now=now)
                notifications.append(notification)
                selections.append(selected)
                ready_ids.append(source["_id"])
            elif index >= 8000:
                accepted = index >= 14000
                notifications.append({"order_id": source["_id"], "status": "accepted" if accepted else "retry_pending",
                                      "accepted_at": now - timedelta(minutes=5) if accepted else None})
                selections.append({"order_id": source["_id"], "status": "pending",
                                   "token_hash": hashlib.sha256(source["_id"].binary).hexdigest(),
                                   "token_version": worker.TOKEN_VERSION})
                source["sms_sent"] = accepted
            orders.append(source)
        await self.collections["orders"].insert_many(copy.deepcopy(orders))
        await self.collections["notifications"].insert_many(notifications)
        await self.collections["selections"].insert_many(selections)
        self.assertEqual(await self.collections["orders"].count_documents({"state": "pending"}), 20000)
        report = {
            "fixture_pending_orders": 20000,
            "distribution": {"no_notification": 8000, "unsuccessful_notification": 6000,
                             "accepted_unexpired_pending": 5900, "ready_expired": 50, "ready_submitted": 50},
            "batch_size": 50, "production_timeout_changed": False,
            "ready_at_end": await self.measure(ready_ids[:50]),
        }
        # All changes are confined to the fixture. Measure the common no-work run
        # too, where every remaining Pending order is currently unreleasable.
        await self.collections["orders"].update_many({"_id": {"$in": ready_ids}}, {"$set": {"state": "not_seen"}})
        report["no_ready_orders"] = await self.measure([])
        report["server_version"] = (await self.db.command("buildInfo"))["version"]
        folder = Path(__file__).resolve().parents[1] / "test-artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "tester-release-performance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("RELEASE PERFORMANCE", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
