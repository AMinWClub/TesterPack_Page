"""No real HTTP/MongoDB: URL selection, provider parsing and SMS regressions."""

import asyncio
import copy
import io
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from pymongo.errors import AutoReconnect

from app.services.sms import FarazSmsClient, FarazSmsConfig, FarazSmsResponse, SmsProviderError
from app.services.url_shortener import zaya
from app.workers import tester_sms as worker
from tests.test_tester_sms_worker import FakeDatabase, SMS_TEMPLATE, order

ORIGINAL = "https://tester.example.com/aB7xQ2mN9pR4tY6kL8"
SHORT = "https://zaya.io/abc123"
ENV = {"ZAYA_ENABLED": "true", "ZAYA_BEARER_TOKEN": "test-bearer", "ZAYA_TIMEOUT_SECONDS": "1"}


def http_response(body, status=201):
    response = MagicMock()
    response.__enter__.return_value = response
    response.getcode.return_value = status
    response.read.return_value = json.dumps(body).encode() if not isinstance(body, bytes) else body
    return response


class ZayaClientTests(unittest.TestCase):
    def setUp(self):
        self.config = zaya.ZayaConfig("test-bearer")

    def test_disabled_ignores_all_other_settings(self):
        with patch.dict(os.environ, {"ZAYA_ENABLED": "false", "ZAYA_TIMEOUT_SECONDS": "invalid"}, clear=True):
            self.assertIsNone(zaya.ZayaConfig.from_environment())

    def test_configuration_validation(self):
        for key, value in (("ZAYA_ENABLED", "yes"), ("ZAYA_BEARER_TOKEN", ""),
                           ("ZAYA_BEARER_TOKEN", "bad\nsecret"), ("ZAYA_TEAM_ID", "0"),
                           ("ZAYA_TEAM_ID", "۱"), ("ZAYA_API_URL", "http://zaya.io/api"),
                           ("ZAYA_API_URL", "https://user:pass@zaya.io/api"),
                           ("ZAYA_TIMEOUT_SECONDS", "nan"), ("ZAYA_TIMEOUT_SECONDS", "11")):
            with self.subTest(key=key), patch.dict(os.environ, {**ENV, key: value}, clear=True):
                with self.assertRaisesRegex(zaya.ZayaConfigurationError, key):
                    zaya.ZayaConfig.from_environment()

    def test_query_headers_payload_and_response_allowlist(self):
        body = {"data": {"short_url": SHORT, "id": 5, "url": ORIGINAL, "token": "private"},
                "Authorization": "Bearer test-bearer", "customer": "private"}
        with patch.object(zaya, "open_request", return_value=http_response(body)) as http:
            result = zaya.post_link(self.config, ORIGINAL)
        request = http.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"team_id": ["463"], "url": [ORIGINAL]})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-bearer")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(result.short_url, SHORT)
        self.assertEqual(result.provider_response, {"data": {"short_url": SHORT, "id": 5}})

    def test_bad_responses_are_fallback_candidates(self):
        for body in ({}, {"data": None}, {"data": []}, {"data": {"short_url": "javascript:alert(1)"}},
                     {"data": {"short_url": "https://user:secret@zaya.io/a"}},
                     {"data": {"short_url": "http://127.0.0.1/a"}}, {"data": {"short_url": 3}},
                     b"invalid-json", b"x" * 65537):
            with self.subTest(body_type=type(body).__name__), patch.object(zaya, "open_request", return_value=http_response(body)):
                result = zaya.post_link(self.config, ORIGINAL)
                self.assertIsNone(result.short_url)
                self.assertIsNotNone(result.error_code)
                self.assertEqual(result.provider_response, {})

    def test_http_network_timeout_errors_are_safe(self):
        for error in (HTTPError(ORIGINAL, 403, "private", {}, io.BytesIO(b"secret")),
                      TimeoutError(ORIGINAL), URLError(ORIGINAL), ConnectionResetError(ORIGINAL)):
            with self.subTest(error=type(error).__name__), patch.object(zaya, "open_request", side_effect=error):
                result = zaya.post_link(self.config, ORIGINAL)
                self.assertIsNone(result.short_url)
                self.assertNotIn(ORIGINAL, repr(result))

    def test_redirects_are_not_followed_with_bearer_header(self):
        self.assertIsNone(zaya.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class ZayaStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase([])
        self.collection = self.db[zaya.COLLECTION]
        await zaya.create_index(self.collection)
        self.service = zaya.ZayaShortener(self.collection, zaya.ZayaConfig("test-bearer", timeout_seconds=1))
        self.http_patch = patch.object(zaya, "open_request", return_value=http_response({"data": {"short_url": SHORT}}))
        self.http = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    async def test_success_and_retry_use_one_durable_decision(self):
        self.assertEqual(await self.service.resolve("one", ORIGINAL), SHORT)
        self.assertEqual(await self.service.resolve("one", ORIGINAL), SHORT)
        self.http.assert_called_once()
        saved = self.collection.documents[0]
        self.assertEqual(saved["final_url"], SHORT)
        self.assertEqual(saved["status"], "success")
        self.assertEqual(saved["http_status"], 201)
        self.assertEqual(saved["original_url"], ORIGINAL)
        self.assertNotIn("test-bearer", json.dumps(saved, default=str))

    async def test_fallback_is_terminal_and_retry_does_not_request_again(self):
        self.http.return_value = http_response({"data": {}})
        with self.assertLogs(zaya.logger.name, level="WARNING") as logs:
            self.assertEqual(await self.service.resolve("one", ORIGINAL), ORIGINAL)
        self.assertNotIn(ORIGINAL, "".join(logs.output))
        self.assertNotIn("test-bearer", "".join(logs.output))
        self.http.return_value = http_response({"data": {"short_url": SHORT}})
        self.assertEqual(await self.service.resolve("one", ORIGINAL), ORIGINAL)
        self.http.assert_called_once()

    async def test_concurrent_workers_issue_one_provider_request(self):
        def slow(*args, **kwargs):
            time.sleep(.03)
            return http_response({"data": {"short_url": SHORT}})
        self.http.side_effect = slow
        results = await asyncio.gather(*(self.service.resolve("one", ORIGINAL) for _ in range(5)))
        self.assertIn(SHORT, results)
        self.assertTrue(all(result in (None, SHORT) for result in results))
        self.http.assert_called_once()
        self.assertEqual(len(self.collection.documents), 1)

    async def test_crashed_processing_expires_to_original_without_duplicate_request(self):
        with patch.object(zaya, "post_link", side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.resolve("one", ORIGINAL)
        self.assertIsNone(await self.service.resolve("one", ORIGINAL))
        self.collection.documents[0]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(await self.service.resolve("one", ORIGINAL), ORIGINAL)
        self.assertEqual(self.collection.documents[0]["error_code"], "processing_expired")
        self.http.assert_not_called()

    async def test_late_provider_result_cannot_replace_recovered_fallback(self):
        async def late(*args, **kwargs):
            record = self.collection.documents[0]
            record["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            self.assertEqual(await self.service.resolve("one", ORIGINAL), ORIGINAL)
            return zaya.ZayaResult(SHORT, 201, provider_response={"data": {"short_url": SHORT}})
        with patch.object(zaya.asyncio, "to_thread", new=late):
            self.assertEqual(await self.service.resolve("one", ORIGINAL), ORIGINAL)
        self.assertEqual(self.collection.documents[0]["status"], "fallback")

    async def test_timeout_is_bounded_and_does_not_block_event_loop(self):
        self.service.config = zaya.ZayaConfig("test-bearer", timeout_seconds=.02)
        def slow(*args, **kwargs):
            time.sleep(.08)
            return zaya.ZayaResult(SHORT, 201)
        with patch.object(zaya, "post_link", side_effect=slow):
            task = asyncio.create_task(self.service.resolve("one", ORIGINAL))
            await asyncio.sleep(.005)
            self.assertFalse(task.done())
            self.assertEqual(await task, ORIGINAL)
        self.assertEqual(self.collection.documents[0]["error_code"], "timeout")

    async def test_original_changed_or_corrupt_final_decision_never_sends_different_url(self):
        await self.service.resolve("one", ORIGINAL)
        with self.assertRaises(zaya.ZayaConfigurationError):
            await self.service.resolve("one", "https://different.example/other")
        self.collection.documents[0]["final_url"] = "https://different.example/other"
        with self.assertRaises(zaya.ZayaConfigurationError):
            await self.service.resolve("one", ORIGINAL)

    async def test_enabling_after_original_was_attempted_pins_original(self):
        self.assertEqual(await self.service.resolve("one", ORIGINAL, previous_attempts=1), ORIGINAL)
        self.http.assert_not_called()

    async def test_missing_unique_index_is_rejected(self):
        await zaya.check_index(self.collection)
        self.collection.indexes.clear()
        with self.assertRaises(zaya.ZayaConfigurationError):
            await zaya.check_index(self.collection)


class ZayaWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.no_network = patch("socket.create_connection", side_effect=AssertionError("Real HTTP forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.http_patch = patch.object(zaya, "open_request", return_value=http_response({"data": {"short_url": SHORT}}))
        self.http = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.settings = worker.WorkerSettings("preprocess_orders", sms_enabled=True, token_secret="x" * 48,
                                             selection_base_url="https://tester.example.com", sms_template=SMS_TEMPLATE)
        self.db = FakeDatabase([order()])
        await worker.create_indexes(worker.worker_collections(self.db, self.settings))
        self.db.writes = 0
        self.sms = FarazSmsClient(FarazSmsConfig("test-key", line_number="50001"))
        self.sms.send_simple = Mock(return_value=FarazSmsResponse(200, {"status": "success", "data": 123}))

    async def run_worker(self, **kwargs):
        return await worker.run_once(self.db, self.settings, sms=self.sms, **kwargs)

    async def test_success_then_no_resend_keeps_selection_and_acceptance_flow(self):
        self.assertEqual((await self.run_worker())["accepted"], 1)
        self.assertIn(SHORT, self.sms.send_simple.call_args.kwargs["text"])
        notification = self.db[worker.NOTIFICATIONS].documents[0]
        self.assertEqual(notification["status"], "accepted")
        self.assertTrue(self.db["preprocess_orders"].documents[0]["sms_sent"])
        self.assertEqual(notification["attempt_count"], 1)
        await self.run_worker()
        self.http.assert_called_once()
        self.sms.send_simple.assert_called_once()

    async def test_faraz_retry_reuses_same_short_url_and_token(self):
        self.sms.send_simple.side_effect = [SmsProviderError("timeout"), FarazSmsResponse(200, {"data": 123})]
        await self.run_worker()
        selection = copy.deepcopy(self.db[worker.SELECTIONS].documents[0])
        self.db[worker.NOTIFICATIONS].documents[0]["next_retry_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual((await self.run_worker())["accepted"], 1)
        texts = [call.kwargs["text"] for call in self.sms.send_simple.call_args_list]
        self.assertEqual(texts[0], texts[1])
        self.assertEqual(selection, self.db[worker.SELECTIONS].documents[0])
        self.http.assert_called_once()

    async def test_two_sms_workers_send_once_with_same_url_decision(self):
        await asyncio.gather(self.run_worker(), self.run_worker())
        self.http.assert_called_once()
        self.sms.send_simple.assert_called_once()
        self.assertIn(SHORT, self.sms.send_simple.call_args.kwargs["text"])
        self.assertEqual(len(self.db[worker.URLS].documents), 1)

    async def test_payment_deadline_crossed_during_shortening_prevents_sms(self):
        def shortened(*args, **kwargs):
            self.db["preprocess_orders"].documents[0]["date_paid"] = datetime.now(timezone.utc) - timedelta(minutes=41)
            return zaya.ZayaResult(SHORT, 201)
        with patch.object(zaya, "post_link", side_effect=shortened):
            self.assertEqual((await self.run_worker())["accepted"], 0)
        self.sms.send_simple.assert_not_called()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents[0]["attempt_count"], 0)

    async def test_fallback_survives_faraz_retry_even_if_zaya_recovers(self):
        self.http.return_value = http_response({"data": {}})
        self.sms.send_simple.side_effect = [SmsProviderError("timeout"), FarazSmsResponse(200, {"data": 123})]
        await self.run_worker()
        self.http.return_value = http_response({"data": {"short_url": SHORT}})
        self.db[worker.NOTIFICATIONS].documents[0]["next_retry_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual((await self.run_worker())["accepted"], 1)
        texts = [call.kwargs["text"] for call in self.sms.send_simple.call_args_list]
        self.assertEqual(texts[0], texts[1])
        self.http.assert_called_once()

    async def test_provider_failure_falls_back_and_still_sends(self):
        self.http.side_effect = URLError("private provider failure")
        self.assertEqual((await self.run_worker())["accepted"], 1)
        link = self.db[worker.URLS].documents[0]["original_url"]
        self.assertIn(link, self.sms.send_simple.call_args.kwargs["text"])
        self.assertEqual(self.db[worker.URLS].documents[0]["status"], "fallback")

    async def test_disabled_makes_no_url_database_or_http_calls(self):
        with patch.dict(os.environ, {"ZAYA_ENABLED": "false", "ZAYA_TIMEOUT_SECONDS": "invalid"}), \
                patch.object(self.db[worker.URLS], "find_one", side_effect=AssertionError("No URL DB work")), \
                patch.object(self.db[worker.URLS], "index_information", side_effect=AssertionError("No URL index work")):
            self.assertEqual((await self.run_worker())["accepted"], 1)
        self.http.assert_not_called()
        self.assertEqual(self.db[worker.URLS].documents, [])
        self.assertIn(self.settings.selection_base_url, self.sms.send_simple.call_args.kwargs["text"])

    async def test_dry_run_does_not_touch_zaya_even_with_broken_configuration(self):
        with patch.dict(os.environ, {"ZAYA_ENABLED": "broken"}):
            await self.run_worker(dry_run=True)
        self.assertEqual(self.db.writes, 0)
        self.http.assert_not_called()
        self.sms.send_simple.assert_not_called()

    async def test_database_failure_after_shortening_prevents_unrecorded_link_send(self):
        collection = self.db[worker.URLS]
        collection.fail_update = lambda update: update.get("$set", {}).get("status") == "success"
        self.assertEqual((await self.run_worker())["database_errors"], 1)
        self.sms.send_simple.assert_not_called()
        collection.fail_update = None
        collection.documents[0]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual((await self.run_worker())["accepted"], 1)
        self.assertIn(collection.documents[0]["original_url"], self.sms.send_simple.call_args.kwargs["text"])
        self.http.assert_called_once()

    async def test_paid_cli_ignores_invalid_zaya_settings(self):
        from types import SimpleNamespace
        client = MagicMock()
        client.admin.command = AsyncMock()
        client.close = AsyncMock()
        args = SimpleNamespace(create_paid_index=True)
        with patch.object(worker, "load_dotenv"), patch.object(worker, "read_environment", return_value=SimpleNamespace(mongodb_db_name="isolated")), \
                patch.dict(os.environ, {"MONGODB_URI": "mongodb://localhost", "ZAYA_ENABLED": "invalid"}), \
                patch.object(worker, "AsyncMongoClient", return_value=client), patch.object(worker, "create_paid_index", new=AsyncMock()), \
                patch.object(zaya.ZayaConfig, "from_environment", side_effect=AssertionError("No Zaya config")):
            self.assertEqual(await worker.run_paid_command(args), {"paid_index": "ready"})
