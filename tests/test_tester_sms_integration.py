"""Opt-in real MongoDB tests, using disposable databases and mocked Faraz HTTP."""

import asyncio
import copy
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from uuid import uuid4

from pymongo import AsyncMongoClient
from pymongo.collation import Collation
from pymongo.errors import PyMongoError
from pymongo.asynchronous.collection import AsyncCollection

from app.services.sms import FarazSmsClient, FarazSmsConfig
from app.workers import tester_sms as worker
from tests.test_sms_faraz import FakeHttpResponse


@unittest.skipUnless(os.getenv("TESTER_RUN_MONGO_INTEGRATION") == "1", "Set TESTER_RUN_MONGO_INTEGRATION=1 for isolated MongoDB tests")
class MongoWorkerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.uri = MONGODB_URI
        self.production_database = MONGODB_DB_NAME
        self.timeout = MONGODB_TIMEOUT_MS
        self.database_name = "tester_worker_it_" + uuid4().hex
        self.owned = False
        self.client = self.new_client()
        self.addAsyncCleanup(self.cleanup_database)
        await self.client.admin.command("ping")
        self.assertNotEqual(self.database_name, self.production_database)
        self.db = self.client[self.database_name]
        self.assertEqual(await self.db.list_collection_names(), [])
        self.owned = True
        self.settings = worker.WorkerSettings(
            "preprocess_orders", "123", datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc), sms_enabled=True,
            selection_base_url="https://tester.example.test", token_secret="integration-secret-" + "x" * 40,
        )
        self.sms = FarazSmsClient(FarazSmsConfig(
            api_key="integration-test-key", base_url="https://provider.example.test", line_number="50001", pattern_code="pattern",
        ))
        self.sms.send_pattern = Mock(wraps=self.sms.send_pattern)
        patcher = patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse())
        self.http = patcher.start()
        self.addCleanup(patcher.stop)
        # Block any accidental unpatched urllib HTTP call, while MongoDB is real.
        blocker = patch("urllib.request.urlopen", side_effect=AssertionError("Real HTTP is forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.collections = worker.worker_collections(self.db, self.settings)
        await worker.create_indexes(self.collections)
        await self.collections["orders"].insert_one(self.order())

    def new_client(self):
        return AsyncMongoClient(self.uri, tz_aware=True, serverSelectionTimeoutMS=self.timeout, timeoutMS=self.timeout)

    async def cleanup_database(self):
        try:
            if self.owned:
                assert self.database_name.startswith("tester_worker_it_") and self.database_name != self.production_database
                # Reconnect if the test deliberately closed its original client.
                cleanup = self.new_client()
                try:
                    await cleanup.drop_database(self.database_name)
                finally:
                    await cleanup.close()
        finally:
            await self.client.close()

    def order(self, order_id="order-1", **changes):
        return {"_id": order_id, "product_id": ["999", "123"], "date_created": "2026-10-13T20:30:00Z",
                "phone": "09120000000", **changes}

    async def run_worker(self, **kwargs):
        return await worker.run_once(self.db, self.settings, sms=self.sms, **kwargs)

    async def notification(self):
        return await self.collections["notifications"].find_one({"order_id": "order-1"})

    async def make_retry_due(self):
        await self.collections["notifications"].update_one(
            {"order_id": "order-1"}, {"$set": {"next_retry_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
        )

    async def test_success_indexes_aggregation_and_lookup(self):
        await worker.check_indexes(self.collections)
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 1)
        notification = await self.notification()
        self.assertEqual(notification["attempt_count"], 1)
        self.assertEqual(notification["attempts"][0]["result"], "accepted")
        self.assertTrue((await self.collections["orders"].find_one({"_id": "order-1"}))["sms_sent"])
        await self.run_worker()
        self.http.assert_called_once()

    async def test_date_product_and_old_order_filter(self):
        await self.collections["orders"].insert_many([
            self.order("old", date_created="2026-10-13T20:29:59.999999Z"),
            self.order("fraction", date_created="2026-10-13T20:30:00.83444Z", product_id=[123]),
            self.order("other", product_id=["1234"]), self.order("scalar", product_id="123"),
            self.order("paid-collection-only", status="arbitrary-paid-status"), self.order("sent", sms_sent=True),
            self.order("invalid-date", date_created="2026-13-14T00:00:00Z"),
        ])
        old_notification = {"order_id": "old", "status": "submission_unknown"}
        await self.collections["notifications"].insert_one(old_notification)
        before = await self.collections["notifications"].find_one({"order_id": "old"})
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 3)
        self.assertEqual(await self.collections["notifications"].find_one({"order_id": "old"}), before)
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "old"}))

    async def test_timeout_due_retry_and_constant_link(self):
        self.http.side_effect = TimeoutError("simulated timeout")
        await self.run_worker()
        self.assertEqual((await self.notification())["status"], "retry_pending")
        await self.run_worker()
        self.assertEqual(self.http.call_count, 1)
        await self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        self.assertEqual((await self.notification())["status"], "accepted")
        self.assertEqual((await self.notification())["attempt_count"], 2)
        links = [call.kwargs["attributes"]["link"] for call in self.sms.send_pattern.call_args_list]
        self.assertEqual(links[0], links[1])
        self.assertEqual(await self.collections["selections"].count_documents({}), 1)

    async def test_concurrent_workers_atomic_claim(self):
        await asyncio.gather(self.run_worker(), self.run_worker())
        self.http.assert_called_once()
        self.assertEqual((await self.notification())["attempt_count"], 1)
        self.assertEqual(await self.collections["notifications"].count_documents({}), 1)

    async def test_expired_lease_recovery_and_stale_attempt_fencing(self):
        notifications = self.collections["notifications"]
        await worker.prepare_notification(notifications, "order-1")
        first = await worker.claim_notification(notifications, "order-1", 15, 3)
        await notifications.update_one({"order_id": "order-1"}, {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        await self.run_worker()
        self.assertEqual((await self.notification())["status"], "retry_pending")
        self.http.assert_not_called()
        await self.make_retry_due()
        second = await worker.claim_notification(notifications, "order-1", 15, 3)
        self.assertIsNotNone(second)
        self.assertFalse(await worker.finish_notification(notifications, first, "accepted", provider_data=42))
        self.assertEqual((await self.notification())["attempt_id"], second["attempt_id"])
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "order-1"}))

    async def test_real_client_disconnect_after_mock_success_then_retry(self):
        loop = asyncio.get_running_loop()

        def success_then_disconnect(*args, **kwargs):
            asyncio.run_coroutine_threadsafe(self.client.close(), loop).result(timeout=5)
            return FakeHttpResponse()

        self.http.side_effect = success_then_disconnect
        with self.assertRaises(PyMongoError):
            await self.run_worker()
        self.client = self.new_client()
        self.db = self.client[self.database_name]
        self.collections = worker.worker_collections(self.db, self.settings)
        notification = await self.notification()
        self.assertEqual(notification["status"], "sending")
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "order-1"}))
        await self.collections["notifications"].update_one(
            {"order_id": "order-1"}, {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
        )
        self.http.side_effect = None
        await self.run_worker()
        await self.make_retry_due()
        await self.run_worker()
        self.assertEqual((await self.notification())["status"], "accepted")
        self.assertEqual(self.http.call_count, 2)

    async def test_disconnect_before_dispatch_can_resume(self):
        await self.client.close()
        with self.assertRaises(PyMongoError):
            await self.run_worker()
        self.http.assert_not_called()
        self.client = self.new_client()
        self.db = self.client[self.database_name]
        self.collections = worker.worker_collections(self.db, self.settings)
        await self.run_worker()
        self.http.assert_called_once()

    async def test_accepted_record_survives_disconnect_before_order_sync(self):
        original_update = AsyncCollection.update_one
        disconnected = False

        async def disconnect_at_order_flag(collection, query, update, *args, **kwargs):
            nonlocal disconnected
            if (not disconnected and collection.database.name == self.database_name
                    and collection.name == self.settings.orders_collection and update.get("$set", {}).get("sms_sent") is True):
                disconnected = True
                await self.client.close()
            return await original_update(collection, query, update, *args, **kwargs)

        with patch.object(AsyncCollection, "update_one", new=disconnect_at_order_flag):
            with self.assertRaises(PyMongoError):
                await self.run_worker()
        self.client = self.new_client()
        self.db = self.client[self.database_name]
        self.collections = worker.worker_collections(self.db, self.settings)
        self.assertEqual((await self.notification())["status"], "accepted")
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "order-1"}))
        result = await self.run_worker()
        self.assertEqual(result["repaired"], 1)
        self.http.assert_called_once()

    async def test_global_errors_stop_run_before_next_order(self):
        await self.collections["orders"].insert_one(self.order("order-2"))
        self.http.side_effect = HTTPError("https://provider.example.test", 429, "limited", {}, io.BytesIO(b'error'))
        result = await self.run_worker()
        self.assertEqual(result["halted_http_status"], 429)
        self.http.assert_called_once()
        self.assertEqual(await self.collections["notifications"].count_documents({}), 1)
        self.assertEqual((await self.notification())["status"], "retry_pending")

    async def test_precise_fraction_boundary_is_enforced_by_real_mongodb(self):
        from dataclasses import replace
        self.settings = replace(self.settings, start_at=self.settings.start_at.replace(microsecond=500000))
        await self.collections["orders"].insert_many([
            self.order("before", date_created="2026-10-13T20:30:00.499999Z"),
            self.order("boundary", date_created="2026-10-13T20:30:00.5Z"),
            self.order("after", date_created="2026-10-13T20:30:00.500000001Z"),
        ])
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 2)
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "before"}))

    async def test_exhaustion_is_reported(self):
        self.http.side_effect = TimeoutError("timeout")
        for _ in range(self.settings.max_attempts):
            await self.run_worker()
            await self.make_retry_due()
        result = await self.run_worker()
        self.assertEqual(result["exhausted_total"], 1)
        self.assertEqual((await self.notification())["status"], "exhausted")
        self.assertEqual(self.http.call_count, 3)
        report = await worker.exhausted_report(self.collections, worker.order_filter(self.settings), 10)
        self.assertEqual(report["orders"][0]["order_id"], "order-1")

    async def test_permanent_failed_codes_in_either_field_are_excluded(self):
        variants = [
            {"error_code": "invalid_order_data"}, {"last_error_code": "invalid_order_data"},
            {"error_code": "provider_rejected", "last_error_code": "invalid_order_data"},
            {"error_code": "invalid_order_data", "last_error_code": "provider_rejected"},
        ]
        await self.collections["orders"].insert_many([self.order(f"bad-{index}") for index in range(len(variants))])
        await self.collections["notifications"].insert_many([
            {"order_id": f"bad-{index}", "status": "failed", **fields} for index, fields in enumerate(variants)
        ])
        before = await self.collections["notifications"].find({"status": "failed"}).sort("order_id", 1).to_list(length=None)
        await worker.recover_notifications(self.collections, worker.order_filter(self.settings), 50, self.settings)
        after = await self.collections["notifications"].find({"status": "failed"}).sort("order_id", 1).to_list(length=None)
        self.assertEqual(before, after)
        self.http.assert_not_called()

    async def test_secret_mismatch_preserves_notification_and_stops_sends(self):
        from dataclasses import replace
        await worker.ensure_selection(self.collections["selections"], "order-1", self.settings)
        await worker.prepare_notification(self.collections["notifications"], "order-1")
        before = await self.notification()
        self.settings = replace(self.settings, token_secret="changed-secret-" + "y" * 40)
        with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_TOKEN_SECRET"):
            await self.run_worker()
        self.assertEqual(await self.notification(), before)
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "order-1"}))
        self.http.assert_not_called()

    async def test_dry_run_does_not_change_real_database(self):
        before = {name: await self.db[name].find({}).to_list(length=None) for name in await self.db.list_collection_names()}
        await self.run_worker(dry_run=True)
        after = {name: await self.db[name].find({}).to_list(length=None) for name in await self.db.list_collection_names()}
        self.assertEqual(before, after)
        self.http.assert_not_called()

    async def test_query_explain_uses_orders_and_lookup_indexes(self):
        await self.collections["orders"].insert_many([
            self.order(f"bulk-{index:04d}", product_id=["123"] if index % 100 == 0 else ["other"],
                       date_created="2026-10-13T20:30:00.123456Z" if index % 2 == 0 else "2026-10-12T00:00:00Z")
            for index in range(3000)
        ])
        pipeline = worker.eligible_pipeline(worker.order_filter(self.settings), 10, self.settings.max_attempts)
        explained = await self.db.command({"explain": {
            "aggregate": self.settings.orders_collection, "pipeline": pipeline, "cursor": {},
            "hint": {"product_id": 1, "date_created": 1, "_id": 1}, "collation": {"locale": "simple"},
        }, "verbosity": "executionStats"})
        cursor = explained["stages"][0]["$cursor"]
        stages = []

        def visit(value):
            if isinstance(value, dict):
                if "stage" in value:
                    stages.append(value["stage"])
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(cursor["queryPlanner"]["winningPlan"])
        lookup = next(stage for stage in explained["stages"] if "$lookup" in stage)
        stats = cursor["executionStats"]
        self.assertIn("IXSCAN", stages)
        self.assertNotIn("COLLSCAN", stages)
        self.assertEqual(lookup["collectionScans"], 0)
        self.assertIn("tester_notification_order_unique", lookup["indexesUsed"])
        self.assertLess(stats["totalDocsExamined"], 100)
        report = {
            "fixture_orders": 3001, "orders_plan_stages": stages,
            "orders_documents_examined": stats["totalDocsExamined"], "orders_keys_examined": stats["totalKeysExamined"],
            "orders_execution_ms": stats["executionTimeMillis"], "lookup_indexes": lookup["indexesUsed"],
            "lookup_collection_scans": lookup["collectionScans"], "returned_orders": explained["stages"][-1]["nReturned"],
        }
        folder = Path(__file__).resolve().parents[1] / "test-artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "tester-worker-explain.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("EXPLAIN", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
