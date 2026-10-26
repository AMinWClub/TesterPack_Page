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
from tests.test_sms_faraz import FakeHttpResponse, simple_success_body
from tests.test_tester_sms_worker import GENDER_CASES, SHIPPING_CASES, SMS_TEMPLATE


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
            "preprocess_orders", sms_enabled=True,
            selection_base_url="https://tester.example.test", token_secret="integration-secret-" + "x" * 40,
            sms_template=SMS_TEMPLATE,
        )
        self.sms = FarazSmsClient(FarazSmsConfig(
            api_key="integration-test-key", base_url="https://provider.example.test", line_number="50001",
        ))
        self.sms.send_simple = Mock(wraps=self.sms.send_simple)
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
        return {"_id": order_id, "state": "pending", "tester_gender": "female", "date_created": "2026-10-13T20:30:00Z",
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

    async def test_real_shipping_is_ignored_and_no_resend(self):
        await self.collections["orders"].insert_many([
            self.order("shipping-" + name, **fields) for name, fields, _ in SHIPPING_CASES
        ])
        expected = {"order-1"} | {"shipping-" + name for name, _, eligible in SHIPPING_CASES if eligible}
        raw_matches = await self.collections["orders"].find(
            worker.sms_order_filter(self.settings), {"_id": 1},
        ).to_list(length=None)
        self.assertEqual({row["_id"] for row in raw_matches}, expected)
        self.assertEqual((await self.run_worker())["accepted"], len(expected))
        sent = await self.collections["orders"].find({"sms_sent": True}, {"_id": 1}).to_list(length=None)
        self.assertEqual({row["_id"] for row in sent}, expected)
        notifications = await self.collections["notifications"].find({}).to_list(length=None)
        self.assertEqual({row["order_id"] for row in notifications}, expected)
        await self.run_worker()
        self.assertEqual(self.http.call_count, len(expected))

    async def test_real_due_retry_waits_for_pending(self):
        self.http.side_effect = TimeoutError("synthetic timeout")
        await self.run_worker()
        await self.make_retry_due()
        before = await self.notification()
        self.http.side_effect = None
        for state in ("production", "not_seen"):
            await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": state}})
            self.assertEqual((await self.run_worker())["eligible"], 0)
            self.assertEqual(await self.notification(), before)
        self.http.assert_called_once()
        await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": "pending"}})
        await self.run_worker()
        self.assertEqual(((await self.notification())["status"], (await self.notification())["attempt_count"]), ("accepted", 2))
        self.assertEqual(self.http.call_args_list[0].args[0].data, self.http.call_args_list[1].args[0].data)

    async def test_real_state_change_after_query_is_rechecked(self):
        original = worker.process_order
        async def change_shipping(*args, **kwargs):
            await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": "production"}})
            return await original(*args, **kwargs)
        with patch.object(worker, "process_order", side_effect=change_shipping):
            summary = await self.run_worker()
        self.assertEqual((summary["eligible"], summary["skipped"]), (1, 1))
        self.assertEqual(await self.collections["notifications"].count_documents({}), 0)
        self.http.assert_not_called()

    async def test_real_state_change_while_preparing_selection_does_not_claim(self):
        original = worker.ensure_selection
        async def change_shipping(*args, **kwargs):
            link = await original(*args, **kwargs)
            await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": "not_seen"}})
            return link
        with patch.object(worker, "ensure_selection", side_effect=change_shipping):
            self.assertEqual((await self.run_worker())["skipped"], 1)
        notification = await self.notification()
        self.assertEqual((notification["status"], notification["attempt_count"]), ("prepared", 0))
        self.http.assert_not_called()
        saved = await self.collections["selections"].find_one({"order_id": "order-1"})
        await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": "pending"}})
        await self.run_worker()
        self.assertEqual(await self.collections["selections"].find_one({"order_id": "order-1"}), saved)
        self.assertEqual((await self.notification())["attempt_count"], 1)

    async def test_object_acceptance_persists_only_numeric_id_and_never_resends(self):
        body = simple_success_body()
        self.http.return_value = FakeHttpResponse(200, json.dumps(body).encode())
        before = datetime.now(timezone.utc)
        self.assertEqual((await self.run_worker())["accepted"], 1)
        after = datetime.now(timezone.utc)
        notification = await self.notification()
        self.assertEqual((notification["status"], notification["response_status_code"], notification["provider_data"]),
                         ("accepted", 200, 15870343))
        self.assertIs(type(notification["provider_data"]), int)
        self.assertTrue(notification["order_synced"])
        self.assertTrue((await self.collections["orders"].find_one({"_id": "order-1"}))["sms_sent"])
        # BSON dates have millisecond precision; allow only the truncated lower bound.
        self.assertLessEqual(before.replace(microsecond=before.microsecond // 1000 * 1000), notification["accepted_at"])
        self.assertLessEqual(notification["accepted_at"], after)
        for key in ("error_code", "last_error_code", "next_retry_at"):
            self.assertIsNone(notification[key])
        self.assertEqual(notification["attempt_count"], 1)
        self.assertEqual([row["result"] for row in notification["attempts"]], ["accepted"])
        saved = repr(notification) + repr(await self.collections["selections"].find_one({"order_id": "order-1"}))
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        for private in ("metadata", body["data"]["schedule"], "private-sms-text", "09120000000", link,
                        link.rsplit("/", 1)[1], "aB7xQ2mN9pR4tY6kL8"):
            self.assertNotIn(private, saved)
        await self.run_worker()
        self.assertEqual(await self.notification(), notification)
        self.http.assert_called_once()

    async def test_only_pending_eligibility_ignores_all_legacy_order_fields(self):
        fixtures = [
            self.order("old", date_created="2001", tester_gender=""),
            self.order("pickup", shipping_method="تحویل حضوری"), self.order("express", shipping_method="ارسال فوری"),
            {"_id": "minimal", "state": "pending", "phone": "09120000000"},
            self.order("sent", sms_sent=True),
        ]
        for index, state in enumerate((None, "production", "not_seen", ["pending"], "Pending", "pending ")):
            fixtures.append(self.order(f"excluded-{index}", state=state))
        fixtures.append({"_id": "missing", "phone": "09120000000"})
        await self.collections["orders"].insert_many(fixtures)
        self.assertEqual((await self.run_worker())["accepted"], 5)
        records = await self.collections["notifications"].find({}).to_list(length=None)
        self.assertEqual({row["order_id"] for row in records}, {"order-1", "old", "pickup", "express", "minimal"})
        await self.run_worker()
        self.assertEqual(self.http.call_count, 5)

    async def test_real_all_gender_values_are_eligible_without_resend(self):
        fixtures = []
        for name, fields, _ in GENDER_CASES:
            document = self.order(name, product_id=["unrelated"])
            document.pop("tester_gender")
            fixtures.append({**document, **fields})
        await self.collections["orders"].insert_many(fixtures)
        expected = {name for name, _, eligible in GENDER_CASES if eligible} | {"order-1"}
        raw_matches = await self.collections["orders"].find(
            worker.sms_order_filter(self.settings), {"_id": 1},
        ).to_list(length=None)
        self.assertEqual({row["_id"] for row in raw_matches}, expected)
        result = await self.run_worker()
        self.assertEqual(result["accepted"], len(expected))
        sent = await self.collections["orders"].find({"sms_sent": True}, {"_id": 1}).to_list(length=None)
        self.assertEqual({row["_id"] for row in sent}, expected)
        self.assertEqual(await self.collections["selections"].count_documents({}), len(expected))
        await self.run_worker()
        self.assertEqual(self.http.call_count, len(expected))

    async def test_new_index_created_without_deleting_old_index(self):
        orders = self.collections["orders"]
        await orders.create_index([("product_id", 1), ("date_created", 1), ("_id", 1)],
                                  name="tester_product_created", collation=Collation(locale="simple"))
        await orders.create_index([("date_created", 1), ("_id", 1)], name="tester_gender_created",
                                  partialFilterExpression={"tester_gender": {"$exists": True}}, collation=Collation(locale="simple"))
        old_index = (await orders.index_information())["tester_product_created"]
        old_partial = (await orders.index_information())["tester_gender_created"]
        await worker.create_indexes(self.collections)
        await worker.check_indexes(self.collections)
        indexes = await orders.index_information()
        self.assertEqual(indexes["tester_product_created"], old_index)
        self.assertEqual(indexes["tester_gender_created"], old_partial)
        self.assertEqual(indexes[worker.ORDER_INDEX]["key"], [("state", 1), ("_id", 1)])
        self.assertNotIn("partialFilterExpression", indexes[worker.ORDER_INDEX])
        await self.run_worker()
        self.http.assert_called_once()

    async def test_recovery_and_order_sync_ignore_removed_gender_scope(self):
        orders, notifications = self.collections["orders"], self.collections["notifications"]
        fixtures, records = [], []
        for status in ("submission_unknown", "accepted"):
            for name, fields in (("null", {"tester_gender": None}), ("empty", {"tester_gender": ""}), ("missing", {})):
                identifier = f"{status}-{name}"
                fixture = self.order(identifier)
                fixture.pop("tester_gender")
                fixtures.append({**fixture, **fields})
                records.append({"order_id": identifier, "status": status, "order_synced": False,
                                "updated_at": datetime.now(timezone.utc) - timedelta(minutes=10)})
        await orders.insert_many(fixtures)
        await notifications.insert_many(records)
        before = {row["order_id"]: row for row in await notifications.find({}).to_list(length=None)}
        result = await worker.recover_notifications(self.collections, worker.order_filter(self.settings), 50, self.settings)
        self.assertEqual((result["recovered_retry"], result["repaired"]), (3, 3))
        for row in await notifications.find({}).to_list(length=None):
            fixture = await orders.find_one({"_id": row["order_id"]})
            if row["order_id"].startswith("accepted-"):
                self.assertTrue(row["order_synced"])
                self.assertTrue(fixture["sms_sent"])
            elif row["order_id"].startswith("submission_unknown-"):
                self.assertEqual(row["status"], "retry_pending")
                self.assertNotIn("sms_sent", fixture)
            else:
                self.assertEqual(row, before[row["order_id"]])
                self.assertNotIn("sms_sent", fixture)
        self.http.assert_not_called()

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
        texts = [call.kwargs["text"] for call in self.sms.send_simple.call_args_list]
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        self.assertEqual(texts, [self.settings.sms_template.format(full_name="مشتری", link=link)] * 2)

    async def test_real_personalized_template_retries_identically_without_private_metadata(self):
        await self.collections["orders"].update_one({"_id": "order-1"}, {
            "$set": {"first_name": "  احمد ", "last_name": " فرجی  "},
        })
        self.http.side_effect = TimeoutError("synthetic timeout")
        await self.run_worker()
        await self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        first, second = [call.args[0].data for call in self.http.call_args_list]
        self.assertEqual(first, second)
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        self.assertEqual(json.loads(first)["text"], SMS_TEMPLATE.format(full_name="احمد فرجی", link=link))
        saved = repr(await self.notification()) + repr(await self.collections["selections"].find_one({"order_id": "order-1"}))
        for private in ("احمد", "فرجی", link, link.rsplit("/", 1)[1], json.loads(first)["text"]):
            self.assertNotIn(private, saved)
        self.assertEqual(await self.collections["selections"].count_documents({}), 1)

    async def test_real_retry_refreshes_name_and_template_while_preserving_token(self):
        from dataclasses import replace
        initial_settings = self.settings
        for change in ("name", "template", "both"):
            with self.subTest(change=change):
                self.settings = initial_settings
                await self.collections["orders"].update_one({"_id": "order-1"}, {
                    "$set": {"first_name": " احمد ", "last_name": " فرجی "}, "$unset": {"sms_sent": ""},
                })
                await self.collections["notifications"].delete_many({})
                await self.collections["selections"].delete_many({})
                self.http.reset_mock()
                self.http.side_effect = TimeoutError("synthetic timeout")
                await self.run_worker()
                selection = await self.collections["selections"].find_one({"order_id": "order-1"})
                await self.make_retry_due()
                if change in {"name", "both"}:
                    await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"first_name": " سارا ", "last_name": " احمدی "}})
                if change in {"template", "both"}:
                    with patch.dict(os.environ, {"TESTER_SMS_TEMPLATE": "{full_name} عزیز\\nانتخاب‌های شما: {link}"}), \
                            patch.object(worker, "load_dotenv"):
                        loaded = worker.WorkerSettings.from_environment()
                    self.settings = replace(self.settings, sms_template=loaded.sms_template)
                self.http.side_effect = None
                await self.run_worker()
                link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
                texts = [json.loads(call.args[0].data)["text"] for call in self.http.call_args_list]
                self.assertEqual(texts[0], SMS_TEMPLATE.format(full_name="احمد فرجی", link=link))
                expected_name = "سارا احمدی" if change in {"name", "both"} else "احمد فرجی"
                self.assertEqual(texts[1], self.settings.sms_template.format(full_name=expected_name, link=link))
                self.assertNotEqual(*texts)
                self.assertTrue(all(link in text for text in texts))
                self.assertEqual(await self.collections["selections"].find_one({"order_id": "order-1"}), selection)
                self.assertEqual(((await self.notification())["attempt_count"], (await self.notification())["status"]), (2, "accepted"))
                await self.run_worker()
                self.assertEqual(self.http.call_count, 2)

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
            return FakeHttpResponse(200, json.dumps(simple_success_body()).encode())

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
        self.http.return_value = FakeHttpResponse(200, json.dumps(simple_success_body()).encode())
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
        self.http.return_value = FakeHttpResponse(200, json.dumps(simple_success_body()).encode())
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
        accepted_at = (await self.notification())["accepted_at"]
        self.assertNotIn("sms_sent", await self.collections["orders"].find_one({"_id": "order-1"}))
        # Accepted repair remains possible even after leaving the pending queue.
        await self.collections["orders"].update_one({"_id": "order-1"}, {"$set": {"state": "production"}})
        result = await self.run_worker()
        self.assertEqual(result["repaired"], 1)
        self.assertEqual((await self.notification())["accepted_at"], accepted_at)
        self.http.assert_called_once()

    async def test_global_errors_stop_run_before_next_order(self):
        await self.collections["orders"].insert_one(self.order("order-2"))
        self.http.side_effect = HTTPError("https://provider.example.test", 429, "limited", {}, io.BytesIO(b'error'))
        result = await self.run_worker()
        self.assertEqual(result["halted_http_status"], 429)
        self.http.assert_called_once()
        self.assertEqual(await self.collections["notifications"].count_documents({}), 1)
        self.assertEqual((await self.notification())["status"], "retry_pending")

    async def test_date_precision_no_longer_filters_pending_orders(self):
        await self.collections["orders"].insert_many([
            self.order("before", date_created="2026-10-13T20:30:00.499999Z"),
            self.order("boundary", date_created="2026-10-13T20:30:00.5Z"),
            self.order("after", date_created="2026-10-13T20:30:00.500000001Z"),
        ])
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 4)
        self.assertTrue((await self.collections["orders"].find_one({"_id": "before"}))["sms_sent"])

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
            {key: value for key, value in self.order(
                f"bulk-{index:04d}",
                state="pending" if index % 100 == 0 else "production",
                date_created="2026-10-13T20:30:00.123456Z" if index % 2 == 0 else "2026-10-12T00:00:00Z",
            ).items() if key != "tester_gender"}
            for index in range(3000)
        ])
        pipeline = worker.eligible_pipeline(worker.sms_order_filter(self.settings), 10, self.settings.max_attempts)
        explained = await self.db.command({"explain": {
            "aggregate": self.settings.orders_collection, "pipeline": pipeline, "cursor": {},
            "hint": worker.ORDER_INDEX, "collation": {"locale": "simple"},
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
