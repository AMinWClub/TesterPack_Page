"""Mock order flow, recovery, privacy and the unchanged worker token contract."""

import asyncio
import copy
import hashlib
import json
import logging
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from pymongo.errors import AutoReconnect

from app import selections as service
from app.selection_security import RedactSelectionAccessLog, SelectionSecurity
from app.workers import tester_sms as worker
from tests.test_tester_sms_worker import FakeCollection, FakeDatabase, GENDER_CASES, SMS_TEMPLATE, SimulatedCrash, matches


class SelectionCollection(FakeCollection):
    async def find_one_and_update(self, query, update, **kwargs):
        await asyncio.sleep(0)
        # Mongo's direct array equality compares the whole array in order.
        arrays = {key: value for key, value in query.items() if isinstance(value, list)}
        remaining = {key: value for key, value in query.items() if key not in arrays}
        for document in self.documents:
            if matches(document, remaining) and all(document.get(key) == value for key, value in arrays.items()):
                self.db.writes += 1
                self.apply_update(document, update)
                return copy.deepcopy(document)
        return None


def settings():
    return worker.WorkerSettings("preprocess_orders",
                                 selection_base_url="https://tester.example.test", token_secret="test-secret-" + "x" * 40,
                                 sms_template=SMS_TEMPLATE)


def order(order_id=None, **changes):
    return {"_id": order_id or ObjectId(), "id": "OR0000087510", "first_name": "  احمد ", "last_name": " فرجی  ",
            "phone": "09123456789", "state": "pending", "tester_gender": "female", "date_created": "2026-10-14T00:00:00Z",
            "address": "must-not-leak", "payment_method": "private", "tracking_code": "private", **changes}


def products():
    return [{"pid": index, "name": f"  Original product {index} | English {index}  ", "vol_idx": index,
             "variations": {"a": {"in_stock": True}}} for index in range(1, 21)]


def make_app(db, config, rate=60):
    app = FastAPI()
    app.include_router(service.router)
    app.add_middleware(SelectionSecurity, requests_per_minute=rate)
    app.state.db, app.state.selection_settings = db, config
    return app


