"""Optional Zaya client and a durable, per-order URL decision."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError

COLLECTION = "tester_sms_url"
INDEX = "tester_sms_url_order_unique"
SIMPLE = Collation(locale="simple")
logger = logging.getLogger(__name__)


class ZayaConfigurationError(ValueError):
    """Safe configuration/state error; never contains a URL or credential."""


def valid_url(value) -> bool:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return False
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.fragment or (url.port is not None and not 1 <= url.port <= 65535)):
            return False
        # Provider short links must be public web URLs, not local/internal targets.
        host = url.hostname.lower()
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ZayaConfig:
    bearer_token: str = field(repr=False)
    api_url: str = "https://zaya.io/api/v1/links"
    team_id: int = 463
    timeout_seconds: float = 5

    @classmethod
    def from_environment(cls) -> ZayaConfig | None:
        enabled = os.getenv("ZAYA_ENABLED", "false").strip().lower()
        if enabled == "false":
            return None  # Do not parse unrelated settings, touch MongoDB or make HTTP calls.
        if enabled != "true":
            raise ZayaConfigurationError("ZAYA_ENABLED must be true or false.")
        token = os.getenv("ZAYA_BEARER_TOKEN", "").strip()
        if not token or re.search(r"[^\x21-\x7e]", token):
            raise ZayaConfigurationError("ZAYA_BEARER_TOKEN must be configured with a valid bearer token.")
        api_url = os.getenv("ZAYA_API_URL", "https://zaya.io/api/v1/links").strip()
        if not valid_url(api_url) or urlsplit(api_url).scheme != "https" or urlsplit(api_url).query:
            raise ZayaConfigurationError("ZAYA_API_URL must be a public HTTPS URL without query, fragment or credentials.")
        team = os.getenv("ZAYA_TEAM_ID", "463").strip()
        if not re.fullmatch(r"[0-9]{1,10}", team) or int(team) <= 0:
            raise ZayaConfigurationError("ZAYA_TEAM_ID must be a positive integer.")
        try:
            timeout = float(os.getenv("ZAYA_TIMEOUT_SECONDS", "5"))
        except ValueError:
            timeout = 0
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ZayaConfigurationError("ZAYA_TIMEOUT_SECONDS must be greater than zero and at most 10 seconds.")
        return cls(token, api_url, int(team), timeout)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer credential to a redirected host.


open_request = build_opener(NoRedirect()).open


@dataclass(frozen=True)
class ZayaResult:
    short_url: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    provider_response: dict = field(default_factory=dict, repr=False)


def post_link(config: ZayaConfig, original_url: str) -> ZayaResult:
    request = Request(config.api_url + "?" + urlencode({"team_id": config.team_id, "url": original_url}),
                      method="POST", data=b"", headers={"Authorization": "Bearer " + config.bearer_token,
                                                        "Accept": "application/json"})
    status = None
    try:
        with open_request(request, timeout=config.timeout_seconds) as response:
            status = response.getcode()
            raw = response.read(65537)
        if not 200 <= status < 300:
            return ZayaResult(http_status=status, error_code="http_error")
        if len(raw) > 65536:
            return ZayaResult(http_status=status, error_code="response_too_large")
        body = json.loads(raw)
        data = body.get("data") if isinstance(body, dict) else None
        short = data.get("short_url") if isinstance(data, dict) else None
        if not valid_url(short):
            return ZayaResult(http_status=status, error_code="invalid_short_url")
        # Allowlist the response: unknown fields may echo credentials or customer data.
        safe = {"short_url": short}
        if type(data.get("id")) is int and data["id"] > 0:
            safe["id"] = data["id"]
        return ZayaResult(short, status, provider_response={"data": safe})
    except HTTPError as error:
        error.close()
        return ZayaResult(http_status=error.code, error_code="http_error")
    except (TimeoutError,):
        return ZayaResult(http_status=status, error_code="timeout")
    except (URLError, ConnectionError, OSError, HTTPException):
        return ZayaResult(http_status=status, error_code="network_error")
    except (ValueError, UnicodeError, TypeError):
        return ZayaResult(http_status=status, error_code="invalid_response")


async def create_index(collection) -> None:
    await collection.create_index("order_id", unique=True, name=INDEX, collation=SIMPLE)


async def check_index(collection) -> None:
    indexes = await collection.index_information()
    if not any(list(index["key"]) == [("order_id", 1)] and index.get("unique")
               and not index.get("sparse") and index.get("partialFilterExpression") is None
               and index.get("collation", {}).get("locale", "simple") == "simple" for index in indexes.values()):
        raise ZayaConfigurationError("Required URL index is missing; run --create-indexes first.")


class ZayaShortener:
    def __init__(self, collection, config: ZayaConfig):
        self.collection = collection
        self.config = config

    @staticmethod
    def final_url(record: dict, original: str) -> str | None:
        if record.get("original_url") != original:
            raise ZayaConfigurationError("Stored order URL differs from the current Selection URL; restore the original link settings.")
        status = record.get("status")
        if status == "success" and valid_url(record.get("short_url")) and record.get("final_url") == record["short_url"]:
            return record["final_url"]
        if status == "fallback" and record.get("final_url") == original:
            return original
        if status != "processing":
            raise ZayaConfigurationError("Stored order URL decision is invalid; inspect the URL record.")
        return None

    async def resolve(self, order_id, original_url: str, *, previous_attempts: int = 0) -> str | None:
        now = datetime.now(timezone.utc)
        claim_id = uuid4().hex
        record = await self.collection.find_one({"order_id": order_id}, collation=SIMPLE)
        if record is None:
            initial = {"order_id": order_id, "original_url": original_url, "short_url": None,
                       "final_url": None, "status": "processing", "provider": "zaya", "team_id": self.config.team_id,
                       "provider_response": {}, "http_status": None, "error_code": None,
                       "created_at": now, "updated_at": now, "claim_id": claim_id,
                       "lease_expires_at": now + timedelta(seconds=self.config.timeout_seconds + 30)}
            try:
                await self.collection.update_one({"order_id": order_id}, {"$setOnInsert": initial}, upsert=True, collation=SIMPLE)
            except DuplicateKeyError:
                pass
            record = await self.collection.find_one({"order_id": order_id}, collation=SIMPLE)
        if record is None:
            raise ZayaConfigurationError("Order URL claim could not be read.")
        final = self.final_url(record, original_url)
        if final is not None:
            return final
        if record.get("claim_id") != claim_id:
            # Recover an abandoned/uncertain creation by pinning the original URL.
            result = await self.collection.update_one({"order_id": order_id, "status": "processing",
                "claim_id": record.get("claim_id"), "lease_expires_at": {"$lte": now}}, {"$set": {
                    "status": "fallback", "final_url": original_url, "error_code": "processing_expired",
                    "updated_at": now, "lease_expires_at": None}}, collation=SIMPLE)
            if not result.matched_count:
                return None  # Another worker is still processing: do not send a different URL.
        else:
            if previous_attempts:
                # Zaya may have been enabled after an earlier original-URL SMS attempt.
                outcome = ZayaResult(error_code="original_already_attempted")
            else:
                try:
                    outcome = await asyncio.wait_for(asyncio.to_thread(post_link, self.config, original_url),
                                                     timeout=self.config.timeout_seconds)
                except TimeoutError:
                    outcome = ZayaResult(error_code="timeout")
            await self.collection.update_one({"order_id": order_id, "status": "processing", "claim_id": claim_id},
                {"$set": {"status": "success" if outcome.short_url else "fallback", "short_url": outcome.short_url,
                    "final_url": outcome.short_url or original_url, "provider_response": outcome.provider_response,
                    "http_status": outcome.http_status, "error_code": outcome.error_code,
                    "updated_at": datetime.now(timezone.utc), "lease_expires_at": None}}, collation=SIMPLE)
            if outcome.error_code:
                logger.warning("Zaya URL creation did not succeed (%s); original URL selected.", outcome.error_code)
        stored = await self.collection.find_one({"order_id": order_id}, collation=SIMPLE)
        if stored is None:
            raise ZayaConfigurationError("Order URL decision could not be read.")
        return self.final_url(stored, original_url)
