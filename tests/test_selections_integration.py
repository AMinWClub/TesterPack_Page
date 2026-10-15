"""Opt-in real MongoDB order flow; every write is to an owned temporary database."""

import asyncio
import copy
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import AutoReconnect

from app import selections as service
from app.catalog import CatalogCache, router as catalog_router
from app.services.sms import FarazSmsClient, FarazSmsConfig
from app.workers import tester_sms as worker
from tests.test_selections import make_app, order, products, request, settings
from tests.test_tester_sms_worker import SimulatedCrash


@unittest.skipUnless(os.getenv("TESTER_RUN_SELECTION_INTEGRATION") == "1", "Set TESTER_RUN_SELECTION_INTEGRATION=1 for isolated MongoDB tests")
class SelectionMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.uri, self.production_database, self.timeout = MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.database_name = "tester_selection_it_" + uuid4().hex
        self.client = self.new_client()
        self.owned = False
        self.addAsyncCleanup(self.cleanup)
        self.assertNotEqual(self.database_name, self.production_database)
        self.db = self.client[self.database_name]
        self.assertEqual(await self.db.list_collection_names(), [])
        self.owned = True
        self.config = settings()
        self.source_order = order()
        await self.db.preprocess_orders.insert_one(self.source_order)
        self.rows = products() + [
            {"pid": index, "name": f"Exact source name {index}", "vol_idx": index, "variations": {"a": {"in_stock": True}}}
            for index in range(21, 511)
        ]
        await self.db.products_api.insert_many(copy.deepcopy(self.rows))
        await self.db.fragrantica_products.insert_one({"pid": "1", "name": "Must not be saved instead of primary name"})
        await worker.create_indexes(worker.worker_collections(self.db, self.config))
        self.token = (await worker.ensure_selection(self.db[worker.SELECTIONS], self.source_order["_id"], self.config)).rsplit("/", 1)[1]
        self.pids = [str(index) for index in range(1, 11)]
        self.app = make_app(self.db, self.config)
        self.app.include_router(catalog_router)
        self.app.state.catalog = CatalogCache(self.db)
        blocker = patch("app.services.sms.faraz.urlopen", side_effect=AssertionError("Real SMS is forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def new_client(self):
        return AsyncMongoClient(self.uri, tz_aware=True, timeoutMS=self.timeout, serverSelectionTimeoutMS=self.timeout)

    async def cleanup(self):
        try:
            if self.owned:
                assert self.database_name.startswith("tester_selection_it_") and self.database_name != self.production_database
                cleanup = self.new_client()
                try:
                    await cleanup.drop_database(self.database_name)
                finally:
                    await cleanup.close()
        finally:
            await self.client.close()

    async def call(self, action="context", **extra):
        return await request(self.app, f"/api/tester/selection/{action}", {"token": self.token, **extra})

    async def stored_order(self):
        return await self.db.preprocess_orders.find_one({"_id": self.source_order["_id"]})

    async def selection(self):
        return await self.db[worker.SELECTIONS].find_one({"order_id": self.source_order["_id"]})

    async def test_worker_to_submission_to_readonly_and_worker_regression(self):
        sms = FarazSmsClient(FarazSmsConfig(api_key="test", base_url="https://provider.example.test", line_number="50001", pattern_code="test"))
        sms.send_pattern = Mock(return_value=SimpleNamespace(status_code=200, data=42))
        result = await worker.run_once(self.db, replace(self.config, sms_enabled=True), sms=sms)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(sms.send_pattern.call_args.kwargs["attributes"]["link"], self.config.selection_base_url + "/" + self.token)
        self.assertEqual((await self.call())[1]["order"]["phone"], "09123456789")
        self.assertEqual((await request(self.app, "/api/tester/catalog", method="GET"))[1]["total"], 498)
        pids = list(reversed(self.pids))
        status, body, _ = await self.call("submit", pids=pids)
        self.assertEqual(status, 200)
        expected = [self.rows[int(pid)-1]["name"] for pid in pids]
        self.assertEqual((await self.stored_order())["recommended_products"], expected)
        self.assertEqual(body["selection"]["selected_names"], expected)
        self.assertEqual((await self.call("submit", pids=pids))[1], body)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        saved = await self.selection()
        await worker.ensure_selection(self.db[worker.SELECTIONS], self.source_order["_id"], self.config)
        self.assertEqual(await self.selection(), saved)
        await worker.run_once(self.db, replace(self.config, sms_enabled=True), sms=sms)
        sms.send_pattern.assert_called_once()
        # Owned fixtures only: receipts must survive later stock disappearance.
        await self.db.products_api.delete_many({})
        self.assertEqual((await self.call())[1]["selection"]["selected_names"], expected)

    async def test_fresh_stock_exact_names_and_current_catalog_cap(self):
        await self.app.state.catalog.get()
        await self.db.products_api.update_one({"pid": 1}, {"$set": {"variations.a.in_stock": False}})
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        await self.db.products_api.update_one({"pid": 1}, {"$set": {"variations.a.in_stock": True}})
        self.assertEqual((await self.call("submit", pids=self.pids[:9] + ["499"]))[0], 409)
        await self.db.products_api.update_one({"pid": 1}, {"$set": {"name": None}})
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        self.assertEqual((await self.selection())["status"], "pending")
        self.assertNotIn("recommended_products", await self.stored_order())

    async def test_existing_recommendations_even_null_or_same_names_are_immutable(self):
        for value in (None, [], [row["name"] for row in self.rows[:10]]):
            await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"recommended_products": value}})
            before = await self.stored_order()
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertEqual(await self.stored_order(), before)
        self.assertEqual((await self.selection())["status"], "pending")

    async def test_real_atomic_concurrency_different_and_identical_requests(self):
        other = [str(index) for index in range(11, 21)]
        results = await asyncio.gather(self.call("submit", pids=self.pids), self.call("submit", pids=other))
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winning = (await self.selection())["selected_pids"]
        results = await asyncio.gather(*(self.call("submit", pids=winning) for _ in range(8)))
        self.assertTrue(all(result[0] == 200 for result in results))
        self.assertEqual(await self.db[worker.SELECTIONS].count_documents({}), 1)
        self.assertEqual((await self.stored_order())[service.ORDER_MARKER], (await self.selection())["submission_id"])

    async def test_real_disconnect_after_order_write_then_context_recovers(self):
        original = AsyncCollection.update_one
        async def disconnect_after_write(collection, *args, **kwargs):
            result = await original(collection, *args, **kwargs)
            if collection.database.name == self.database_name and collection.name == self.config.orders_collection:
                await self.client.close()
                raise AutoReconnect("simulated lost acknowledgment")
            return result
        with patch.object(AsyncCollection, "update_one", disconnect_after_write):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        self.client = self.new_client()
        self.db = self.client[self.database_name]
        self.app.state.db = self.db
        saved = await self.stored_order()
        self.assertIn("recommended_products", saved)
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")
        self.assertEqual(await self.stored_order(), saved)

    async def test_selection_finalize_failure_and_crash_before_order_write(self):
        original = AsyncCollection.find_one_and_update
        async def fail_final(collection, query, update, **kwargs):
            if collection.database.name == self.database_name and update["$set"].get("status") == "submitted":
                raise AutoReconnect("simulated final write error")
            return await original(collection, query, update, **kwargs)
        with patch.object(AsyncCollection, "find_one_and_update", fail_final):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        self.assertEqual((await self.selection())["status"], "submitting")
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")

    async def test_crash_before_order_write_resumes_and_rejects_replacement(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        intent = await self.selection()
        self.assertNotIn("recommended_products", await self.stored_order())
        self.assertEqual((await self.call("submit", pids=list(reversed(self.pids))))[0], 409)
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")
        self.assertEqual((await self.selection())["submission_id"], intent["submission_id"])

    async def test_historical_order_and_configuration_collection_boundary(self):
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"date_created": "2026-10-13T23:59:59.999999Z"}})
        self.assertEqual((await self.call())[0], 404)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 404)
        self.assertNotIn("recommended_products", await self.stored_order())
        self.app.state.selection_settings = replace(self.config, orders_collection="different_orders")
        await self.db.different_orders.insert_one(self.source_order)
        self.assertEqual((await self.call())[0], 200)
