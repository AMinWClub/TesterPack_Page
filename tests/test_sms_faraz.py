"""Pattern SMS tests; all HTTP calls and credentials are mocked."""

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
                pattern_code="PATTERN_CODE",
            )
        )

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_pattern_payload_matches_documented_shape_without_sending(self):
        payload = self.client.build_pattern_payload(
            recipient="09123456789", attributes={"link": "https://example.com/token"},
        )
        self.assertEqual(payload, {
            "code": "PATTERN_CODE",
            "attributes": {"link": "https://example.com/token"},
            "recipient": "09123456789",
            "line_number": "50002178584000",
            "number_format": "english",
        })
        self.http.assert_not_called()

    def test_pattern_send_uses_correct_endpoint_headers_timeout_and_response(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as opener:
            result = self.client.send_pattern(
                recipient="+989120000000", attributes={"link": "https://example.com/token"},
            )
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://provider.example/ws/v1/sms/pattern")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Api-key"), "test-key")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertEqual(opener.call_args.kwargs["timeout"], 15.0)
        self.assertEqual(json.loads(request.data), self.client.build_pattern_payload(
            recipient="09120000000", attributes={"link": "https://example.com/token"},
        ))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.body, {"status": "success", "data": 42, "messages": None})
        self.assertEqual(result.data, 42)
        self.assertFalse(hasattr(result, "request_id"))

    def test_attributes_are_dynamic_and_preserve_strings_numbers_and_empty_values(self):
        attributes = {"first_name": "سارا", "otp": 41597, "amount": 12.5, "optional": ""}
        payload = self.client.build_pattern_payload(recipient="09120000000", attributes=attributes)
        self.assertEqual(payload["attributes"], attributes)
        self.assertIsNot(payload["attributes"], attributes)
        self.assertNotIn("link", payload["attributes"])
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as opener:
            self.client.send_pattern(recipient="09120000000", attributes=attributes)
        self.assertEqual(json.loads(opener.call_args.args[0].data)["attributes"], attributes)

    def test_empty_attributes_are_allowed_for_a_pattern_without_variables(self):
        payload = self.client.build_pattern_payload(recipient="09120000000", attributes={})
        self.assertEqual(payload["attributes"], {})

    def test_line_override_and_number_format_are_preserved(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse()) as opener:
            self.client.send_pattern(
                recipient="09120000000", attributes={}, line_number="50001", number_format="persian",
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
                self.client.send_pattern(recipient=recipient, attributes={})
        self.http.assert_not_called()

    def test_invalid_attributes_mapping_is_rejected_before_network(self):
        for attributes in (None, [], [("otp", "1")], "", "otp=1"):
            with self.subTest(attributes=attributes), self.assertRaises(SmsValidationError):
                self.client.send_pattern(recipient="09120000000", attributes=attributes)
        self.http.assert_not_called()

    def test_invalid_attribute_names_are_rejected_before_network(self):
        for name in ("", " ", " otp", "first-name", "1otp", 123):
            with self.subTest(name=name), self.assertRaises(SmsValidationError):
                self.client.send_pattern(recipient="09120000000", attributes={name: "1"})
        self.http.assert_not_called()

    def test_invalid_attribute_values_are_rejected_before_network(self):
        for value in (None, True, [], {}, float("nan"), float("inf"), object()):
            with self.subTest(value=value), self.assertRaises(SmsValidationError):
                self.client.send_pattern(recipient="09120000000", attributes={"otp": value})
        self.http.assert_not_called()

    def test_invalid_pattern_code_is_rejected_before_network(self):
        for code in ("", " ", None, 42):
            client = FarazSmsClient(FarazSmsConfig(
                api_key="test-key", line_number="50001", pattern_code=code,
            ))
            with self.subTest(code=code), self.assertRaises(SmsConfigurationError):
                client.send_pattern(recipient="09120000000", attributes={})
        self.http.assert_not_called()

    def test_invalid_number_format_is_rejected_before_network(self):
        for number_format in ("en", "fa", None, []):
            with self.subTest(number_format=number_format), self.assertRaises(SmsValidationError):
                self.client.send_pattern(recipient="09120000000", attributes={}, number_format=number_format)
        self.http.assert_not_called()

    def test_missing_api_key_url_and_line_are_rejected_before_network(self):
        for change in ({"api_key": ""}, {"base_url": ""}, {"line_number": ""}):
            config = FarazSmsConfig(**{
                "api_key": "test-key", "base_url": "https://provider.example",
                "line_number": "50001", "pattern_code": "PATTERN_CODE", **change,
            })
            with self.subTest(change=change), self.assertRaises(SmsConfigurationError):
                FarazSmsClient(config).send_pattern(recipient="09120000000", attributes={})
        self.http.assert_not_called()

    def test_environment_settings(self):
        with patch.dict(os.environ, {
            "FARAZ_API_KEY": " test-key ", "FARAZ_API_URL": " https://provider.example ",
            "FARAZ_LINE_NUMBER": " 50001 ", "FARAZ_TIMEOUT_SECONDS": "2.5",
            "FARAZ_PATTERN_CODE": " ENV_PATTERN ",
        }, clear=True):
            client = FarazSmsClient()
        self.assertEqual(client.config.api_key, "test-key")
        self.assertEqual(client.config.base_url, "https://provider.example")
        self.assertEqual(client.config.line_number, "50001")
        self.assertEqual(client.config.timeout_seconds, 2.5)
        self.assertEqual(client.config.pattern_code, "ENV_PATTERN")
        self.assertEqual(client.build_pattern_payload(
            recipient="09120000000", attributes={},
        )["code"], "ENV_PATTERN")
        self.env_loader.assert_called_once_with(ENV_FILE, encoding="utf-8")
        self.assertEqual(ENV_FILE, Path(__file__).resolve().parents[1] / ".env")

    def test_environment_defaults_do_not_require_mongodb_or_sms_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            config = FarazSmsClient().config
        self.assertEqual(config.base_url, "https://api.iranpayamak.com")
        self.assertEqual(config.timeout_seconds, 15.0)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.line_number, "")
        self.assertEqual(config.pattern_code, "")

    def test_dotenv_loading_keeps_existing_environment_priority(self):
        env_text = (
            'FARAZ_API_KEY=file-key\nFARAZ_API_URL=https://provider.example\n'
            'FARAZ_LINE_NUMBER=50001\nFARAZ_TIMEOUT_SECONDS=3\nFARAZ_PATTERN_CODE=file-pattern\n'
        )

        def load_test_env(path, *, encoding):
            self.assertEqual(path, ENV_FILE)
            return real_load_dotenv(stream=io.StringIO(env_text), encoding=encoding)

        with patch.dict(os.environ, {
            "FARAZ_API_KEY": "environment-key", "FARAZ_PATTERN_CODE": "environment-pattern",
        }, clear=True), patch("app.services.sms.faraz.load_dotenv", side_effect=load_test_env):
            config = FarazSmsConfig.from_environment()
        self.assertEqual(config.api_key, "environment-key")
        self.assertEqual(config.base_url, "https://provider.example")
        self.assertEqual(config.line_number, "50001")
        self.assertEqual(config.timeout_seconds, 3.0)
        self.assertEqual(config.pattern_code, "environment-pattern")

    def test_pattern_code_is_loaded_from_dotenv(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.services.sms.faraz.load_dotenv",
            side_effect=lambda *args, **kwargs: real_load_dotenv(
                stream=io.StringIO("FARAZ_PATTERN_CODE=file-pattern\n"),
            ),
        ):
            config = FarazSmsConfig.from_environment()
        self.assertEqual(config.pattern_code, "file-pattern")

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
                self.client.send_pattern(recipient="09120000000", attributes={})
            opener.assert_called_once()
            self.assertIsNone(context.exception.status_code)

    def test_http_error_preserves_status_and_body(self):
        body = {"status": "error", "data": None, "messages": {"recipient": ["invalid"]}}
        error = HTTPError("https://provider.example", 422, "Invalid", {}, io.BytesIO(json.dumps(body).encode()))
        with patch("app.services.sms.faraz.urlopen", side_effect=error) as opener, \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_pattern(recipient="09120000000", attributes={})
        opener.assert_called_once()
        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.body, body)

    def test_unsuccessful_http_response_is_provider_error(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(503)), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_pattern(recipient="09120000000", attributes={})
        self.assertEqual(context.exception.status_code, 503)

    def test_provider_error_status_in_successful_http_response_is_rejected(self):
        body = {"status": "error", "data": None, "messages": ["Pattern is invalid"]}
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode())), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_pattern(recipient="09120000000", attributes={})
        self.assertEqual(context.exception.status_code, 201)
        self.assertEqual(context.exception.body, body)

    def test_numeric_response_and_all_message_shapes_are_preserved_without_request_id(self):
        for data, messages in ((0, None), (42, "sent"), (1.5, []), (7, {"info": ["sent"]})):
            body = {"status": "success", "data": data, "messages": messages}
            with self.subTest(data=data, messages=messages), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode()),
            ):
                response = self.client.send_pattern(recipient="09120000000", attributes={})
            self.assertEqual(response.body, body)
            self.assertEqual(response.data, data)
            self.assertFalse(hasattr(response, "request_id"))

    def test_invalid_success_response_is_rejected(self):
        for body in ({}, [], {"data": 42}, {"status": "unknown", "data": 42},
                     {"status": "success"}, {"status": "success", "data": {"id": 42}},
                     {"status": "success", "data": True}, {"status": "success", "data": "42"},
                     {"status": "success", "data": float("inf")}):
            with self.subTest(body=body), patch(
                "app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, json.dumps(body).encode()),
            ), self.assertRaises(SmsProviderError):
                self.client.send_pattern(recipient="09120000000", attributes={})

    def test_non_json_success_response_is_rejected_and_preserved(self):
        with patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse(201, b"plain response")), \
             self.assertRaises(SmsProviderError) as context:
            self.client.send_pattern(recipient="09120000000", attributes={})
        self.assertEqual(context.exception.body, {"raw": "plain response"})

    def test_api_key_is_excluded_from_config_repr(self):
        self.assertNotIn("test-key", repr(self.client.config))

    def test_sensitive_attributes_and_provider_data_are_not_logged(self):
        sensitive = "09120000000 test-key https://example.test/?token=private-token"
        cases = (
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
                        self.client.send_pattern(recipient="09120000000", attributes={"link": sensitive})
                    except SmsProviderError as error:
                        rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
                        for secret in ("test-key", "private-token"):
                            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