async def request(app, path, body=None, *, method="POST", raw=None, client="test"):
    events = []
    payload = raw if raw is not None else json.dumps(body).encode()
    received = False
    response_done = asyncio.Event()

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(event):
        events.append(event)
        if event["type"] == "http.response.body" and not event.get("more_body", False):
            response_done.set()

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http", "method": method,
               "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
               "server": ("test", 80), "client": (client, 1234)}, receive, send)
    start = next(event for event in events if event["type"] == "http.response.start")
    content = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    headers = dict((key.decode(), value.decode()) for key, value in start["headers"])
    return start["status"], json.loads(content) if content.startswith(b"{") else content, headers


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = settings()
        self.source_order = order()
        self.db = FakeDatabase([self.source_order])
        link = await worker.ensure_selection(self.db[worker.SELECTIONS], self.source_order["_id"], self.config)
        self.token = link.rsplit("/", 1)[1]
        self.db.collections[worker.SELECTIONS] = SelectionCollection(self.db, self.db[worker.SELECTIONS].documents)
        self.db[worker.SELECTIONS].documents[0]["_id"] = ObjectId()
        self.db[worker.NOTIFICATIONS].documents.append({
            "order_id": self.source_order["_id"], "status": "accepted",
            "accepted_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        })
        self.app = make_app(self.db, self.config)
        self.rows = products()
        patcher = patch("app.selections.eligible_products", new_callable=AsyncMock, return_value=self.rows)
        self.fresh_catalog = patcher.start()
        self.addCleanup(patcher.stop)
        blocker = patch("socket.create_connection", side_effect=AssertionError("Unit tests must not use the network"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.pids = [str(index) for index in range(1, 11)]

    async def call(self, action="context", **extra):
        return await request(self.app, f"/api/tester/selection/{action}", {"token": self.token, **extra})

    @property
    def stored_order(self):
        return self.db["preprocess_orders"].documents[0]

    @property
    def selection(self):
        return self.db[worker.SELECTIONS].documents[0]

    def freeze_window(self):
        self.clock = datetime(2026, 10, 17, 10, tzinfo=timezone.utc)
        self.accepted = self.clock
        self.db[worker.NOTIFICATIONS].documents[0]["accepted_at"] = self.accepted
        patcher = patch("tests.test_tester_sms_worker.mongo_now", side_effect=lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_sms_shipping_exclusion_does_not_change_link_or_submission(self):
        for shipping in ("تحویل حضوری", "ارسال فوری"):
            self.stored_order["shipping_method"] = shipping
            status, body, _ = await self.call()
            self.assertEqual((status, body["selection"]["availability"]), (200, "active"))
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        saved = copy.deepcopy(self.stored_order["recommended_products"])
        self.stored_order["shipping_method"] = "تحویل حضوری"
        status, body, _ = await self.call()
        self.assertEqual((status, body["selection"]["status"]), (200, "submitted"))
        self.assertEqual(self.stored_order["recommended_products"], saved)

    async def test_receipt_is_readonly_after_release_and_production_state_changes(self):
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        for state in ("not_seen", "production"):
            self.stored_order["state"] = state
            before = copy.deepcopy(self.stored_order)
            status, result, _ = await self.call()
            self.assertEqual((status, result["selection"]["status"]), (200, "submitted"))
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
            self.assertEqual((await self.call("submit", pids=list(reversed(self.pids))))[0], 409)
            self.assertEqual(self.stored_order, before)

    async def test_expired_link_remains_readable_after_release_without_new_submit(self):
        self.freeze_window()
        self.clock += timedelta(minutes=31)
        self.stored_order["state"] = "not_seen"
        before = copy.deepcopy((self.stored_order, self.selection))
        status, result, _ = await self.call()
        self.assertEqual((status, result["selection"]["availability"]), (200, "expired"))
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 410)
        self.assertEqual((self.stored_order, self.selection), before)

    async def test_state_change_during_catalog_validation_prevents_order_write(self):
        async def changed_state(*args, **kwargs):
            self.stored_order["state"] = "production"
            return self.rows
        self.fresh_catalog.side_effect = changed_state
        status, result, _ = await self.call("submit", pids=self.pids)
        self.assertEqual((status, result["detail"]["code"]), (409, "order_not_pending"))
        self.assertNotIn("recommended_products", self.stored_order)
        self.assertNotIn(service.ORDER_MARKER, self.stored_order)

    async def test_pending_guard_rechecked_in_atomic_order_write(self):
        original = self.db["preprocess_orders"].update_one
        async def leave_pending(*args, **kwargs):
            self.stored_order["state"] = "not_seen"
            return await original(*args, **kwargs)
        with patch.object(self.db["preprocess_orders"], "update_one", side_effect=leave_pending):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
        self.assertNotIn("recommended_products", self.stored_order)

    async def test_window_starts_at_acceptance_and_allows_29_minutes_59_seconds(self):
        self.freeze_window()
        self.selection["created_at"] = self.accepted - timedelta(days=20)
        first = (await self.call())[1]["selection"]
        self.assertEqual(first["availability"], "active")
        self.assertEqual(first["remaining_seconds"], 1800)
        self.assertEqual(first["expires_at"], (self.accepted + timedelta(minutes=30)).isoformat())
        self.clock += timedelta(minutes=29, seconds=59)
        self.assertEqual((await self.call())[1]["selection"]["remaining_seconds"], 1)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.assertEqual(self.stored_order[service.ORDER_WRITTEN_AT], self.clock)

    async def test_configured_window_drives_context_submit_and_release_without_intent(self):
        self.freeze_window()
        self.config = replace(self.config, selection_window_minutes=7)
        self.app.state.selection_settings = self.config
        initial = (await self.call())[1]["selection"]
        self.assertEqual(initial["remaining_seconds"], 7 * 60)
        self.assertEqual(initial["expires_at"], (self.accepted + timedelta(minutes=7)).isoformat())
        await worker.create_indexes(worker.worker_collections(self.db, self.config))
        self.clock = self.accepted + timedelta(minutes=7)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 410)
        result = await worker.release_resolved_tester_orders(worker.worker_collections(self.db, self.config), self.config)
        self.assertEqual(result["released_expired"], 1)
        self.assertEqual(self.stored_order["state"], "not_seen")

    async def test_frozen_intent_deadline_survives_both_setting_increases_and_decreases(self):
        self.freeze_window()
        self.config = replace(self.config, selection_window_minutes=7)
        self.app.state.selection_settings = self.config
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        frozen = self.selection["expires_at"]
        self.assertEqual(frozen, self.accepted + timedelta(minutes=7))
        self.clock = self.accepted + timedelta(minutes=8)
        for minutes in (60, 2):
            self.config = replace(self.config, selection_window_minutes=minutes)
            self.app.state.selection_settings = self.config
            window = await service.link_window(worker.worker_collections(self.db, self.config),
                                               {**self.selection, "_server_now": self.clock}, self.config)
            self.assertEqual(window["expires_at"], frozen)
        self.assertEqual((await self.call())[1]["selection"]["availability"], "review_required")
        self.assertNotIn("recommended_products", self.stored_order)
        await worker.create_indexes(worker.worker_collections(self.db, self.config))
        result = await worker.release_resolved_tester_orders(worker.worker_collections(self.db, self.config), self.config)
        self.assertEqual(result["released_expired"], 1)

    async def test_on_time_receipt_recovers_after_configuration_shortens_window(self):
        self.freeze_window()
        self.clock += timedelta(minutes=20)
        original = self.db[worker.SELECTIONS].find_one_and_update
        async def fail_final(query, update, **kwargs):
            if update.get("$set", {}).get("status") == "submitted":
                raise AutoReconnect("synthetic receipt failure")
            return await original(query, update, **kwargs)
        with patch.object(self.db[worker.SELECTIONS], "find_one_and_update", side_effect=fail_final):
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        self.config = replace(self.config, selection_window_minutes=5)
        self.app.state.selection_settings = self.config
        self.clock += timedelta(hours=1)
        await worker.create_indexes(worker.worker_collections(self.db, self.config))
        result = await worker.release_resolved_tester_orders(worker.worker_collections(self.db, self.config), self.config)
        self.assertEqual(result["released_submitted"], 1)
        self.assertEqual(self.stored_order["tester_gender"], "تستر انتخابی")
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")

    async def test_exact_deadline_and_later_reject_direct_submit_without_changes(self):
        self.freeze_window()
        for elapsed in (timedelta(minutes=30), timedelta(hours=4)):
            self.clock = self.accepted + elapsed
            before_order, before_selection = copy.deepcopy(self.stored_order), copy.deepcopy(self.selection)
            status, data, _ = await self.call()
            self.assertEqual(status, 200)
            self.assertEqual(data["selection"]["availability"], "expired")
            self.assertEqual(data["selection"]["code"], "selection_expired")
            status, data, _ = await self.call("submit", pids=self.pids)
            self.assertEqual(status, 410)
            self.assertEqual(data["detail"]["code"], "selection_expired")
            self.assertEqual(self.stored_order, before_order)
            self.assertEqual(self.selection, before_selection)

    async def test_notification_status_and_invalid_acceptance_disable_selection(self):
        self.freeze_window()
        notification = self.db[worker.NOTIFICATIONS].documents[0]
        for status, accepted in (("prepared", self.accepted), ("sending", self.accepted),
                                 ("retry_pending", self.accepted), ("accepted", None),
                                 ("accepted", "2026-10-17T10:00:00Z"), ("accepted", self.accepted + timedelta(minutes=1))):
            notification.update(status=status, accepted_at=accepted)
            result = (await self.call())[1]["selection"]
            self.assertEqual(result["availability"], "awaiting_sms")
            self.assertIsNone(result["expires_at"])
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertNotIn("recommended_products", self.stored_order)
        self.db[worker.NOTIFICATIONS].documents.clear()
        self.assertEqual((await self.call())[1]["selection"]["availability"], "awaiting_sms")

    async def test_naive_bson_accepted_at_is_utc(self):
        self.freeze_window()
        self.db[worker.NOTIFICATIONS].documents[0]["accepted_at"] = self.accepted.replace(tzinfo=None)
        self.assertEqual((await self.call())[1]["selection"]["remaining_seconds"], 1800)

    async def test_expiration_during_catalog_read_cannot_create_intent(self):
        self.freeze_window()
        async def slow_catalog(db):
            self.clock += timedelta(minutes=30)
            return self.rows
        self.fresh_catalog.side_effect = slow_catalog
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 410)
        self.assertEqual(self.selection["status"], "pending")
        self.assertNotIn("selected_pids", self.selection)
        self.assertNotIn("recommended_products", self.stored_order)

    async def test_expiration_between_intent_and_order_write_blocks_write_and_flags_review(self):
        self.freeze_window()
        orders = self.db["preprocess_orders"]
        original = orders.update_one
        async def expire_before_write(query, update, **kwargs):
            if isinstance(update, list):
                self.clock = self.accepted + timedelta(minutes=30)
            return await original(query, update, **kwargs)
        orders.update_one = expire_before_write
        status, result, _ = await self.call("submit", pids=self.pids)
        self.assertEqual(status, 409)
        self.assertEqual(result["detail"]["code"], "selection_review_required")
        self.assertEqual(self.selection["status"], "review_required")
        self.assertEqual(self.selection["review_reason"], "deadline_before_order_write")
        self.assertNotIn("recommended_products", self.stored_order)
        self.assertEqual((await self.call())[1]["selection"]["availability"], "review_required")

    async def test_crashed_intent_may_not_write_order_after_expiry(self):
        self.freeze_window()
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        intent = copy.deepcopy(self.selection)
        self.clock += timedelta(minutes=30)
        status, result, _ = await self.call()
        self.assertEqual(status, 200)
        self.assertEqual(result["selection"]["availability"], "review_required")
        self.assertTrue(result["selection"]["requires_support"])
        self.assertEqual(self.selection["selected_pids"], intent["selected_pids"])
        self.assertEqual(self.selection["submission_id"], intent["submission_id"])
        self.assertNotIn("recommended_products", self.stored_order)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)

    async def test_on_time_order_write_recovers_receipt_after_deadline(self):
        self.freeze_window()
        collection = self.db[worker.SELECTIONS]
        original = collection.find_one_and_update
        async def crash_final(query, update, **kwargs):
            if update["$set"]["status"] == "submitted":
                raise AutoReconnect("lost final acknowledgment")
            return await original(query, update, **kwargs)
        collection.find_one_and_update = crash_final
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        before = copy.deepcopy(self.stored_order)
        self.clock += timedelta(hours=1)
        collection.find_one_and_update = original
        result = (await self.call())[1]
        self.assertEqual(result["selection"]["status"], "submitted")
        self.assertEqual(self.stored_order, before)
        self.assertEqual((await self.call("submit", pids=self.pids))[1], result)
        self.db[worker.NOTIFICATIONS].documents.clear()
        self.assertEqual((await self.call())[1], result)

    async def test_concurrent_late_requests_cannot_create_choices(self):
        self.freeze_window()
        async def expired_catalog(db):
            self.clock = self.accepted + timedelta(minutes=30)
            await asyncio.sleep(0)
            return self.rows
        self.fresh_catalog.side_effect = expired_catalog
        results = await asyncio.gather(*(self.call("submit", pids=self.pids) for _ in range(5)))
        self.assertTrue(all(result[0] == 410 for result in results))
        self.assertEqual(self.selection["status"], "pending")
        self.assertNotIn("recommended_products", self.stored_order)

    async def test_client_cannot_set_deadline_or_accepted_timestamp(self):
        self.freeze_window()
        for key in ("expires_at", "accepted_at", "remaining_seconds", "status"):
            self.assertEqual((await self.call("submit", pids=self.pids, **{key: "2099-01-01"}))[0], 400)
        self.assertNotIn("expires_at", self.selection)

    async def test_unverified_legacy_order_write_is_preserved_for_support(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        self.stored_order.update(recommended_products=self.selection["selected_names"],
                                 **{service.ORDER_MARKER: self.selection["submission_id"]})
        before = copy.deepcopy(self.stored_order)
        result = (await self.call())[1]["selection"]
        self.assertEqual(result["availability"], "review_required")
        self.assertEqual(self.selection["review_reason"], "unverified_order_write_time")
        self.assertEqual(self.stored_order, before)

    async def test_legacy_intent_without_a_valid_start_time_cannot_write_order(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        self.selection.pop("submitting_at")
        result = (await self.call())[1]["selection"]
        self.assertEqual(result["availability"], "review_required")
        self.assertEqual(self.selection["review_reason"], "unverified_intent_time")
        self.assertNotIn("recommended_products", self.stored_order)

    async def test_review_required_overrides_expired_and_missing_sms_timing_without_order_writes(self):
        self.freeze_window()
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        self.selection.update(status="review_required", review_reason="unverified_order_write_time")
        # A pre-existing order write without a provable timestamp must not promise a default pack.
        self.stored_order.update(recommended_products=self.selection["selected_names"],
                                 **{service.ORDER_MARKER: self.selection["submission_id"]})
        before_order, before_selection = copy.deepcopy(self.stored_order), copy.deepcopy(self.selection)
        for accepted_at in (self.accepted, None, "invalid"):
            self.clock = self.accepted + timedelta(hours=1)
            self.db[worker.NOTIFICATIONS].documents[0]["accepted_at"] = accepted_at
            result = (await self.call())[1]["selection"]
            self.assertEqual((result["status"], result["availability"], result["code"]),
                             ("review_required", "review_required", "selection_review_required"))
            status, body, _ = await self.call("submit", pids=self.pids)
            self.assertEqual(status, 409)
            self.assertEqual(body["detail"]["code"], "selection_review_required")
            self.assertEqual(self.stored_order, before_order)
            self.assertEqual(self.selection, before_selection)

    async def test_matched_but_expired_pipeline_is_noop_and_never_reported_as_success(self):
        self.freeze_window()
        orders = self.db["preprocess_orders"]
        original = orders.update_one
        results = []
        async def stale_match(query, update, **kwargs):
            if isinstance(update, list):
                self.clock = self.accepted + timedelta(minutes=30)
                # Simulate an outer match that passed: the pipeline must independently enforce the deadline.
                query = {**query, "$expr": query["$expr"]["$and"][0]}
            result = await original(query, update, **kwargs)
            results.append(result)
            return result
        orders.update_one = stale_match
        before = copy.deepcopy(self.stored_order)
        status, body, _ = await self.call("submit", pids=self.pids)
        self.assertEqual((results[0].matched_count, results[0].modified_count), (1, 0))
        self.assertEqual((status, body["detail"]["code"]), (409, "selection_review_required"))
        self.assertEqual(self.stored_order, before)
        self.assertFalse(any(key in self.stored_order for key in ("recommended_products", service.ORDER_MARKER, service.ORDER_WRITTEN_AT)))

    async def test_pipeline_keeps_source_names_with_dollar_signs_literal(self):
        for index, product in enumerate(self.rows):
            product["name"] = "$name" if index == 0 else f"$$source-name-{index}"
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.assertEqual(self.stored_order["recommended_products"], [row["name"] for row in self.rows[:10]])

    async def test_valid_context_full_phone_trimmed_name_display_id_and_privacy(self):
        self.stored_order["sms_sent"] = True
        status, body, headers = await self.call()
        self.assertEqual(status, 200)
        self.assertEqual(body["order"], {"id": "OR0000087510", "customer_name": "احمد فرجی", "phone": "09123456789", "tester_count": 10})
        self.assertEqual(body["selection"]["status"], "pending")
        self.assertEqual(body["selection"]["selected_pids"], [])
        self.assertEqual(body["selection"]["selected_names"], [])
        self.assertEqual(body["selection"]["availability"], "active")
        for private in ("address", "payment_method", "tracking_code", "token_hash", str(self.source_order["_id"])):
            self.assertNotIn(private, json.dumps(body))
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertIn("noindex", headers["x-robots-tag"])

    async def test_invalid_format_missing_selection_and_wrong_version(self):
        for token, expected in (("wrong", 400), ("0" * 18, 404), ("A" * 24, 400), ("1" * 17, 400),
                                ("1" * 19, 400), ("۱" * 18, 400), ("١" * 18, 400),
                                (123456789012345678, 400), (True, 400), (None, 400), ({"$ne": None}, 400)):
            status, body, _ = await request(self.app, "/api/tester/selection/context", {"token": token})
            self.assertEqual(status, expected)
            self.assertNotIn("order", body)
        self.selection["token_version"] = worker.TOKEN_VERSION + 1
        self.assertEqual((await self.call())[0], 404)

    def test_token_hash_preserves_exact_text_and_rejects_whitespace_and_non_urlsafe_characters(self):
        token = "000000000000000001"
        self.assertEqual(service.validate_token(token), hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertNotEqual(service.validate_token(token), hashlib.sha256(b"1").hexdigest())
        for invalid in (" " + token, token + " ", token + "\n", "+" + token, token[:9] + "." + token[10:],
                        "+" * 18, "/" * 18, "=" * 18):
            with self.assertRaises(HTTPException) as caught:
                service.validate_token(invalid)
            self.assertEqual(caught.exception.status_code, 400)

    async def test_case_sensitive_token_hash_and_order_access(self):
        self.token = "aB7xQ2mN9pR4tY6kL8"
        changed_case = "AB7xQ2mN9pR4tY6kL8"
        self.selection["token_hash"] = service.validate_token(self.token)
        self.assertNotEqual(service.validate_token(self.token), service.validate_token(changed_case))
        self.assertEqual((await self.call())[0], 200)
        for action, extra in (("context", {}), ("submit", {"pids": self.pids})):
            status, body, _ = await request(self.app, f"/api/tester/selection/{action}", {"token": changed_case, **extra})
            self.assertEqual(status, 404)
            self.assertNotIn("order", body)
        self.assertEqual(self.selection["status"], "pending")
        self.assertNotIn("recommended_products", self.stored_order)

    async def test_nonpending_or_missing_order_cannot_start_selection(self):
        for state in ("not_seen", "production", None, ["pending"], "Pending", "pending "):
            self.stored_order["state"] = state
            before = copy.deepcopy(self.stored_order)
            self.assertEqual((await self.call())[0], 409)
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertEqual(self.stored_order, before)
        self.db["preprocess_orders"].documents.clear()
        self.assertEqual((await self.call())[0], 404)

    async def test_context_and_submit_ignore_all_gender_types_and_old_date(self):
        original_order = copy.deepcopy(self.stored_order)
        original_order.pop("tester_gender")
        original_selection = copy.deepcopy(self.selection)
        for name, fields, eligible in GENDER_CASES:
            with self.subTest(name=name):
                self.db["preprocess_orders"].documents = [{**copy.deepcopy(original_order), **fields,
                                                         "product_id": ["unrelated"], "sms_sent": True, "date_created": "2001"}]
                self.db[worker.SELECTIONS].documents = [copy.deepcopy(original_selection)]
                self.db.writes = 0
                expected = 200
                self.assertEqual((await self.call())[0], expected)
                self.assertEqual((await self.call("submit", pids=self.pids))[0], expected)
                self.assertEqual(len(self.stored_order["recommended_products"]), 10)

    async def test_missing_or_arbitrary_purchased_product_does_not_restrict_link(self):
        for value in (None, [], "other", [123], {"unexpected": "shape"}):
            self.stored_order["product_id"] = value
            self.assertEqual((await self.call())[0], 200)
        self.stored_order.pop("product_id")
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)

    async def test_exact_ten_names_order_and_only_required_order_fields_change(self):
        original = copy.deepcopy(self.stored_order)
        pids = list(reversed(self.pids))
        status, body, _ = await self.call("submit", pids=pids)
        self.assertEqual(status, 200)
        expected = [self.rows[int(pid)-1]["name"] for pid in pids]
        self.assertEqual(self.stored_order["recommended_products"], expected)
        self.assertEqual(body["selection"]["selected_names"], expected)
        self.assertEqual(body["selection"]["selected_pids"], pids)
        self.assertEqual(self.selection["status"], "submitted")
        self.assertIsInstance(self.selection["order_id"], ObjectId)
        self.assertEqual({key: value for key, value in self.stored_order.items() if key not in {"recommended_products", service.ORDER_MARKER, service.ORDER_WRITTEN_AT}}, original)
        self.assertEqual(self.stored_order[service.ORDER_MARKER], self.selection["submission_id"])
        self.fresh_catalog.assert_awaited_once_with(self.db)

    async def test_count_duplicates_and_nonstring_pids_rejected_without_writes(self):
        for pids in (self.pids[:9], self.pids + ["11"], self.pids[:9]+["1"], list(range(10)), "123", None):
            self.assertEqual((await self.call("submit", pids=pids))[0], 422)
            self.assertNotIn("recommended_products", self.stored_order)
            self.assertEqual(self.selection["status"], "pending")

    async def test_missing_products_or_names_never_create_intent(self):
        for rows in (self.rows[1:], [{**row, "name": None} for row in self.rows], self.rows + [self.rows[0]]):
            self.fresh_catalog.return_value = rows
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertNotIn("recommended_products", self.stored_order)
            self.assertEqual(self.selection["status"], "pending")

    async def test_any_existing_field_including_null_and_identical_names_is_never_overwritten(self):
        for value in (None, [], ["old"], [row["name"] for row in self.rows[:10]]):
            self.stored_order["recommended_products"] = value
            # The worker fake conflates missing and null; the explicit Python guard
            # and real integration tests both check that null is also protected.
            self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)
            self.assertEqual((await self.call())[0], 409)
            self.assertEqual(self.stored_order["recommended_products"], value)

    async def test_same_request_is_idempotent_different_request_conflicts(self):
        first = await self.call("submit", pids=self.pids)
        self.assertEqual(first[0], 200)
        saved = copy.deepcopy(self.selection)
        self.assertEqual((await self.call("submit", pids=self.pids))[1], first[1])
        self.assertEqual((await self.call("submit", pids=list(reversed(self.pids))))[0], 409)
        self.assertEqual(self.selection, saved)
        self.fresh_catalog.assert_awaited_once()

    async def test_two_different_requests_only_one_wins(self):
        other = [str(index) for index in range(11, 21)]
        results = await asyncio.gather(self.call("submit", pids=self.pids), self.call("submit", pids=other))
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winner = next(result[1] for result in results if result[0] == 200)
        self.assertEqual(self.stored_order["recommended_products"], winner["selection"]["selected_names"])

    async def test_same_request_concurrently_succeeds_without_replacing_names(self):
        results = await asyncio.gather(*(self.call("submit", pids=self.pids) for _ in range(4)))
        self.assertTrue(all(result[0] == 200 for result in results))
        self.assertTrue(all(result[1] == results[0][1] for result in results))

    async def test_recover_when_final_selection_write_fails(self):
        collection = self.db[worker.SELECTIONS]
        original = collection.find_one_and_update
        async def fail_final(query, update, **kwargs):
            if update["$set"]["status"] == "submitted":
                raise AutoReconnect("must-not-leak")
            return await original(query, update, **kwargs)
        collection.find_one_and_update = fail_final
        status, body, _ = await self.call("submit", pids=self.pids)
        self.assertEqual(status, 503)
        self.assertNotIn("must-not-leak", str(body))
        self.assertIn("recommended_products", self.stored_order)
        saved = copy.deepcopy(self.stored_order)
        collection.find_one_and_update = original
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")
        self.assertEqual(self.stored_order, saved)

    async def test_order_write_accepted_but_response_lost_recovery(self):
        collection = self.db["preprocess_orders"]
        original = collection.update_one
        async def write_then_disconnect(*args, **kwargs):
            await original(*args, **kwargs)
            raise AutoReconnect("simulated lost write acknowledgment")
        collection.update_one = write_then_disconnect
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        collection.update_one = original
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)

    async def test_crash_before_order_write_resumes_same_intent(self):
        with patch("app.selections.finish_intent", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                await service.submit(self.db, self.config, self.token, self.pids)
        self.assertEqual(self.selection["status"], "submitting")
        self.assertNotIn("recommended_products", self.stored_order)
        self.assertEqual((await self.call("submit", pids=list(reversed(self.pids))))[0], 409)
        self.assertEqual((await self.call())[1]["selection"]["status"], "submitted")

    async def test_database_failure_before_intent_recovers_to_pending(self):
        self.fresh_catalog.side_effect = AutoReconnect("private-db-host")
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 503)
        self.assertEqual(self.selection["status"], "pending")
        self.fresh_catalog.side_effect = None
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)

    async def test_submitted_context_uses_saved_names_even_if_stock_disappears(self):
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.fresh_catalog.side_effect = AssertionError("Submitted receipt must not depend on catalog")
        self.assertEqual((await self.call())[1]["selection"]["selected_names"], self.selection["selected_names"])

    async def test_marker_or_list_mismatch_is_not_silently_repaired(self):
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        self.stored_order[service.ORDER_MARKER] = "different"
        self.assertEqual((await self.call())[0], 409)
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 409)

    async def test_worker_ensure_selection_preserves_submitted_record_and_token(self):
        self.assertEqual((await self.call("submit", pids=self.pids))[0], 200)
        before = copy.deepcopy(self.selection)
        link = await worker.ensure_selection(self.db[worker.SELECTIONS], self.source_order["_id"], self.config)
        self.assertEqual(link.rsplit("/", 1)[1], self.token)
        self.assertEqual(self.selection, before)

    async def test_worker_link_has_one_root_segment_even_with_trailing_base_slash(self):
        from dataclasses import replace
        from urllib.parse import urlsplit
        for config in (self.config, replace(self.config, selection_base_url=self.config.selection_base_url + "/")):
            link = await worker.ensure_selection(self.db[worker.SELECTIONS], self.source_order["_id"], config)
            self.assertEqual(urlsplit(link).path, "/" + self.token)

    async def test_bad_body_oversize_and_rate_limit_are_private(self):
        for raw, expected in ((b'{bad', 400), (b'[]', 400), (b'x'*8193, 413)):
            self.assertEqual((await request(self.app, "/api/tester/selection/context", raw=raw))[0], expected)
        app = make_app(self.db, self.config, rate=1)
        self.assertEqual((await request(app, "/api/tester/selection/context", {"token": "bad"}))[0], 400)
        status, body, headers = await request(app, "/api/tester/selection/context", {"token": self.token})
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn(self.token, str(body))

    def test_access_log_redacts_token_and_query(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1", "GET", f"/{self.token}?x=secret", "1.1", 200), None)
        RedactSelectionAccessLog().filter(record)
        self.assertNotIn(self.token, record.getMessage())
        self.assertNotIn("secret", record.getMessage())
        self.assertIn("/[redacted]", record.getMessage())
