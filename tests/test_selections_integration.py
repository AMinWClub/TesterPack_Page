"""Opt-in real MongoDB order flow; every write is to an owned temporary database."""

import asyncio
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import AutoReconnect

from app import selections as service
from app.catalog import CatalogCache, router as catalog_router
from app.services.sms import FarazSmsClient, FarazSmsConfig
from app.workers import tester_sms as worker
from tests.test_selections import make_app, order, products, request, settings
from tests.test_tester_sms_worker import SimulatedCrash
from tests.test_sms_faraz import FakeHttpResponse, simple_success_body


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
        await self.db[worker.NOTIFICATIONS].insert_one({
            "order_id": self.source_order["_id"], "status": "accepted",
            "accepted_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        })
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

    async def test_real_sms_shipping_exclusion_does_not_invalidate_link_or_submission(self):
        for shipping in ("تحویل حضوری", "ارسال فوری"):
            await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"shipping_method": shipping}})
            status, body, _ = await self.call()
            self.assertEqual((status, body["selection"]["availability"]), (200, "active"))
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        saved = (await self.stored_order())["recommended_products"]
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"shipping_method": "تحویل حضوری"}})
        status, body, _ = await self.call()
        self.assertEqual((status, body["selection"]["status"]), (200, "submitted"))
        self.assertEqual((await self.stored_order())["recommended_products"], saved)

    async def test_worker_to_submission_to_readonly_and_worker_regression(self):
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {
            "$set": {"date_created": "2001", "shipping_method": "تحویل حضوری"}, "$unset": {"tester_gender": ""},
        })
        await self.db[worker.NOTIFICATIONS].delete_one({"order_id": self.source_order["_id"]})
        sms = FarazSmsClient(FarazSmsConfig(api_key="test", base_url="https://provider.example.test", line_number="50001"))
        sms.send_simple = Mock(wraps=sms.send_simple)
        provider = patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(200, json.dumps(simple_success_body()).encode()))
        http = provider.start()
        self.addCleanup(provider.stop)
        before = datetime.now(timezone.utc)
        result = await worker.run_once(self.db, replace(self.config, sms_enabled=True), sms=sms)
        self.assertEqual(result["accepted"], 1)
        notification = await self.db[worker.NOTIFICATIONS].find_one({"order_id": self.source_order["_id"]})
        accepted_at = notification["accepted_at"]
        self.assertEqual(notification["provider_data"], 15870343)
        self.assertGreaterEqual(accepted_at, before.replace(microsecond=before.microsecond // 1000 * 1000))
        initial_context = (await self.call())[1]["selection"]
        self.assertEqual(service.utc_date(datetime.fromisoformat(initial_context["expires_at"])), accepted_at + timedelta(minutes=30))
        self.assertEqual(initial_context["availability"], "active")
        self.assertEqual(sms.send_simple.call_args.kwargs["text"], worker.TESTER_SMS_TEXT.format(link=self.config.selection_base_url + "/" + self.token))
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
        sms.send_simple.assert_called_once()
        http.assert_called_once()
        self.assertEqual((await self.db[worker.NOTIFICATIONS].find_one({"order_id": self.source_order["_id"]}))["accepted_at"], accepted_at)
        # Owned fixtures only: receipts must survive later stock disappearance.
        await self.db.products_api.delete_many({})
        self.assertEqual((await self.call())[1]["selection"]["selected_names"], expected)

    async def test_null_gender_allows_context_submission_and_readonly_without_purchased_product(self):
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {
            "$set": {"tester_gender": None, "sms_sent": True}, "$unset": {"product_id": ""},
        })
        self.assertEqual((await self.call())[0], 200)
        status, result, _ = await self.call("submit", pids=self.pids)
        self.assertEqual(status, 200)
        self.assertEqual(result["selection"]["status"], "submitted")
        self.assertEqual((await self.stored_order())["recommended_products"], [row["name"] for row in self.rows[:10]])
        self.assertEqual((await self.call())[1], result)
        self.assertEqual((await self.call("submit", pids=self.pids))[1], result)

    async def test_old_order_without_gender_can_submit_and_receipt_survives_release(self):
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {
            "$unset": {"tester_gender": "", "date_created": ""}, "$set": {"shipping_method": "ارسال فوری"},
        })
        self.assertEqual((await self.call())[0], 200)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        summary = await worker.release_resolved_tester_orders(worker.worker_collections(self.db, self.config), self.config)
        self.assertEqual(summary["released_submitted"], 1)
        saved = await self.stored_order()
        self.assertEqual((saved["state"], saved["tester_gender"]), ("not_seen", "تستر انتخابی"))
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.assertEqual((await self.call("submit", pids=list(reversed(self.pids))))[0], 409)
        self.assertEqual(await self.stored_order(), saved)

    async def test_real_nonpending_cannot_submit_and_expired_link_survives_release(self):
        for state in ("production", "not_seen", None, ["pending"]):
            await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"state": state}})
            before = await self.stored_order()
            self.assertEqual((await self.call())[0], 409)
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertEqual(await self.stored_order(), before)
        await self.db[worker.NOTIFICATIONS].update_one({"order_id": self.source_order["_id"]}, {
            "$set": {"accepted_at": datetime.now(timezone.utc) - timedelta(minutes=31)},
        })
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {"state": "pending"}})
        summary = await worker.release_resolved_tester_orders(worker.worker_collections(self.db, self.config), self.config)
        self.assertEqual(summary["released_expired"], 1)
        before = await self.stored_order()
        self.assertEqual((await self.call())[1]["selection"]["availability"], "expired")
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 410)
        self.assertEqual(await self.stored_order(), before)

    async def test_real_state_changed_at_order_write_does_not_store_recommendations(self):
        original = AsyncCollection.update_one
        async def leave_pending(collection, query, update, *args, **kwargs):
            if collection.database.name == self.database_name and collection.name == self.config.orders_collection and isinstance(update, list):
                await original(collection, {"_id": self.source_order["_id"]}, {"$set": {"state": "production"}})
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=leave_pending):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        saved = await self.stored_order()
        self.assertEqual(saved["state"], "production")
        self.assertNotIn("recommended_products", saved)
        self.assertNotIn(service.ORDER_MARKER, saved)

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
        self.assertEqual((await self.call())[0], 200)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.app.state.selection_settings = replace(self.config, orders_collection="different_orders")
        self.assertEqual((await self.call())[0], 404)
        # Move the actual persisted receipt, not the pre-submission fixture.
        await self.db.different_orders.insert_one(await self.stored_order())
        self.assertEqual((await self.call())[0], 200)

    async def test_case_sensitive_urlsafe_token_lookup_submission_and_readonly_context(self):
        fixture = order(ObjectId("000000000000000000000002"), id="OR-CASE-SENSITIVE")
        await self.db.preprocess_orders.insert_one(fixture)
        link = await worker.ensure_selection(self.db[worker.SELECTIONS], fixture["_id"], self.config)
        token = link.rsplit("/", 1)[1]
        await self.db[worker.NOTIFICATIONS].insert_one({"order_id": fixture["_id"], "status": "accepted",
                                                       "accepted_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        self.assertEqual(token, "C5azLUAZxMoxdlt5qn")
        changed_case = "c" + token[1:]
        self.assertEqual((await request(self.app, "/api/tester/selection/context", {"token": token}))[1]["order"]["id"], "OR-CASE-SENSITIVE")
        self.assertEqual((await request(self.app, "/api/tester/selection/context", {"token": changed_case}))[0], 404)
        self.assertEqual((await request(self.app, "/api/tester/selection/submit", {"token": changed_case, "pids": self.pids}))[0], 404)
        self.assertNotIn("recommended_products", await self.db.preprocess_orders.find_one({"_id": fixture["_id"]}))
        body = {"token": token, "pids": self.pids}
        self.assertEqual((await request(self.app, "/api/tester/selection/submit", body))[0], 200)
        self.assertEqual((await request(self.app, "/api/tester/selection/context", {"token": token}))[1]["selection"]["status"], "submitted")
        record = await self.db[worker.SELECTIONS].find_one({"order_id": fixture["_id"]})
        self.assertEqual(record["token_version"], worker.TOKEN_VERSION)
        self.assertNotIn(token, str(record))

    async def test_accepted_window_and_real_expired_context_direct_submit(self):
        notification = self.db[worker.NOTIFICATIONS]
        for status in ("prepared", "sending", "retry_pending"):
            await notification.update_one({"order_id": self.source_order["_id"]}, {"$set": {"status": status}})
            self.assertEqual((await self.call())[1]["selection"]["availability"], "awaiting_sms")
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        for value in (None, "not-a-date"):
            await notification.update_one({"order_id": self.source_order["_id"]}, {"$set": {"status": "accepted", "accepted_at": value}})
            self.assertEqual((await self.call())[1]["selection"]["availability"], "awaiting_sms")
        now = (await self.db.command("hello"))["localTime"]
        await notification.update_one({"order_id": self.source_order["_id"]}, {"$set": {"accepted_at": now - timedelta(minutes=30)}})
        before_order, before_selection = await self.stored_order(), await self.selection()
        self.assertEqual((await self.call())[1]["selection"]["availability"], "expired")
        results = await asyncio.gather(*(self.call("submit", pids=self.pids) for _ in range(4)))
        self.assertTrue(all(row[0] == 410 and row[1]["detail"]["code"] == "selection_expired" for row in results))
        self.assertEqual(await self.stored_order(), before_order)
        self.assertEqual(await self.selection(), before_selection)

    async def test_real_atomic_intent_guard_rejects_deadline_even_if_initial_check_is_stale(self):
        now = (await self.db.command("hello"))["localTime"]
        await self.db[worker.NOTIFICATIONS].update_one({"order_id": self.source_order["_id"]},
            {"$set": {"accepted_at": now - timedelta(minutes=30)}})
        with patch("app.selections.require_active"):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 410)
        self.assertEqual((await self.selection())["status"], "pending")
        self.assertNotIn("recommended_products", await self.stored_order())

    async def test_real_order_write_guard_rejects_crossed_deadline(self):
        now = (await self.db.command("hello"))["localTime"]
        await self.db[worker.NOTIFICATIONS].update_one({"order_id": self.source_order["_id"]},
            {"$set": {"accepted_at": now - timedelta(minutes=29, seconds=55)}})
        original = AsyncCollection.update_one
        reached_order_write = False
        write_results = []
        async def pause_order_write(collection, query, update, *args, **kwargs):
            nonlocal reached_order_write
            if collection.database.name == self.database_name and isinstance(update, list):
                reached_order_write = True
                deadline = query["$expr"]["$and"][-1]["$lt"][1]
                server_now = service.utc_date((await self.db.command("hello"))["localTime"])
                await asyncio.sleep(max(0, (deadline - server_now).total_seconds()) + .1)
                # Even a successful outer match cannot bypass the pipeline's own clock guard.
                query = {**query, "$expr": query["$expr"]["$and"][0]}
                result = await original(collection, query, update, *args, **kwargs)
                write_results.append((result.matched_count, result.modified_count))
                return result
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "update_one", new=pause_order_write):
            result = await self.call("submit", pids=self.pids)
        self.assertTrue(reached_order_write)
        self.assertEqual(write_results, [(1, 0)])
        self.assertEqual(result[0], 409)
        self.assertNotIn("recommended_products", await self.stored_order())
        self.assertEqual((await self.selection())["status"], "review_required")
        self.assertEqual((await self.call())[1]["selection"]["availability"], "review_required")

    async def test_real_pipeline_clock_consistency_exact_deadline_and_literal_names(self):
        orders = self.db.preprocess_orders
        intent = {"submission_id": "a" * 32, "selected_names": [f"$literal-name-{i}" for i in range(10)]}
        now = service.utc_date((await self.db.command("hello"))["localTime"])
        deadline = now + timedelta(seconds=3)
        original = await self.stored_order()
        result = await orders.update_one({"_id": original["_id"]}, service.order_write_pipeline(intent, {"expires_at": deadline}))
        self.assertEqual((result.matched_count, result.modified_count), (1, 1))
        saved = await self.stored_order()
        self.assertEqual(saved["recommended_products"], intent["selected_names"])
        self.assertEqual(saved[service.ORDER_MARKER], intent["submission_id"])
        self.assertLess(saved[service.ORDER_WRITTEN_AT], deadline)
        self.assertGreaterEqual(saved[service.ORDER_WRITTEN_AT], now)
        self.assertEqual({key: value for key, value in saved.items() if key not in
                          {"recommended_products", service.ORDER_MARKER, service.ORDER_WRITTEN_AT}}, original)
        # Equality and one millisecond late are evaluated using the actual server's NOW,
        # without a flaky client sleep or an application-clock approximation.
        for expiry in ("$$NOW", {"$subtract": ["$$NOW", 1]}):
            fixture = order()
            await orders.insert_one(fixture)
            result = await orders.update_one({"_id": fixture["_id"]}, service.order_write_pipeline(intent, {"expires_at": expiry}))
            self.assertEqual((result.matched_count, result.modified_count), (1, 0))
            self.assertEqual(await orders.find_one({"_id": fixture["_id"]}), fixture)

    async def test_review_required_remains_support_state_after_expiry_or_missing_acceptance(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        intent = await self.selection()
        await self.db.preprocess_orders.update_one({"_id": self.source_order["_id"]}, {"$set": {
            "recommended_products": intent["selected_names"], service.ORDER_MARKER: intent["submission_id"],
        }})
        await self.db[worker.SELECTIONS].update_one({"_id": intent["_id"]}, {"$set": {
            "status": "review_required", "review_reason": "unverified_order_write_time",
        }})
        before_order, before_selection = await self.stored_order(), await self.selection()
        now = service.utc_date((await self.db.command("hello"))["localTime"])
        for accepted in (now - timedelta(minutes=31), None):
            await self.db[worker.NOTIFICATIONS].update_one({"order_id": self.source_order["_id"]}, {"$set": {"accepted_at": accepted}})
            result = (await self.call())[1]["selection"]
            self.assertEqual((result["status"], result["availability"], result["code"]),
                             ("review_required", "review_required", "selection_review_required"))
            status, error, _ = await self.call("submit", pids=self.pids)
            self.assertEqual((status, error["detail"]["code"]), (409, "selection_review_required"))
            self.assertEqual(await self.stored_order(), before_order)
            self.assertEqual(await self.selection(), before_selection)

    async def test_crashed_intent_after_expiry_is_not_recovered_into_order_choices(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        expired = (await self.db.command("hello"))["localTime"] - timedelta(seconds=1)
        await self.db[worker.SELECTIONS].update_one({"order_id": self.source_order["_id"]}, {"$set": {"expires_at": expired}})
        context = (await self.call())[1]["selection"]
        self.assertEqual(context["status"], "review_required")
        self.assertTrue(context["requires_support"])
        self.assertNotIn("recommended_products", await self.stored_order())
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)

    async def test_on_time_order_write_can_finalize_after_deadline_without_rewriting_order(self):
        original = AsyncCollection.find_one_and_update
        async def fail_final(collection, query, update, *args, **kwargs):
            if collection.database.name == self.database_name and update.get("$set", {}).get("status") == "submitted":
                raise AutoReconnect("simulated finalization disconnect")
            return await original(collection, query, update, *args, **kwargs)
        with patch.object(AsyncCollection, "find_one_and_update", new=fail_final):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        saved = await self.stored_order()
        # Move the trusted fixture window into the past, keeping its original write on time.
        expired = (await self.db.command("hello"))["localTime"] - timedelta(seconds=1)
        await self.db.preprocess_orders.update_one({"_id": saved["_id"]},
            {"$set": {service.ORDER_WRITTEN_AT: expired - timedelta(seconds=1)}})
        await self.db[worker.SELECTIONS].update_one({"order_id": saved["_id"]},
            {"$set": {"expires_at": expired, "submitting_at": expired - timedelta(seconds=2)}})
        await self.db[worker.NOTIFICATIONS].update_one({"order_id": saved["_id"]},
            {"$set": {"accepted_at": expired - timedelta(minutes=30)}})
        before = await self.stored_order()
        result = await self.call()
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["selection"]["status"], "submitted")
        self.assertEqual(await self.stored_order(), before)
        self.assertEqual((await self.call("submit", pids=self.pids))[1], result[1])

    async def test_worker_rerun_does_not_extend_an_accepted_window(self):
        before = (await self.call())[1]["selection"]["expires_at"]
        notification = await self.db[worker.NOTIFICATIONS].find_one({"order_id": self.source_order["_id"]})
        sms = FarazSmsClient(FarazSmsConfig(api_key="test", line_number="50001"))
        sms.send_simple = Mock(side_effect=AssertionError("Already accepted SMS must not be sent again"))
        await worker.run_once(self.db, replace(self.config, sms_enabled=True), sms=sms)
        self.assertEqual((await self.call())[1]["selection"]["expires_at"], before)
        self.assertEqual((await self.db[worker.NOTIFICATIONS].find_one({"order_id": self.source_order["_id"]}))["accepted_at"], notification["accepted_at"])
        sms.send_simple.assert_not_called()

    async def test_unique_hash_collision_never_reassigns_another_orders_token(self):
        fixture = order()
        await self.db.preprocess_orders.insert_one(fixture)
        original = await self.selection()
        with patch.object(worker, "token_for_order", return_value=self.token):
            with self.assertRaisesRegex(worker.WorkerConfigurationError, "collision") as caught:
                await worker.ensure_selection(self.db[worker.SELECTIONS], fixture["_id"], self.config)
        self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(await self.selection(), original)
        self.assertIsNone(await self.db[worker.SELECTIONS].find_one({"order_id": fixture["_id"]}))
        self.assertEqual(await self.db[worker.SELECTIONS].count_documents({}), 1)
