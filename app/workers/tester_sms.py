"""One-shot tester-order SMS worker; no database or network work at import."""

from __future__ import annotations

import asyncio
import argparse
import base64
import hashlib
import hmac
import logging
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Formatter
from urllib.parse import urlsplit
from uuid import uuid4

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps
from dotenv import load_dotenv
from pymongo import ReadPreference, ReturnDocument
from pymongo import AsyncMongoClient
from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from app.environment import read_environment
from app.services.sms import (
    FarazSmsClient,
    SmsConfigurationError,
    SmsProviderError,
    SmsValidationError,
)
from app.services.sms.faraz import normalize_recipient
from app.services.url_shortener.zaya import (
    COLLECTION as URLS, ZayaConfig, ZayaConfigurationError, ZayaShortener,
    check_index as check_url_index, create_index as create_url_index,
)
from app.workers.paid_release import create_index as create_paid_index, paid_at_expression, release_paid_orders

NOTIFICATIONS = "tester_sms_notifications"
SELECTIONS = "tester_selections"
TOKEN_VERSION = 3
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
logger = logging.getLogger(__name__)
ORDER_MOBILE_FIELD = "phone"
ORDER_INDEX = "tester_pending_orders"
ORDER_INDEX_KEYS = [("state", 1), ("_id", 1)]
RELEASE_SOURCE_INDEX = "tester_notification_repair"
RELEASE_SOURCE_INDEX_KEYS = [("status", 1), ("order_synced", 1), ("accepted_at", 1)]
RELEASE_DECISION_ATTEMPTS = 3


class WorkerConfigurationError(ValueError):
    """Worker settings or the order schema need attention."""


@dataclass(frozen=True)
class WorkerSettings:
    orders_collection: str
    batch_size: int = 50
    sms_enabled: bool = False
    selection_base_url: str = ""
    token_secret: str = field(default="", repr=False)
    retry_delay_minutes: int = 5
    max_attempts: int = 3
    release_enabled: bool | None = None  # Unset follows sms_enabled, preserving disabled read-only runs.
    release_batch_size: int = 50
    selection_window_minutes: int = 30
    sms_template: str = field(default="", repr=False)

    def __post_init__(self):
        if type(self.selection_window_minutes) is not int or self.selection_window_minutes <= 0:
            raise WorkerConfigurationError("TESTER_SELECTION_WINDOW_MINUTES must be a positive integer.")
        try:
            datetime.now(timezone.utc) + self.selection_window
        except OverflowError:
            raise WorkerConfigurationError("TESTER_SELECTION_WINDOW_MINUTES exceeds the supported datetime range.") from None

    @property
    def selection_window(self) -> timedelta:
        return timedelta(minutes=self.selection_window_minutes)

    @classmethod
    def from_environment(cls, *, require_delivery: bool = False) -> WorkerSettings:
        load_dotenv(ENV_FILE, encoding="utf-8")
        try:
            environment = read_environment()
        except ValueError as error:
            raise WorkerConfigurationError(str(error)) from None
        collection = os.getenv("TESTER_ORDERS_COLLECTION", "preprocess_orders").strip()
        if not collection or "$" in collection or "\x00" in collection or collection in {NOTIFICATIONS, SELECTIONS, URLS}:
            raise WorkerConfigurationError("TESTER_ORDERS_COLLECTION must name the orders collection.")
        try:
            batch_size = int(os.getenv("TESTER_WORKER_BATCH_SIZE", "50"))
        except ValueError:
            raise WorkerConfigurationError("TESTER_WORKER_BATCH_SIZE must be a positive integer.") from None
        if batch_size <= 0:
            raise WorkerConfigurationError("TESTER_WORKER_BATCH_SIZE must be a positive integer.")
        enabled = os.getenv("TESTER_SMS_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false"}:
            raise WorkerConfigurationError("TESTER_SMS_ENABLED must be true or false.")
        release = os.getenv("TESTER_RELEASE_ENABLED", "").strip().lower()
        if release not in {"", "true", "false"}:
            raise WorkerConfigurationError("TESTER_RELEASE_ENABLED must be true or false, or unset.")
        retry_settings = {}
        for name, default in (("TESTER_SMS_RETRY_DELAY_MINUTES", "5"), ("TESTER_SMS_MAX_ATTEMPTS", "3"),
                              ("TESTER_RELEASE_BATCH_SIZE", "50"), ("TESTER_SELECTION_WINDOW_MINUTES", "30")):
            raw = os.getenv(name, default).strip()
            if not re.fullmatch(r"[0-9]+", raw):
                raise WorkerConfigurationError(f"{name} must be a positive integer.")
            try:
                retry_settings[name] = int(raw)
            except ValueError:
                raise WorkerConfigurationError(f"{name} must be a positive integer.") from None
            if retry_settings[name] <= 0:
                raise WorkerConfigurationError(f"{name} must be a positive integer.")
        settings = cls(
            orders_collection=collection,
            batch_size=batch_size,
            sms_enabled=enabled == "true",
            selection_base_url=environment.selection_base_url,
            token_secret=os.getenv("TESTER_TOKEN_SECRET", ""),
            retry_delay_minutes=retry_settings["TESTER_SMS_RETRY_DELAY_MINUTES"],
            max_attempts=retry_settings["TESTER_SMS_MAX_ATTEMPTS"],
            release_enabled=None if not release else release == "true",
            release_batch_size=retry_settings["TESTER_RELEASE_BATCH_SIZE"],
            selection_window_minutes=retry_settings["TESTER_SELECTION_WINDOW_MINUTES"],
            sms_template=os.getenv("TESTER_SMS_TEMPLATE", "").replace(r"\n", "\n"),
        )
        if require_delivery and settings.sms_enabled:
            settings.validate_delivery()
        return settings

    def validate_delivery(self) -> None:
        try:
            url = urlsplit(self.selection_base_url)
            valid_url = url.scheme in {"https", "http"} and url.hostname and url.path in {"", "/"} and not (
                url.username or url.password or url.query or url.fragment
            ) and not re.search(r"\s", self.selection_base_url) and (url.port is None or 1 <= url.port <= 65535)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise WorkerConfigurationError("TESTER_SELECTION_BASE_URL must be an HTTP(S) origin without a path, credentials, query or fragment.")
        if len(self.token_secret.encode("utf-8")) < 32 or not self.token_secret.strip():
            raise WorkerConfigurationError("TESTER_TOKEN_SECRET must be a stable random secret of at least 32 bytes.")
        validate_sms_template(self.sms_template)


