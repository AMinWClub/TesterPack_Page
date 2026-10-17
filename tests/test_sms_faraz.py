"""Simple SMS tests; all HTTP calls and credentials are mocked."""

import io
import json
import os
import traceback
import unittest
from http.client import HTTPException
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv as real_load_dotenv

from app.services.sms import (
    FarazSmsClient,
    FarazSmsConfig,
    SmsConfigurationError,
    SmsProviderError,
    SmsValidationError,
)
from app.services.sms.faraz import ENV_FILE, normalize_recipient


class FakeHttpResponse:
    def __init__(self, status_code=201, body=b'{"status":"success","data":42,"messages":null}'):
        self.status_code = status_code
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status_code

    def read(self):
        return self.body


def simple_success_body():
    """Synthetic provider fixture, including metadata that must never be persisted."""
    return {
        "status": "success", "message": "",
        "data": {
            "id": 15870343, "type": "simple", "status": "pending-approval",
            "metadata": {
                "text": "private-sms-text https://example.test/aB7xQ2mN9pR4tY6kL8",
                "line_number": "PRO", "number_format": "english", "recipients": ["09120000000"],
            },
            "schedule": "2026-09-21T13:07:32.000000Z",
        },
    }


class FarazSmsClientTests(unittest.TestCase):
    def setUp(self):
        # Fail closed: no test may read real credentials or make an HTTP call.
        self.http = self.start_patch(
            "app.services.sms.faraz.urlopen", side_effect=AssertionError("HTTP must be mocked"),
        )
        self.start_patch("socket.create_connection", side_effect=AssertionError("Network is disabled in tests"))
        self.env_loader = self.start_patch("app.services.sms.faraz.load_dotenv")
        self.client = FarazSmsClient(
            FarazSmsConfig(
                api_key="test-key",
                line_number="50002178584000",
                base_url="https://provider.example",
            )
        )

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_simple_payload_matches_documented_shape_without_sending(self):
        payload = self.client.build_simple_payload(
            recipient="09123456789", text="پیام آزمایشی\nhttps://example.com/token",
        )
        self.assertEqual(payload, {
            "text": "پیام آزمایشی\nhttps://example.com/token",
            "recipients": ["09123456789"],
            "line_number": "50002178584000",
            "number_format": "english",
            "schedule": None,
        })
        self.http.assert_not_called()

    def test_simple_send_uses_correct_endpoint_headers_timeout_and_response(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as opener:
            result = self.client.send_simple(
                recipient="+989120000000", text="پیام آزمایشی\nhttps://example.com/token",
            )
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://provider.example/ws/v1/sms/simple")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Api-key"), "test-key")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertEqual(opener.call_args.kwargs["timeout"], 15.0)
        self.assertEqual(json.loads(request.data), self.client.build_simple_payload(
            recipient="09120000000", text="پیام آزمایشی\nhttps://example.com/token",
        ))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.body, {"status": "success", "data": 42, "messages": None})
        self.assertEqual(result.data, 42)
        self.assertFalse(hasattr(result, "request_id"))

    def test_exact_text_whitespace_and_unicode_are_preserved(self):
        text = "  مشتری عزیز سراد\n\nبرای تکمیل سفارش، لطفاً اقدام فرمایید:\n\nhttps://example.test/aB7xQ2mN9pR4tY6kL8  \n"
        self.http.return_value = FakeHttpResponse()
        self.http.side_effect = None
        self.client.send_simple(recipient="09120000000", text=text)
        encoded = self.http.call_args.args[0].data
        self.assertEqual(json.loads(encoded)["text"], text)
        self.assertIn("مشتری عزیز سراد".encode("utf-8"), encoded)

    def test_invalid_text_is_rejected_before_network(self):
        for text in (None, "", " \n\t", 42, True, [], {}, ["message"]):
            with self.subTest(text=text), self.assertRaises(SmsValidationError):
                self.client.send_simple(recipient="09120000000", text=text)
        self.http.assert_not_called()

    def test_line_override_and_number_format_are_preserved(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as opener:
            self.client.send_simple(
                recipient="09120000000", text="test message", line_number="50001", number_format="persian",
            )
        payload = json.loads(opener.call_args.args[0].data)
        self.assertEqual(payload["line_number"], "50001")
        self.assertEqual(payload["number_format"], "persian")

    def test_recipient_normalization(self):
        for phone in ("09120000000", "+98 912-000-0000", "989120000000", "۰۹۱۲۰۰۰۰۰۰۰"):
            with self.subTest(phone=phone):
                self.assertEqual(normalize_recipient(phone), "09120000000")

    def test_invalid_recipient_and_multiple_recipients_are_rejected_before_network(self):
        for recipient in ("123", "", None, 9120000000, ["09120000000"], ("09120000000",)):
            with self.subTest(recipient=recipient), self.assertRaises(SmsValidationError):
                self.client.send_simple(recipient=recipient, text="test message")
        self.http.assert_not_called()

    def test_invalid_number_format_is_rejected_before_network(self):
        for number_format in ("en", "fa", None, []):
            with self.subTest(number_format=number_format), self.assertRaises(SmsValidationError):
                self.client.send_simple(recipient="09120000000", text="test message", number_format=number_format)
        self.http.assert_not_called()

    def test_missing_api_key_url_and_line_are_rejected_before_network(self):
        for change in ({"api_key": ""}, {"base_url": ""}, {"line_number": ""}):
            config = FarazSmsConfig(**{
                "api_key": "test-key", "base_url": "https://provider.example",
                "line_number": "50001", **change,
            })
            with self.subTest(change=change), self.assertRaises(SmsConfigurationError):
                FarazSmsClient(config).send_simple(recipient="09120000000", text="test message")
        self.http.assert_not_called()

    def test_environment_settings(self):
        with patch.dict(os.environ, {
            "FARAZ_API_KEY": " test-key ", "FARAZ_API_URL": " https://provider.example ",
            "FARAZ_LINE_NUMBER": " 50001 ", "FARAZ_TIMEOUT_SECONDS": "2.5",
        }, clear=True):
            client = FarazSmsClient()
        self.assertEqual(client.config.api_key, "test-key")
        self.assertEqual(client.config.base_url, "https://provider.example")
        self.assertEqual(client.config.line_number, "50001")
        self.assertEqual(client.config.timeout_seconds, 2.5)
        self.env_loader.assert_called_once_with(ENV_FILE, encoding="utf-8")
        self.assertEqual(ENV_FILE, Path(__file__).resolve().parents[1] / ".env")

    def test_environment_defaults_do_not_require_mongodb_or_sms_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            config = FarazSmsClient().config
        self.assertEqual(config.base_url, "https://api.iranpayamak.com")
        self.assertEqual(config.timeout_seconds, 15.0)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.line_number, "")

    def test_dotenv_loading_keeps_existing_environment_priority(self):
        env_text = (
            'FARAZ_API_KEY=file-key\nFARAZ_API_URL=https://provider.example\n'
            'FARAZ_LINE_NUMBER=50001\nFARAZ_TIMEOUT_SECONDS=3\n'
        )

        def load_test_env(path, *, encoding):
            self.assertEqual(path, ENV_FILE)
            return real_load_dotenv(stream=io.StringIO(env_text), encoding=encoding)

        with patch.dict(os.environ, {
            "FARAZ_API_KEY": "environment-key",
        }, clear=True), patch("app.services.sms.faraz.load_dotenv", side_effect=load_test_env):
            config = FarazSmsConfig.from_environment()
        self.assertEqual(config.api_key, "environment-key")
        self.assertEqual(config.base_url, "https://provider.example")
        self.assertEqual(config.line_number, "50001")
        self.assertEqual(config.timeout_seconds, 3.0)

    def test_invalid_environment_timeout_is_rejected(self):
        for timeout in ("invalid", "0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout), patch.dict(
                os.environ, {"FARAZ_TIMEOUT_SECONDS": timeout}, clear=True,
            ), self.assertRaises(SmsConfigurationError):
                FarazSmsConfig.from_environment()

    def test_post_transport_failures_are_normalized_without_retry(self):
        for error in (TimeoutError("read timed out"), URLError("unreachable"),
                      ConnectionError("disconnected"), HTTPException("bad response")):
            with self.subTest(error=type(error).__name__), patch(
                "app.services.sms.faraz.urlopen", side_effect=error,
            ) as opener, self.assertRaises(SmsProviderError) as context:
                self.client.send_simple(recipient="09120000000", text="test message")
            opener.assert_called_once()
            self.assertIsNone(context.exception.status_code)

    def test_http_error_preserves_status_and_body(self):
        body = {"status": "error", "data": None, "messages": {"recipient": ["invalid"]}}
        error = HTTPError("https://provider.example", 422, "Invalid", {}, io.BytesIO(json.dumps(body).encode()))
        with patch("app.services.sms.faraz.urlopen", side_effect=error) as opener, \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_simple(recipient="09120000000", text="test message")
        opener.assert_called_once()
        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.body, body)

    def test_unsuccessful_http_response_is_provider_error(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(503)), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_simple(recipient="09120000000", text="test message")
        self.assertEqual(context.exception.status_code, 503)

    def test_provider_error_status_in_successful_http_response_is_rejected(self):
        body = {"status": "error", "data": None, "messages": ["Message is invalid"]}
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode())), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_simple(recipient="09120000000", text="test message")
        self.assertEqual(context.exception.status_code, 201)
        self.assertEqual(context.exception.body, body)

    def test_numeric_response_and_all_message_shapes_are_preserved_without_request_id(self):
        for data, messages in ((0, None), (42, "sent"), (1.5, []), (7, {"info": ["sent"]})):
            body = {"status": "success", "data": data, "messages": messages}
            with self.subTest(data=data, messages=messages), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode()),
            ):
                response = self.client.send_simple(recipient="09120000000", text="test message")
            self.assertEqual(response.body, body)
            self.assertEqual(response.data, data)
            self.assertFalse(hasattr(response, "request_id"))

    def test_object_pending_approval_is_accepted_for_successful_http_statuses(self):
        body = simple_success_body()
        for status_code in (200, 201, 202, 299):
            with self.subTest(status_code=status_code), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(status_code, json.dumps(body).encode()),
            ):
                response = self.client.send_simple(recipient="09120000000", text="test message")
            self.assertEqual(response.status_code, status_code)
            self.assertEqual(response.data, 15870343)
            self.assertIs(type(response.data), int)
            self.assertEqual(response.body, body)
            for private in ("private-sms-text", "09120000000", "aB7xQ2mN9pR4tY6kL8"):
                self.assertNotIn(private, repr(response))

    def test_object_success_requires_positive_integer_id_simple_type_and_known_state(self):
        invalid_data = [
            {}, [], None, "15870343", True,
            *({"id": value, "type": "simple", "status": "pending-approval"}
              for value in (None, True, False, 0, -1, 1.5, 42.0, "42", float("inf"))),
            *({"id": 42, "type": value, "status": "pending-approval"}
              for value in (None, "pattern", "Simple", "", [])),
            *({"id": 42, "type": "simple", "status": value}
              for value in (None, "success", "sent", "delivered", "failed", "pending", "PENDING-APPROVAL", [])),
            {"id": 42, "type": "simple"}, {"id": 42, "status": "pending-approval"},
        ]
        for data in invalid_data:
            body = {"status": "success", "data": data}
            with self.subTest(data=data), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(200, json.dumps(body).encode()),
            ), self.assertRaises(SmsProviderError) as caught:
                self.client.send_simple(recipient="09120000000", text="test message")
            self.assertEqual(caught.exception.status_code, 200)

    def test_valid_object_does_not_override_http_or_top_level_failure(self):
        for http_status, provider_status in ((199, "success"), (300, "success"), (500, "success"),
                                             (200, "error"), (200, "Success"), (200, None)):
            body = {**simple_success_body(), "status": provider_status}
            with self.subTest(http_status=http_status, provider_status=provider_status), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(http_status, json.dumps(body).encode()),
            ), self.assertRaises(SmsProviderError):
                self.client.send_simple(recipient="09120000000", text="test message")

    def test_invalid_success_response_is_rejected(self):
        for body in ({}, [], {"data": 42}, {"status": "unknown", "data": 42},
                     {"status": "success"}, {"status": "success", "data": {"id": 42}},
                     {"status": "success", "data": True}, {"status": "success", "data": "42"},
                     {"status": "success", "data": float("inf")}):
            with self.subTest(body=body), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode()),
            ), self.assertRaises(SmsProviderError):
                self.client.send_simple(recipient="09120000000", text="test message")

    def test_non_json_success_response_is_rejected_and_preserved(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, b"plain response")), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_simple(recipient="09120000000", text="test message")
        self.assertEqual(context.exception.body, {"raw": "plain response"})

    def test_api_key_is_excluded_from_config_repr(self):
        self.assertNotIn("test-key", repr(self.client.config))

    def test_sensitive_text_and_provider_data_are_not_logged(self):
        sensitive = "09120000000 test-key https://example.test/?token=private-token"
        cases = (
            FakeHttpResponse(200, json.dumps(simple_success_body()).encode()),
            FakeHttpResponse(201, json.dumps({"status": "success", "data": 42, "messages": sensitive}).encode()),
            FakeHttpResponse(201, json.dumps({"status": "error", "data": None, "messages": sensitive}).encode()),
            HTTPError("https://provider.example", 400, sensitive, {}, io.BytesIO(sensitive.encode())),
            URLError(sensitive),
        )
        for case in cases:
            with self.subTest(case=type(case).__name__), self.assertNoLogs("app.services.sms.faraz", level="DEBUG"):
                options = {"return_value": case} if isinstance(case, FakeHttpResponse) else {"side_effect": case}
                with patch("app.services.sms.faraz.urlopen", **options):
                    try:
                        self.client.send_simple(recipient="09120000000", text=sensitive)
                    except SmsProviderError as error:
                        rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
                        for secret in ("test-key", "private-token"):
                            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
