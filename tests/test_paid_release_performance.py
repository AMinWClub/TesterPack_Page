"""Opt-in indexed payment release benchmark with an owned temporary database."""

import json
import os
import time
import unittest
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from pymongo import AsyncMongoClient

from app.workers.paid_release import PAID_FIELD, PAID_INDEX, create_index, exact_pending, release_paid_orders


@unittest.skipUnless(os.getenv("TESTER_RUN_PAID_PERFORMANCE") == "1", "Opt-in 20k MongoDB benchmark")
class PaidReleasePerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexed_backfill_and_due_queries_with_20000_orders(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        name = "tester_paid_perf_" + uuid4().hex
        self.assertNotEqual(name, MONGODB_DB_NAME)
        client = AsyncMongoClient(MONGODB_URI, tz_aware=True, timeoutMS=60000, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        owned = False
        try:
            db = client[name]
            self.assertEqual(await db.list_collection_names(), [])
            owned = True
            orders = db["preprocess_orders"]
            await create_index(orders)
            now = (await db.command("hello"))["localTime"]
            rows = []
            for i in range(20000):
                paid = now - timedelta(minutes=45) if i < 1000 else now - timedelta(minutes=10)
                doc = {"_id": i, "state": "pending", "date_paid": paid}
                if i >= 100:
                    doc[PAID_FIELD] = paid + timedelta(minutes=40)
                rows.append(doc)
            await orders.insert_many(rows)
            pipelines = {
                "backfill": [{"$match": {"state": "pending", PAID_FIELD: None, "$expr": exact_pending()}},
                             {"$sort": {"_id": 1}}, {"$limit": 51}],
                "due": [{"$match": {"state": "pending", PAID_FIELD: {"$lte": now}, "$expr": exact_pending()}},
                        {"$sort": {PAID_FIELD: 1, "_id": 1}}, {"$limit": 51}],
            }
            results = {}
            for key, pipeline in pipelines.items():
                start = time.perf_counter()
                cursor = await orders.aggregate(pipeline, hint=PAID_INDEX, collation={"locale": "simple"})
                self.assertEqual(len(await cursor.to_list()), 51)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                explained = await db.command({"explain": {"aggregate": orders.name, "pipeline": pipeline,
                    "cursor": {}, "hint": PAID_INDEX, "collation": {"locale": "simple"}}, "verbosity": "executionStats"})
                source = explained.get("executionStats") or explained["stages"][0]["$cursor"]["executionStats"]
                plan = json.dumps(explained.get("queryPlanner") or explained["stages"][0]["$cursor"]["queryPlanner"], default=str)
                self.assertIn("IXSCAN", plan)
                self.assertNotIn("COLLSCAN", plan)
                self.assertLessEqual(source["totalDocsExamined"], 100)
                self.assertLessEqual(source["totalKeysExamined"], 100)
                results[key] = {"wall_ms": elapsed, "docs": source["totalDocsExamined"], "keys": source["totalKeysExamined"]}
            with patch("app.services.sms.faraz.urlopen", side_effect=AssertionError("No SMS")):
                start = time.perf_counter()
                result = await release_paid_orders(orders, limit=10)
                results["batch_wall_ms"] = round((time.perf_counter() - start) * 1000, 2)
            self.assertEqual(result["normalized"], 10)
            self.assertEqual(result["released_paid_timeout"], 10)
            self.assertTrue(result["backfill_pending"])
            self.assertTrue(result["release_pending"])
            print("PAID_RELEASE_20K " + json.dumps(results, sort_keys=True))
        finally:
            if owned:
                assert name.startswith("tester_paid_perf_") and name != MONGODB_DB_NAME
                await client.drop_database(name)
            await client.close()