def validate_sms_template(template: str) -> None:
    if not isinstance(template, str) or not template.strip():
        raise WorkerConfigurationError("TESTER_SMS_TEMPLATE must be a non-empty message template.")
    fields = set()
    try:
        parts = list(Formatter().parse(template))
    except ValueError:
        raise WorkerConfigurationError("TESTER_SMS_TEMPLATE has invalid placeholder syntax.") from None
    for _, name, spec, conversion in parts:
        if name is not None:
            if name not in {"full_name", "link"} or spec or conversion:
                raise WorkerConfigurationError("TESTER_SMS_TEMPLATE allows only {full_name} and {link}, without conversion or format specifiers.")
            fields.add(name)
    if fields != {"full_name", "link"}:
        raise WorkerConfigurationError("TESTER_SMS_TEMPLATE must include both {full_name} and {link} placeholders.")


def sms_full_name(order: dict) -> str:
    """Shared API/SMS customer name; invalid name parts never block an order."""
    parts = []
    for key in ("first_name", "last_name"):
        value = order.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if (not value or value.casefold() in {"none", "null", "undefined"}
                or not any(char.isalpha() for char in value)
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            continue
        parts.append(value)
    return " ".join(parts) or "مشتری"


def render_sms(settings: WorkerSettings, order: dict, link: str) -> str:
    validate_sms_template(settings.sms_template)
    return settings.sms_template.format(full_name=sms_full_name(order), link=link)


def selection_deadline(accepted_at: datetime, settings: WorkerSettings, selection: dict) -> datetime:
    # Server-created intent deadlines are immutable across configuration changes.
    frozen = selection.get("expires_at")
    if isinstance(frozen, datetime):
        return frozen.replace(tzinfo=timezone.utc) if frozen.tzinfo is None else frozen.astimezone(timezone.utc)
    return accepted_at + settings.selection_window


def order_filter(settings: WorkerSettings) -> dict:
    """Business eligibility: the exact scalar state pending, with simple collation."""
    # MongoDB equality alone would also admit an array containing "pending".
    return {"state": "pending", "$expr": {"$eq": [{"$type": "$state"}, "string"]}}


def paid_window_guard() -> dict:
    return {"$let": {"vars": {"paid_at": paid_at_expression()}, "in": {"$and": [
        {"$ne": ["$$paid_at", None]},
        {"$lt": ["$$NOW", {"$add": ["$$paid_at", 40 * 60 * 1000]}]},
    ]}}}


def sms_order_filter(settings: WorkerSettings) -> dict:
    """Business eligibility plus the operational duplicate-send guard."""
    match = order_filter(settings)
    match["$expr"] = {"$and": [match["$expr"], paid_window_guard()]}
    return {**match, "sms_sent": {"$ne": True}}


def eligible_pipeline(match: dict, limit: int, max_attempts: int = 3, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    return [
        {"$match": match},
        {"$sort": {"_id": 1}},
        {"$lookup": {"from": NOTIFICATIONS, "localField": "_id", "foreignField": "order_id", "as": "notification"}},
        {"$match": {"$or": [
            {"notification": {"$size": 0}},
            {"notification": {"$elemMatch": {
                "$or": [{"attempt_count": {"$exists": False}}, {"attempt_count": {"$lt": max_attempts}}],
                "status": "prepared",
            }}},
            {"notification": {"$elemMatch": {
                "status": "retry_pending", "attempt_count": {"$lt": max_attempts}, "next_retry_at": {"$lte": now},
            }}},
        ]}},
        {"$limit": limit},
        {"$project": {"_id": 1, ORDER_MOBILE_FIELD: 1}},
    ]


async def create_indexes(collections: dict) -> None:
    await collections["orders"].create_index(
        ORDER_INDEX_KEYS, name=ORDER_INDEX,
        collation=Collation(locale="simple"),
    )
    await create_paid_index(collections["orders"])
    await collections["notifications"].create_index("order_id", unique=True, name="tester_notification_order_unique")
    await collections["notifications"].create_index([("status", 1), ("lease_expires_at", 1)], name="tester_notification_recovery")
    await collections["notifications"].create_index([("status", 1), ("next_retry_at", 1)], name="tester_notification_retry")
    await collections["notifications"].create_index([("status", 1), ("order_synced", 1), ("accepted_at", 1)], name="tester_notification_repair")
    await collections["selections"].create_index("order_id", unique=True, name="tester_selection_order_unique")
    await collections["selections"].create_index("token_hash", unique=True, name="tester_selection_token_unique")
    await create_url_index(collections["urls"])


async def check_release_index(collections: dict) -> None:
    index = (await collections["notifications"].index_information()).get(RELEASE_SOURCE_INDEX, {})
    if (index.get("key") != RELEASE_SOURCE_INDEX_KEYS or index.get("partialFilterExpression") is not None
            or index.get("unique") or index.get("sparse")
            or index.get("collation", {}).get("locale", "simple") != "simple"):
        raise WorkerConfigurationError("Required release index is missing; run --create-indexes first.")


async def check_indexes(collections: dict, *, require_release: bool = True) -> None:
    if require_release:
        await check_release_index(collections)
    required = {
        "orders": [(False, ORDER_INDEX_KEYS)],
        "notifications": [
            (True, [("order_id", 1)]), (False, [("status", 1), ("lease_expires_at", 1)]),
            (False, [("status", 1), ("order_synced", 1), ("accepted_at", 1)]),
            (False, [("status", 1), ("next_retry_at", 1)]),
        ],
        "selections": [(True, [("order_id", 1)]), (True, [("token_hash", 1)])],
    }
    for collection, indexes in required.items():
        existing = await collections[collection].index_information()
        for unique, keys in indexes:
            if not any(
                list(index["key"]) == keys and bool(index.get("unique", False)) == unique
                and index.get("partialFilterExpression") is None
                and (collection != "orders" or name == ORDER_INDEX)
                and not index.get("sparse")
                and index.get("collation", {}).get("locale", "simple") == "simple"
                for name, index in existing.items()
            ):
                raise WorkerConfigurationError("Required indexes are missing; run --create-indexes first.")


def token_for_order(order_id, secret: str) -> str:
    identity = dumps(order_id, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret.encode("utf-8"), f"tester-selection:v{TOKEN_VERSION}:{identity}".encode("utf-8"), hashlib.sha256).digest()
    # Preserve the exact case of the first 18 Base64 URL-safe characters.
    return base64.urlsafe_b64encode(digest).decode("ascii")[:18]


def worker_collections(db, settings: WorkerSettings) -> dict:
    concern = WriteConcern(w="majority")
    return {
        "orders": db[settings.orders_collection].with_options(write_concern=concern),
        "notifications": db[NOTIFICATIONS].with_options(write_concern=concern),
        "selections": db[SELECTIONS].with_options(write_concern=concern),
        "urls": db[URLS].with_options(write_concern=concern),
    }


async def ensure_selection(selections, order_id, settings: WorkerSettings) -> str:
    token = token_for_order(order_id, settings.token_secret)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    try:
        await selections.update_one({"order_id": order_id}, {"$setOnInsert": {
            "order_id": order_id,
            "token_hash": token_hash,
            "token_version": TOKEN_VERSION,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }}, upsert=True)
    except DuplicateKeyError:
        # A simultaneous worker may have inserted the same order first.
        pass
    selection = await selections.find_one({"order_id": order_id})
    if not selection:
        raise WorkerConfigurationError("Selection token allocation failed; check unique token/order indexes for a collision.")
    if selection.get("token_version") != TOKEN_VERSION or not hmac.compare_digest(
        selection.get("token_hash", ""), token_hash,
    ):
        raise WorkerConfigurationError(
            "TESTER_TOKEN_SECRET is incompatible with the existing Selection token hash/version; restore the original secret."
        )
    return f"{settings.selection_base_url.rstrip('/')}/{token}"


async def prepare_notification(notifications, order_id):
    now = datetime.now(timezone.utc)
    try:
        await notifications.update_one({"order_id": order_id}, {"$setOnInsert": {
            "order_id": order_id, "status": "prepared", "created_at": now, "updated_at": now,
            "attempt_count": 0, "attempts": [], "next_retry_at": None, "last_attempt_at": None,
            "error_code": None, "last_error_code": None, "lease_expires_at": None, "attempt_id": None,
            "accepted_at": None, "order_synced": False,
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    return await notifications.find_one({"order_id": order_id})


async def claim_notification(notifications, order_id, timeout_seconds: float, max_attempts: int = 3):
    now = datetime.now(timezone.utc)
    attempt_id = uuid4().hex
    return await notifications.find_one_and_update(
        {"order_id": order_id,
         "$expr": {"$lt": [{"$ifNull": ["$attempt_count", 0]}, max_attempts]},
         "$or": [{"status": "prepared"}, {"status": "retry_pending", "next_retry_at": {"$lte": now}}]},
        [{"$set": {
            "status": "sending", "attempt_id": attempt_id,
            "attempt_count": {"$add": [{"$ifNull": ["$attempt_count", 0]}, 1]},
            "last_attempt_at": now, "sending_started_at": now, "updated_at": now, "next_retry_at": None,
            "lease_expires_at": now + timedelta(seconds=timeout_seconds + 300),
        }}, {"$set": {"attempts": {"$concatArrays": [{"$ifNull": ["$attempts", []]}, [{
            "attempt_number": "$attempt_count", "attempt_id": attempt_id,
            "started_at": now, "finished_at": None, "result": "sending", "http_status": None, "error_code": None,
        }]]}}}], return_document=ReturnDocument.AFTER,
    )


async def finish_notification(notifications, claim: dict, status: str, *, expired_at: datetime | None = None, **details) -> bool:
    now = datetime.now(timezone.utc)
    allowed = ["sending", "retry_pending", "exhausted"] if status == "accepted" else ["sending"]
    error_code = details.pop("error_code", None)
    history = {"$map": {
        "input": {"$ifNull": ["$attempts", []]}, "as": "attempt",
        "in": {"$cond": [{"$eq": ["$$attempt.attempt_id", claim["attempt_id"]]},
            {"$mergeObjects": ["$$attempt", {
                "finished_at": now, "result": status, "http_status": details.get("response_status_code"), "error_code": error_code,
            }]}, "$$attempt"]},
    }}
    match = {
        "order_id": claim["order_id"], "attempt_id": claim["attempt_id"],
        "status": {"$in": allowed},
    }
    if expired_at is not None:
        match["lease_expires_at"] = {"$lte": expired_at}
    result = await notifications.update_one(match, [{"$set": {"status": status, "updated_at": now,
                   "error_code": error_code, "last_error_code": error_code,
                   "lease_expires_at": None, "next_retry_at": None, "attempts": history, **details}}])
    return result.matched_count == 1


def submission_failure(error: SmsProviderError) -> str:
    status_code = error.status_code
    if status_code is not None and 400 <= status_code < 500 and status_code != 408:
        return "provider_rejected"
    if status_code is not None and 200 <= status_code < 300 and isinstance(error.body, dict) and error.body.get("status") == "error":
        return "provider_rejected"
    return "provider_outcome_unknown"


async def schedule_retry(notifications, claim: dict, settings: WorkerSettings, code: str,
                         status_code: int | None = None) -> str:
    status = "exhausted" if claim["attempt_count"] >= settings.max_attempts else "retry_pending"
    next_retry = None if status == "exhausted" else datetime.now(timezone.utc) + timedelta(minutes=settings.retry_delay_minutes)
    changed = await finish_notification(notifications, claim, status, error_code=code,
                                        response_status_code=status_code, next_retry_at=next_retry)
    if changed and status == "exhausted":
        logger.warning("SMS attempts exhausted; inspect the exhausted report.")
    return status if changed else "skipped"


async def sync_accepted_order(collections: dict, notification: dict) -> bool:
    # This is repair of a confirmed acceptance, not entry into the pending queue.
    result = await collections["orders"].update_one(
        {"_id": notification["order_id"]}, {"$set": {"sms_sent": True}},
        collation=Collation(locale="simple"),
    )
    if not result.matched_count:
        await collections["notifications"].update_one(
            {"order_id": notification["order_id"], "status": "accepted"},
            {"$set": {"order_sync_blocked": "order_missing_or_ineligible"}},
        )
        return False
    await collections["notifications"].update_one(
        {"order_id": notification["order_id"], "status": "accepted"},
        {"$set": {"order_synced": True, "order_synced_at": datetime.now(timezone.utc)}},
    )
    return True


def notification_scope_pipeline(collections: dict, match: dict, status_match: dict) -> list[dict]:
    return [
        {"$match": status_match},
        {"$lookup": {
            "from": collections["orders"].name, "localField": "order_id", "foreignField": "_id",
            "pipeline": [{"$match": match}, {"$project": {"_id": 1}}], "as": "_eligible_order",
        }},
        {"$match": {"_eligible_order.0": {"$exists": True}}},
        {"$project": {"_eligible_order": 0}},
    ]


async def recover_notifications(collections: dict, order_match: dict, limit: int,
                                settings: WorkerSettings) -> dict:
    counts = {"recovered_retry": 0, "repaired": 0, "repair_errors": 0}
    notifications = collections["notifications"]
    now = datetime.now(timezone.utc)
    criteria = {"$or": [
        {"status": "sending", "lease_expires_at": {"$lte": now}},
        {"status": {"$in": ["prepared", "retry_pending"]}, "attempt_count": {"$gte": settings.max_attempts}},
        {"status": "submission_unknown"},
        {"status": "failed", "error_code": {"$ne": "invalid_order_data"},
         "last_error_code": {"$ne": "invalid_order_data"}},
    ]}
    cursor = await notifications.aggregate(
        notification_scope_pipeline(collections, order_match, criteria) + [{"$limit": limit}],
        collation=Collation(locale="simple"),
    )
    async for notification in cursor:
        # Legacy sends predate attempt_count: conservatively count their one send.
        count = notification.get("attempt_count", 1)
        if notification["status"] == "sending":
            status = "exhausted" if count >= settings.max_attempts else "retry_pending"
            claim = {**notification, "attempt_count": count}
            if not notification.get("attempts"):
                # Only old-format sending documents need this compatibility step.
                history = [{
                    "attempt_number": count, "attempt_id": notification.get("attempt_id"),
                    "started_at": notification.get("sending_started_at", notification.get("created_at")),
                    "finished_at": None, "result": "sending", "http_status": None, "error_code": None,
                }]
                await notifications.update_one(
                    {"order_id": notification["order_id"], "status": "sending",
                     "attempt_id": notification.get("attempt_id"), "attempt_count": {"$exists": False},
                     "lease_expires_at": {"$lte": now}},
                    {"$set": {"attempt_count": count, "attempts": history}},
                )
            changed = await finish_notification(
                notifications, claim, status, expired_at=now, error_code="interrupted_submission",
                next_retry_at=now + timedelta(minutes=settings.retry_delay_minutes) if status == "retry_pending" else None,
            )
        else:
            status = "exhausted" if count >= settings.max_attempts else "retry_pending"
            last_at = notification.get("last_attempt_at") or notification.get("sending_started_at") or notification.get("updated_at") or now
            last_at = last_at.replace(tzinfo=timezone.utc) if last_at.tzinfo is None else last_at
            attempt_id = notification.get("attempt_id") or uuid4().hex
            code = notification.get("last_error_code") or notification.get("error_code") or "legacy_unconfirmed_submission"
            history = notification.get("attempts") or [{
                "attempt_number": count, "attempt_id": attempt_id, "started_at": last_at, "finished_at": last_at,
                "result": "unconfirmed", "http_status": notification.get("response_status_code"), "error_code": code,
            }]
            recovery_match = {
                "order_id": notification["order_id"], "status": notification["status"],
                "attempt_id": notification.get("attempt_id"),
            }
            if notification["status"] == "failed":
                recovery_match.update({"error_code": {"$ne": "invalid_order_data"},
                                       "last_error_code": {"$ne": "invalid_order_data"}})
            result = await notifications.update_one(recovery_match, {"$set": {
                "status": status, "attempt_count": count, "attempts": history, "attempt_id": attempt_id,
                "last_attempt_at": last_at, "error_code": code, "last_error_code": code, "lease_expires_at": None,
                "next_retry_at": last_at + timedelta(minutes=settings.retry_delay_minutes) if status == "retry_pending" else None,
                "updated_at": now,
            }})
            changed = bool(result.matched_count)
        counts["recovered_retry"] += int(changed)
    # An accepted send must still be synced after release or a production change.
    accepted_match = {}
    cursor = await notifications.aggregate(notification_scope_pipeline(collections, accepted_match, {
        "status": "accepted", "order_synced": {"$ne": True}, "order_sync_blocked": {"$exists": False},
    }) + [{"$limit": limit}], collation=Collation(locale="simple"))
    async for notification in cursor:
        try:
            if await sync_accepted_order(collections, notification):
                counts["repaired"] += 1
        except PyMongoError:
            counts["repair_errors"] += 1
            logger.warning("Accepted order flag repair failed; no SMS will be resubmitted.")
    return counts


async def exhausted_report(collections: dict, match: dict, limit: int) -> dict:
    match = {key: value for key, value in match.items() if key != "sms_sent"}
    pipeline = notification_scope_pipeline(collections, match, {"status": "exhausted"})
    totals = await collections["notifications"].aggregate(pipeline + [{"$count": "total"}])
    total = sum([item["total"] async for item in totals])
    if limit == 0:
        return {"exhausted_total": total, "orders": []}
    records = await collections["notifications"].aggregate(pipeline + [
        {"$limit": limit},
        {"$project": {"_id": 0, "order_id": 1, "attempt_count": 1, "last_attempt_at": 1, "last_error_code": 1}},
    ])
    return {"exhausted_total": total, "orders": [item async for item in records]}


async def send_with_lease(sms, recipient, text, notifications, claim):
    # Keep an active request leased even if reading a response takes longer than
    # one socket timeout. A dead process cannot renew; its lease will expire.
    task = asyncio.create_task(asyncio.to_thread(sms.send_simple, recipient=recipient, text=text))
    while True:
        done, _ = await asyncio.wait({task}, timeout=min(sms.config.timeout_seconds, 30))
        if done:
            return await task
        try:
            await notifications.update_one({
                "order_id": claim["order_id"], "attempt_id": claim["attempt_id"], "status": "sending",
            }, {"$set": {"lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=sms.config.timeout_seconds + 300)}})
        except PyMongoError:
            logger.warning("MongoDB lease renewal failed; request result will remain fenced by attempt id.")


async def process_order(order: dict, collections: dict, settings: WorkerSettings, sms: FarazSmsClient,
                        order_match: dict, shortener: ZayaShortener | None = None) -> tuple[str, int | None]:
    current = await collections["orders"].find_one(
        {**order_match, "_id": order["_id"]}, collation=Collation(locale="simple"),
    )
    if not current:
        return "skipped", None
    order = current
    notifications = collections["notifications"]
    notification = await prepare_notification(notifications, order["_id"])
    if not notification or notification["status"] not in {"prepared", "retry_pending"}:
        return "skipped", None
    try:
        recipient = normalize_recipient(order.get(ORDER_MOBILE_FIELD))
        link = await ensure_selection(collections["selections"], order["_id"], settings)
        if shortener is not None:
            try:
                link = await shortener.resolve(order["_id"], link, previous_attempts=notification.get("attempt_count", 0))
            except ZayaConfigurationError as error:
                raise WorkerConfigurationError(str(error)) from None
            if link is None:
                return "skipped", None
        text = render_sms(settings, order, link)
        sms.build_simple_payload(recipient=recipient, text=text)
    except (SmsValidationError, SmsConfigurationError):
        result = await notifications.update_one({
            "order_id": order["_id"], "status": notification["status"], "attempt_id": notification.get("attempt_id"),
        }, {"$set": {"status": "failed", "error_code": "invalid_order_data",
                    "last_error_code": "invalid_order_data", "updated_at": datetime.now(timezone.utc)}})
        return ("failed" if result.matched_count else "skipped"), None
    # Selection preparation can await MongoDB; recheck immediately before Claim
    # so an order leaving pending consumes no send attempt.
    if await collections["orders"].find_one(
        {**order_match, "_id": order["_id"]}, projection={"_id": 1}, collation=Collation(locale="simple"),
    ) is None:
        return "skipped", None
    claim = await claim_notification(notifications, order["_id"], sms.config.timeout_seconds, settings.max_attempts)
    if not claim:
        return "skipped", None
    # Claiming uses a separate document and can take time. Recheck the order,
    # including MongoDB's clock and payment deadline, immediately before dispatch.
    if await collections["orders"].find_one(
        {**order_match, "_id": order["_id"]}, projection={"_id": 1}, collation=Collation(locale="simple"),
    ) is None:
        await schedule_retry(notifications, claim, settings, "order_no_longer_eligible")
        return "skipped", None
    try:
        response = await send_with_lease(sms, recipient, text, notifications, claim)
    except SmsProviderError as error:
        stop_code = error.status_code if error.status_code in {401, 403, 429} else None
        try:
            status = await schedule_retry(notifications, claim, settings, submission_failure(error), error.status_code)
        except PyMongoError:
            if stop_code is None:
                raise
            status = "database_errors"
        if stop_code:
            logger.warning("Faraz returned HTTP %s; stopping this run with future retry preserved.", stop_code)
        return status, stop_code
    except (SmsValidationError, SmsConfigurationError):
        await finish_notification(notifications, claim, "failed", error_code="invalid_order_data")
        return "failed", None
    except Exception:
        return await schedule_retry(notifications, claim, settings, "unexpected_submission_error"), None
    accepted = await finish_notification(
        notifications, claim, "accepted", accepted_at=datetime.now(timezone.utc),
        response_status_code=response.status_code, provider_data=response.data,
    )
    if not accepted:
        # A newer attempt owns the notification: never overwrite it or sync the order.
        return "skipped", None
    try:
        await sync_accepted_order(collections, claim)
    except PyMongoError:
        logger.warning("SMS accepted; order flag update is pending a later repair.")
    return "accepted", None

def release_order_filter(settings: WorkerSettings) -> dict:
    return order_filter(settings)


def valid_release_selection(selection: dict, order: dict, accepted_at: datetime, deadline: datetime) -> bool:
    """Prove the order was written on time, independently of API finalization."""
    from fastapi import HTTPException
    from app.selections import ORDER_WRITTEN_AT, owns_order, utc_date, validate_pids, written_in_time

    status = selection.get("status")
    if status not in ("submitting", "review_required", "submitted"):
        return False
    try:
        validate_pids(selection.get("selected_pids"))
    except HTTPException:
        return False
    frozen = utc_date(selection.get("expires_at"))
    window = {"accepted_at": accepted_at, "expires_at": min(deadline, frozen) if frozen else deadline}
    return (owns_order(selection, order) and written_in_time(selection, order, window)
            and (status != "submitted" or utc_date(selection.get("submitted_at")) == utc_date(order.get(ORDER_WRITTEN_AT))))


def release_pipeline(settings: WorkerSettings, *, after: dict | None = None, limit: int | None = None) -> list[dict]:
    # Lazy import avoids the existing selections -> worker import cycle and reuses
    # the receipt field names/window rather than defining another selection model.
    from app.selections import COUNT, ORDER_MARKER, ORDER_WRITTEN_AT

    match = release_order_filter(settings)
    if after is not None:
        match["_id"] = {"$gt": after["_id"]}
    # Validate before LIMIT: malformed or inconsistent old receipts cannot starve
    # later ready orders. This is a query predicate, not a collection validator.
    strings = {"bsonType": "array", "minItems": COUNT, "maxItems": COUNT,
               "items": {"bsonType": "string", "pattern": r"(*UCP)[^\s\x1c-\x1f]"}}
    receipt_schema = {
        "required": ["submission_id", "selected_pids", "selected_names", "submitting_at"],
        "properties": {
            "submission_id": {"bsonType": "string", "minLength": 32, "maxLength": 32, "pattern": "^[a-f0-9]{32}$"},
            "selected_pids": {**strings, "uniqueItems": True, "items": {
                "bsonType": "string", "minLength": 1, "maxLength": 128,
                "not": {"pattern": r"(*UCP)(?:^\s|\s$|[\x00-\x1f])"},
            }}, "selected_names": strings,
            "submitting_at": {"bsonType": "date"},
        },
    }
    submitted_schema = {
        "required": ["submitted_at"], "properties": {"submitted_at": {"bsonType": "date"}},
    }
    effective_deadline = {"$cond": [
        {"$eq": [{"$type": "$_selection.expires_at"}, "date"]},
        "$_selection.expires_at", {"$add": ["$_accepted_at", int(settings.selection_window.total_seconds() * 1000)]},
    ]}
    return [
        # Start from accepted notifications using the existing status-prefix index.
        # Missing/failed notifications never cause an order or receipt lookup.
        {"$match": {"status": "accepted", "$expr": {"$and": [
            {"$eq": [{"$type": "$accepted_at"}, "date"]}, {"$lte": ["$accepted_at", "$$NOW"]},
            {"$eq": ["$status", "accepted"]},
        ]}}},
        # Keep this an equality lookup; apply the full order filter immediately
        # afterwards instead of running a validation sub-pipeline for every join.
        {"$lookup": {"from": settings.orders_collection, "localField": "order_id", "foreignField": "_id",
                     "as": "_order"}},
        {"$match": {"_order.0": {"$exists": True}}},
        {"$replaceWith": {"$mergeObjects": [{"$arrayElemAt": ["$_order", 0]}, {"_accepted_at": "$accepted_at"}]}},
        {"$match": match},
        {"$lookup": {"from": SELECTIONS, "localField": "_id", "foreignField": "order_id",
                     "pipeline": [{"$project": {key: 1 for key in (
                                      "status", "submission_id", "selected_pids", "selected_names", "submitting_at", "submitted_at", "expires_at",
                                  )}}], "as": "_submitted"}},
        {"$set": {"_selection": {"$ifNull": [{"$arrayElemAt": ["$_submitted", 0]}, {}]}}},
        {"$set": {"_deadline": effective_deadline}},
        {"$set": {"_expired": {"$gte": ["$$NOW", "$_deadline"]}}},
        # Inspect the frozen deadline before excluding unexpired empty orders.
        {"$match": {"$or": [{"_expired": True}, {"$expr": {"$and": [
            {"$eq": [{"$type": "$recommended_products"}, "array"]},
            {"$eq": [{"$type": f"${ORDER_MARKER}"}, "string"]},
            {"$eq": [{"$type": f"${ORDER_WRITTEN_AT}"}, "date"]},
        ]}}]}},
        {"$set": {"_valid_receipt": {"$and": [
            {"$or": [
                {"$eq": ["$_selection.status", "submitting"]},
                {"$eq": ["$_selection.status", "review_required"]},
                {"$and": [{"$eq": ["$_selection.status", "submitted"]},
                          {"$eq": [f"${ORDER_WRITTEN_AT}", "$_selection.submitted_at"]}]},
            ]},
            {"$eq": [f"${ORDER_MARKER}", "$_selection.submission_id"]},
            {"$eq": ["$recommended_products", "$_selection.selected_names"]},
            {"$eq": [{"$type": f"${ORDER_WRITTEN_AT}"}, "date"]},
            {"$gte": ["$_selection.submitting_at", "$_accepted_at"]},
            {"$lte": ["$_selection.submitting_at", f"${ORDER_WRITTEN_AT}"]},
            {"$lt": [f"${ORDER_WRITTEN_AT}", "$_deadline"]},
        ]}}},
        {"$match": {"$or": [{"_expired": True}, {
            "_valid_receipt": True, "$jsonSchema": {"properties": {"_selection": receipt_schema}},
            "$or": [{"_selection.status": {"$in": ["submitting", "review_required"]}},
                    {"_selection.status": "submitted", "$jsonSchema": {"properties": {"_selection": submitted_schema}}}],
        }]}},
        {"$sort": {"_id": 1}},
        {"$limit": settings.release_batch_size if limit is None else limit},
        {"$project": {"_id": 1, "_accepted_at": 1, "_deadline": 1}},
    ]


async def release_resolved_tester_orders(collections: dict, settings: WorkerSettings, *, dry_run: bool = False) -> dict:
    from app.selections import ORDER_MARKER, ORDER_WRITTEN_AT, utc_date

    summary = {"released_submitted": 0, "released_expired": 0, "release_ready": 0,
               "release_skipped": 0, "release_errors": 0}
    if type(settings.release_batch_size) is not int or settings.release_batch_size <= 0:
        raise WorkerConfigurationError("TESTER_RELEASE_BATCH_SIZE must be a positive integer.")
    # Proof reads must not mix lagging replicas; other worker/API reads are unchanged.
    orders = collections["orders"].with_options(read_preference=ReadPreference.PRIMARY)
    selections = collections["selections"].with_options(read_preference=ReadPreference.PRIMARY)
    notifications = collections["notifications"].with_options(read_preference=ReadPreference.PRIMARY)
    after = None
    completed = 0
    try:
        await check_release_index(collections)
        while completed < settings.release_batch_size:
            # Keyset pages bypass failed/racing candidates within this run too.
            # Never let a permanently failing first order monopolize a small batch.
            remaining = settings.release_batch_size - completed
            cursor = await notifications.aggregate(
                release_pipeline(settings, after=after, limit=remaining), batchSize=remaining,
                collation=Collation(locale="simple"), hint=RELEASE_SOURCE_INDEX,
            )
            page_count = 0
            async for candidate in cursor:
                page_count += 1
                after = candidate
                summary["release_ready"] += 1
                try:
                    accepted_at, deadline = utc_date(candidate["_accepted_at"]), utc_date(candidate["_deadline"])
                    proof_fields = ("recommended_products", ORDER_MARKER, ORDER_WRITTEN_AT)
                    for _ in range(RELEASE_DECISION_ATTEMPTS):
                        # Read the order FIRST: its write follows the immutable
                        # Selection intent. Reading Selection first could pair a
                        # stale pending intent with newly committed order choices.
                        current = await orders.find_one(
                            {**release_order_filter(settings), "_id": candidate["_id"]},
                            projection={**{key: 1 for key in proof_fields}, "_release_now": "$$NOW"},
                            collation=Collation(locale="simple"),
                        )
                        if current is None:
                            summary["release_skipped"] += 1
                            break
                        now = utc_date(current.pop("_release_now"))
                        selection = await selections.find_one(
                            {"order_id": candidate["_id"]}, collation=Collation(locale="simple"),
                        ) or {}
                        deadline = selection_deadline(accepted_at, settings, selection)
                        valid = valid_release_selection(selection, current, accepted_at, deadline)
                        if now < accepted_at or (not valid and now < deadline):
                            summary["release_skipped"] += 1
                            break
                        if dry_run:
                            completed += 1
                            break
                        match = release_order_filter(settings)
                        exact_state = match.pop("$expr")
                        # BOTH paths compare the full proof snapshot, including
                        # missing vs null. A concurrent order write cannot turn
                        # an expired decision into an incorrect state-only release.
                        proof = [{"$eq": [f"${key}", {"$literal": current[key]}]}
                                 for key in proof_fields if key in current]
                        reason = "submitted" if valid else "expired"
                        guard = {"$gte": ["$$NOW", accepted_at if valid else deadline]}
                        result = await orders.update_one({
                            **match, "_id": candidate["_id"],
                            "$and": [{key: {"$exists": key in current}} for key in proof_fields],
                            "$expr": {"$and": [exact_state, *proof, guard]},
                        }, {"$set": {"state": "not_seen", **({"tester_gender": "تستر انتخابی"} if valid else {})}},
                            collation=Collation(locale="simple"))
                        if result.modified_count:
                            summary[f"released_{reason}"] += 1
                            completed += 1
                            break
                    else:
                        remaining_order = await orders.find_one(
                            {**release_order_filter(settings), "_id": candidate["_id"]},
                            projection={"_id": 1}, collation=Collation(locale="simple"),
                        )
                        if remaining_order is None:
                            summary["release_skipped"] += 1
                        else:
                            summary["release_errors"] += 1
                            logger.warning("Tester release evidence kept changing; deferring release to a later run.")
                except PyMongoError:
                    summary["release_errors"] += 1
                    logger.warning("Tester order release failed; a later run can retry.")
            if page_count < remaining:
                break
    except PyMongoError:
        summary["release_errors"] += 1
        logger.warning("Tester release query unavailable; SMS processing can continue.")
    return summary


async def release_paid_timeout_orders(collections: dict, settings: WorkerSettings, *, dry_run: bool = False) -> dict:
    return await release_paid_orders(collections["orders"], limit=settings.release_batch_size, dry_run=dry_run)


async def run_once(db, settings: WorkerSettings, *, limit: int | None = None,
                   dry_run: bool = False, sms: FarazSmsClient | None = None) -> dict:
    limit = settings.batch_size if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise WorkerConfigurationError("--limit must be a positive integer.")
    collections = worker_collections(db, settings)
    match = sms_order_filter(settings)
    readonly = dry_run or not settings.sms_enabled
    shortener = None
    release_enabled = settings.sms_enabled if settings.release_enabled is None else settings.release_enabled
    summary = {"eligible": 0, "accepted": 0, "failed": 0, "retry_pending": 0, "exhausted": 0, "skipped": 0, "database_errors": 0}
    summary.update({"released_submitted": 0, "released_expired": 0, "release_ready": 0,
                    "release_skipped": 0, "release_errors": 0, "release_enabled": release_enabled})
    if release_enabled:
        summary.update(await release_resolved_tester_orders(collections, settings, dry_run=dry_run))
    if not readonly:
        settings.validate_delivery()
        sms = sms or FarazSmsClient()
        if not sms.config.api_key or not sms.config.base_url or not sms.config.line_number:
            raise WorkerConfigurationError("FARAZ_API_KEY, FARAZ_API_URL and FARAZ_LINE_NUMBER are required for sending.")
        try:
            provider_url = urlsplit(sms.config.base_url)
            valid_url = provider_url.scheme in {"http", "https"} and provider_url.hostname and not (
                provider_url.username or provider_url.password or provider_url.query or provider_url.fragment
            ) and not re.search(r"\s", sms.config.base_url) and (provider_url.port is None or 1 <= provider_url.port <= 65535)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise WorkerConfigurationError("FARAZ_API_URL must be a valid HTTP(S) base URL.")
        # Validate the Simple SMS payload without generating a token or sending.
        sms.build_simple_payload(recipient="09120000000", text=render_sms(settings, {}, "https://validation.invalid"))
        await check_indexes(collections, require_release=False)
        try:
            zaya = ZayaConfig.from_environment()
            if zaya is not None:
                await check_url_index(collections["urls"])
                shortener = ZayaShortener(collections["urls"], zaya)
        except ZayaConfigurationError as error:
            raise WorkerConfigurationError(str(error)) from None
        summary.update(await recover_notifications(collections, match, limit, settings))
    cursor = await collections["orders"].aggregate(
        eligible_pipeline(match, limit, settings.max_attempts), batchSize=min(limit, settings.batch_size),
        allowDiskUse=True, collation=Collation(locale="simple"),
        hint=ORDER_INDEX,
    )
    async for order in cursor:
        summary["eligible"] += 1
        if readonly:
            continue
        try:
            status, stop_code = await process_order(order, collections, settings, sms, match, shortener)
            summary[status] += 1
            if stop_code:
                summary["halted_http_status"] = stop_code
                break
        except PyMongoError:
            # If dispatch already began, a durable sending claim remains fenced.
            summary["database_errors"] += 1
            logger.warning("Order database operation failed; unfinished attempts will recover after lease expiry.")
    summary["exhausted_total"] = (await exhausted_report(collections, order_filter(settings), 0))["exhausted_total"]
    summary["read_only"] = readonly and (dry_run or not release_enabled)
    return summary


async def run_command(args) -> dict:
    if (getattr(args, "release_paid_timeout", False) or getattr(args, "create_paid_index", False)
            or getattr(args, "backfill_paid_dates", False)):
        return await run_paid_command(args)
    # Delivery validation belongs after release, so broken SMS credentials cannot
    # prevent already-resolved orders from leaving the selection waiting state.
    settings = WorkerSettings.from_environment()
    # Reuse the application's MongoDB settings without changing its lifespan.
    try:
        from app.config import MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
    except (KeyError, ValueError) as error:
        raise WorkerConfigurationError(f"Invalid MongoDB configuration: {error}") from None

    client = AsyncMongoClient(
        MONGODB_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS, timeoutMS=MONGODB_TIMEOUT_MS, tz_aware=True,
    )
    try:
        await client.admin.command("ping")
        db = client[MONGODB_DB_NAME]
        if args.create_indexes:
            await create_indexes(worker_collections(db, settings))
            return {"indexes": "ready"}
        if getattr(args, "report_exhausted", False):
            return await exhausted_report(worker_collections(db, settings), order_filter(settings), args.limit or settings.batch_size)
        return await run_once(db, settings, limit=args.limit, dry_run=args.dry_run)
    finally:
        await client.close()


async def run_paid_command(args) -> dict:
    """Only database settings are parsed; unrelated delivery settings are ignored."""
    load_dotenv(ENV_FILE, encoding="utf-8")
    uri = os.getenv("MONGODB_URI", "")
    try:
        database = read_environment().mongodb_db_name
    except ValueError as error:
        raise WorkerConfigurationError(str(error)) from None
    collection = os.getenv("TESTER_ORDERS_COLLECTION", "preprocess_orders").strip()
    try:
        timeout = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))
    except ValueError:
        raise WorkerConfigurationError("MONGODB_TIMEOUT_MS must be a positive integer.") from None
    if (not uri or not database or not collection or "$" in collection or "\x00" in collection or timeout <= 0
            or collection in {NOTIFICATIONS, SELECTIONS, URLS}):
        raise WorkerConfigurationError("Valid MongoDB settings and an orders collection are required.")
    raw_limit = os.getenv("TESTER_PAID_RELEASE_BATCH_SIZE", "50").strip()
    if not re.fullmatch(r"[0-9]+", raw_limit) or len(raw_limit) > 9 or int(raw_limit) <= 0:
        raise WorkerConfigurationError("TESTER_PAID_RELEASE_BATCH_SIZE must be a positive integer.")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=timeout, timeoutMS=timeout, tz_aware=True)
    try:
        await client.admin.command("ping")
        orders = client[database][collection].with_options(write_concern=WriteConcern(w="majority"))
        if args.create_paid_index:
            await create_paid_index(orders)
            return {"paid_index": "ready"}
        try:
            return await release_paid_orders(orders, limit=args.limit or int(raw_limit), dry_run=args.dry_run,
                                             normalize_only=getattr(args, "backfill_paid_dates", False))
        except ValueError as error:
            raise WorkerConfigurationError(str(error)) from None
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a bounded batch of tester orders; one run then exit.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--create-indexes", action="store_true")
    mode.add_argument("--report-exhausted", action="store_true")
    mode.add_argument("--release-paid-timeout", action="store_true")
    mode.add_argument("--create-paid-index", action="store_true")
    mode.add_argument("--backfill-paid-dates", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not (args.once or args.dry_run or args.create_indexes or args.report_exhausted or args.release_paid_timeout
            or args.create_paid_index or args.backfill_paid_dates):
        parser.error("Choose --once, --dry-run, --create-indexes, --report-exhausted, --release-paid-timeout, --create-paid-index or --backfill-paid-dates.")
    if (args.create_indexes or args.create_paid_index) and (args.dry_run or args.limit is not None):
        parser.error("--create-indexes cannot be combined with --dry-run or --limit.")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive.")
    try:
        summary = asyncio.run(run_command(args))
        print(json.dumps(summary, sort_keys=True, default=str))
        release_failed = summary.get("release_errors", 0) > 0 or summary.get("paid_timeout_errors", 0) > 0
        if release_failed:
            logger.error("Tester order release finished with %s error(s); exiting unsuccessfully.",
                         summary.get("release_errors", 0) + summary.get("paid_timeout_errors", 0))
        return 1 if release_failed or summary.get("halted_http_status") else 0
    except (WorkerConfigurationError, SmsConfigurationError) as error:
        # Our configuration errors never include setting values or credentials.
        print(f"Worker configuration error: {error}", file=sys.stderr)
        return 1
    except PyMongoError:
        print("Worker MongoDB operation failed; a later run can resume safely.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
