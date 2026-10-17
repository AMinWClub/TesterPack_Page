"""Standalone FarazSMS (IranPayamak) client for single-recipient Simple SMS.

Adapted from SMS_Panel/app/services/sms/faraz.py (source blob 23825ee).
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

SIMPLE_PATH = "/ws/v1/sms/simple"
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class SmsServiceError(RuntimeError):
    """Base error raised by the SMS provider client."""


class SmsConfigurationError(SmsServiceError):
    """Provider credentials/defaults are missing."""


class SmsValidationError(SmsServiceError):
    """A provider request is invalid before it is sent."""


class SmsProviderError(SmsServiceError):
    """The provider rejected a request or could not be reached."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class FarazSmsConfig:
    """Sender credentials and request timeout."""

    api_key: str = field(repr=False)
    base_url: str = "https://api.iranpayamak.com"
    line_number: str = ""
    timeout_seconds: float = 15.0

    @classmethod
    def from_environment(cls) -> "FarazSmsConfig":
        # Match app.config: root .env, UTF-8, existing environment takes priority.
        load_dotenv(ENV_FILE, encoding="utf-8")
        try:
            timeout = float(os.getenv("FARAZ_TIMEOUT_SECONDS", "15"))
        except ValueError as exc:
            raise SmsConfigurationError("FARAZ_TIMEOUT_SECONDS must be numeric.") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise SmsConfigurationError("FARAZ_TIMEOUT_SECONDS must be finite and greater than zero.")
        return cls(
            api_key=os.getenv("FARAZ_API_KEY", "").strip(),
            base_url=os.getenv("FARAZ_API_URL", "https://api.iranpayamak.com").strip(),
            line_number=os.getenv("FARAZ_LINE_NUMBER", "").strip(),
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class FarazSmsResponse:
    """Accepted Simple response; data exposes only the provider's numeric result."""

    status_code: int
    body: Any = field(repr=False)

    @property
    def data(self) -> int | float:
        data = self.body["data"]
        return data["id"] if isinstance(data, Mapping) else data


def normalize_recipient(value: str) -> str:
    """Normalize +989.../989.../09... to provider form 09xxxxxxxxx."""

    if not isinstance(value, str):
        raise SmsValidationError("Recipient must be a string.")
    phone = value.translate(PERSIAN_DIGITS).replace(" ", "").replace("-", "")
    if phone.startswith("+98"):
        phone = "0" + phone[3:]
    elif phone.startswith("98"):
        phone = "0" + phone[2:]
    if not re.fullmatch(r"09\d{9}", phone):
        raise SmsValidationError("Recipient must be a valid Iranian mobile number.")
    return phone


class FarazSmsClient:
    """Synchronous client for one Simple SMS recipient per request."""

    def __init__(self, config: FarazSmsConfig | None = None):
        self.config = config or FarazSmsConfig.from_environment()

    def send_simple(
        self,
        *,
        recipient: str,
        text: str,
        line_number: str | None = None,
        number_format: str = "english",
    ) -> FarazSmsResponse:
        """POST /ws/v1/sms/simple for a single recipient."""

        return self._post(
            SIMPLE_PATH,
            self.build_simple_payload(
                recipient=recipient,
                text=text,
                line_number=line_number,
                number_format=number_format,
            ),
        )

    def build_simple_payload(
        self,
        *,
        recipient: str,
        text: str,
        line_number: str | None = None,
        number_format: str = "english",
    ) -> dict[str, Any]:
        """Build an immediate Simple SMS body, preserving the exact message text."""

        if not isinstance(text, str) or not text.strip():
            raise SmsValidationError("SMS text must be a non-empty string.")
        return {
            "text": text,
            "recipients": [normalize_recipient(recipient)],
            "line_number": self._line_number(line_number),
            "number_format": self._number_format(number_format),
            "schedule": None,
        }

    def _line_number(self, line_number: str | None) -> str:
        value = (line_number or "").strip() or self.config.line_number.strip()
        if not value:
            raise SmsConfigurationError("A FarazSMS line number is required.")
        return value

    @staticmethod
    def _number_format(value: str) -> str:
        if not isinstance(value, str) or value not in {"english", "persian"}:
            raise SmsValidationError("number_format must be english or persian.")
        return value

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> FarazSmsResponse:
        if not self.config.api_key:
            raise SmsConfigurationError("FARAZ_API_KEY is not configured.")
        if not self.config.base_url:
            raise SmsConfigurationError("FARAZ_API_URL is not configured.")

        url = f"{self.config.base_url.rstrip('/')}{path}"
        payload_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            url,
            data=payload_bytes,
            headers={
                "Api-Key": self.config.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

        # Never log requests, provider bodies or transport exceptions: they can
        # contain credentials, customer numbers, message text or link tokens.
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                status_code = response.getcode()
                body = self._decode_response(response.read())
        except HTTPError as exc:
            raise SmsProviderError(
                "FarazSMS rejected the request.",
                status_code=exc.code,
                body=self._decode_response(exc.read()),
            ) from None
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            raise SmsProviderError(
                "FarazSMS connection failed before a reliable response was received."
            ) from None

        if not 200 <= status_code < 300:
            raise SmsProviderError(
                "FarazSMS returned an unsuccessful response.",
                status_code=status_code,
                body=body,
            )
        if not isinstance(body, Mapping) or body.get("status") != "success":
            raise SmsProviderError(
                "FarazSMS did not report a successful Simple SMS send.",
                status_code=status_code,
                body=body,
            )
        data = body.get("data")
        # Accept only the contracted Simple submission state, not delivery claims
        # or undocumented states. Keep the legacy finite numeric response valid.
        object_accepted = (
            isinstance(data, Mapping)
            and type(data.get("id")) is int and data["id"] > 0
            and data.get("type") == "simple"
            and data.get("status") == "pending-approval"
        )
        numeric_accepted = isinstance(data, (int, float)) and not isinstance(data, bool) and not (
            isinstance(data, float) and not math.isfinite(data)
        )
        if not (object_accepted or numeric_accepted):
            raise SmsProviderError(
                "FarazSMS returned an invalid Simple SMS response.",
                status_code=status_code,
                body=body,
            )
        return FarazSmsResponse(status_code=status_code, body=body)

    @staticmethod
    def _decode_response(raw: bytes) -> Any:
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

