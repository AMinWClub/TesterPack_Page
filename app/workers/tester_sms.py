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
from urllib.parse import urlsplit
from uuid import uuid4

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps
from dotenv import load_dotenv
from pymongo import ReturnDocument
from pymongo import AsyncMongoClient
from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from app.services.sms import (
    FarazSmsClient,
    SmsConfigurationError,
    SmsProviderError,
    SmsValidationError,
)
from app.services.sms.faraz import normalize_recipient

NOTIFICATIONS = "tester_sms_notifications"
SELECTIONS = "tester_selections"
TOKEN_VERSION = 1
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
logger = logging.getLogger(__name__)
ORDER_DATE_FIELD = "date_created"
ORDER_MOBILE_FIELD = "phone"
UTC_DATE_PATTERN = r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?Z$"
ORDER_INDEX = "tester_product_created"


class WorkerConfigurationError(ValueError):
    """Worker settings or the order schema need attention."""


@dataclass(frozen=True)
class WorkerSettings:
    orders_collection: str
    product_id: str
    start_at: datetime
    batch_size: int = 50
    sms_enabled: bool = False
    selection_base_url: str = ""
    link_attribute: str = "link"
    token_secret: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, *, require_delivery: bool = False) -> WorkerSettings:
        load_dotenv(ENV_FILE, encoding="utf-8")
        collection = os.getenv("TESTER_ORDERS_COLLECTION", "preprocess_orders").strip()
        product_id = os.getenv("TESTER_PACK_PRODUCT_ID", "").strip()
        if not collection or "$" in collection or "\x00" in collection or collection in {NOTIFICATIONS, SELECTIONS}:
            raise WorkerConfigurationError("TESTER_ORDERS_COLLECTION must name the orders collection.")
        if not product_id:
            raise WorkerConfigurationError("TESTER_PACK_PRODUCT_ID is required.")
        try:
            start_at = datetime.fromisoformat(os.getenv("TESTER_SMS_START_AT", ""))
        except ValueError:
            raise WorkerConfigurationError("TESTER_SMS_START_AT must be an ISO 8601 date with timezone.") from None
        if start_at.tzinfo is None or start_at.utcoffset() is None:
            raise WorkerConfigurationError("TESTER_SMS_START_AT must include a timezone.")
        try:
            batch_size = int(os.getenv("TESTER_WORKER_BATCH_SIZE", "50"))
        except ValueError:
            raise WorkerConfigurationError("TESTER_WORKER_BATCH_SIZE must be a positive integer.") from None
        if batch_size <= 0:
            raise WorkerConfigurationError("TESTER_WORKER_BATCH_SIZE must be a positive integer.")
        enabled = os.getenv("TESTER_SMS_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false"}:
            raise WorkerConfigurationError("TESTER_SMS_ENABLED must be true or false.")
        settings = cls(
            orders_collection=collection,
            product_id=product_id,
            start_at=start_at.astimezone(timezone.utc),
            batch_size=batch_size,
            sms_enabled=enabled == "true",
            selection_base_url=os.getenv("TESTER_SELECTION_BASE_URL", "").strip().rstrip("/"),
            link_attribute=os.getenv("TESTER_PATTERN_LINK_ATTRIBUTE", "link").strip(),
            token_secret=os.getenv("TESTER_TOKEN_SECRET", ""),
        )
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", settings.link_attribute):
            raise WorkerConfigurationError("TESTER_PATTERN_LINK_ATTRIBUTE must be a valid pattern variable name.")
        if require_delivery and settings.sms_enabled:
            settings.validate_delivery()
        return settings

    def validate_delivery(self) -> None:
        try:
            url = urlsplit(self.selection_base_url)
            valid_url = url.scheme in {"https", "http"} and url.hostname and not (
                url.username or url.password or url.query or url.fragment
            ) and not re.search(r"\s", self.selection_base_url) and (url.port is None or 1 <= url.port <= 65535)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise WorkerConfigurationError("TESTER_SELECTION_BASE_URL must be an HTTP(S) base URL without credentials or query.")
        if len(self.token_secret.encode("utf-8")) < 32 or not self.token_secret.strip():
            raise WorkerConfigurationError("TESTER_TOKEN_SECRET must be a stable random secret of at least 32 bytes.")


def product_id_variants(product_id: str) -> list[str | int]:
    """Match the real array element, allowing canonical numeric strings and ints."""
    variants: list[str | int] = [product_id]
    if re.fullmatch(r"0|[1-9][0-9]*", product_id):
        number = int(product_id)
        if number <= 2**63 - 1:
            variants.append(number)
    return variants


def order_filter(settings: WorkerSettings) -> dict:
    """Use the supplied schema: product_id array, UTC date_created string.

    The indexed range is only a coarse seconds bound. The expression pads the
    fractional part to nine digits before the exact comparison, so variable
    precision (including timestamps without a fraction) remains correct.
    Calendar validation parses seconds only, avoiding parser limits on fractions.
    """
    start = settings.start_at.astimezone(timezone.utc)
    prefix = start.strftime("%Y-%m-%dT%H:%M:%S")
    threshold = f"{prefix}.{start.microsecond:06d}000Z"
    seconds = {"$arrayElemAt": ["$$parts.captures", 0]}
    fraction = {"$ifNull": [{"$arrayElemAt": ["$$parts.captures", 1]}, ""]}
    normalized = {"$concat": [seconds, ".", {"$substrBytes": [{"$concat": [fraction, "000000000"]}, 0, 9]}, "Z"]}
    valid_date = {"$dateFromString": {
        "dateString": {"$concat": [seconds, "Z"]}, "format": "%Y-%m-%dT%H:%M:%SZ",
        "onError": None, "onNull": None,
    }}
    return {
        "product_id": {"$elemMatch": {"$in": product_id_variants(settings.product_id)}},
        ORDER_DATE_FIELD: {"$type": "string", "$gte": prefix},
        "sms_sent": {"$ne": True},
        "$expr": {"$let": {
            "vars": {"parts": {"$regexFind": {
                "input": {"$cond": [{"$eq": [{"$type": f"${ORDER_DATE_FIELD}"}, "string"]}, f"${ORDER_DATE_FIELD}", ""]},
                "regex": UTC_DATE_PATTERN,
            }}},
            "in": {"$cond": [{"$eq": ["$$parts", None]}, False, {"$and": [
                {"$ne": [valid_date, None]}, {"$gte": [normalized, threshold]},
            ]}]},
        }},
    }


def eligible_pipeline(match: dict, limit: int) -> list[dict]:
    return [
        {"$match": match},
        {"$sort": {ORDER_DATE_FIELD: 1, "_id": 1}},
        {"$lookup": {"from": NOTIFICATIONS, "localField": "_id", "foreignField": "order_id", "as": "notification"}},
        {"$match": {"$or": [{"notification": {"$size": 0}}, {"notification.status": "prepared"}]}},
        {"$limit": limit},
        {"$project": {"_id": 1, ORDER_MOBILE_FIELD: 1}},
    ]


async def create_indexes(collections: dict) -> None:
    await collections["orders"].create_index(
        [("product_id", 1), (ORDER_DATE_FIELD, 1), ("_id", 1)], name=ORDER_INDEX,
        collation=Collation(locale="simple"),
    )
    await collections["notifications"].create_index("order_id", unique=True, name="tester_notification_order_unique")
    await collections["notifications"].create_index([("status", 1), ("lease_expires_at", 1)], name="tester_notification_recovery")
    await collections["notifications"].create_index([("status", 1), ("order_synced", 1), ("accepted_at", 1)], name="tester_notification_repair")
    await collections["selections"].create_index("order_id", unique=True, name="tester_selection_order_unique")
    await collections["selections"].create_index("token_hash", unique=True, name="tester_selection_token_unique")


async def check_indexes(collections: dict) -> None:
    required = {
        "orders": [(False, [("product_id", 1), (ORDER_DATE_FIELD, 1), ("_id", 1)])],
        "notifications": [
            (True, [("order_id", 1)]), (False, [("status", 1), ("lease_expires_at", 1)]),
            (False, [("status", 1), ("order_synced", 1), ("accepted_at", 1)]),
        ],
        "selections": [(True, [("order_id", 1)]), (True, [("token_hash", 1)])],
    }
    for collection, indexes in required.items():
        existing = await collections[collection].index_information()
        for unique, keys in indexes:
            if not any(
                list(index["key"]) == keys and bool(index.get("unique", False)) == unique
                and not index.get("partialFilterExpression") and not index.get("sparse")
                and index.get("collation", {}).get("locale", "simple") == "simple"
                for index in existing.values()
            ):
                raise WorkerConfigurationError("Required indexes are missing; run --create-indexes first.")


def token_for_order(order_id, secret: str) -> str:
    identity = dumps(order_id, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret.encode("utf-8"), f"tester-selection:v{TOKEN_VERSION}:{identity}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode("ascii").rstrip("=")


def worker_collections(db, settings: WorkerSettings) -> dict:
    concern = WriteConcern(w="majority")
    return {
        "orders": db[settings.orders_collection].with_options(write_concern=concern),
        "notifications": db[NOTIFICATIONS].with_options(write_concern=concern),
        "selections": db[SELECTIONS].with_options(write_concern=concern),
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
    if not selection or selection.get("token_version") != TOKEN_VERSION or not hmac.compare_digest(
        selection.get("token_hash", ""), token_hash,
    ):
        raise SmsValidationError("Selection token configuration does not match the stored selection.")
    return f"{settings.selection_base_url}/{token}"


async def prepare_notification(notifications, order_id):
    now = datetime.now(timezone.utc)
    try:
        await notifications.update_one({"order_id": order_id}, {"$setOnInsert": {
            "order_id": order_id, "status": "prepared", "created_at": now, "updated_at": now,
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    return await notifications.find_one({"order_id": order_id})


async def claim_notification(notifications, order_id, timeout_seconds: float):
    now = datetime.now(timezone.utc)
    return await notifications.find_one_and_update(
        {"order_id": order_id, "status": "prepared"},
        {"$set": {
            "status": "sending", "attempt_id": uuid4().hex,
            "sending_started_at": now, "updated_at": now,
            "lease_expires_at": now + timedelta(seconds=timeout_seconds + 300),
        }}, return_document=ReturnDocument.AFTER,
    )


async def finish_notification(notifications, claim: dict, status: str, **details) -> bool:
    result = await notifications.update_one({
        "order_id": claim["order_id"], "attempt_id": claim["attempt_id"],
        "status": {"$in": ["sending", "submission_unknown"]},
    }, {"$set": {"status": status, "updated_at": datetime.now(timezone.utc), **details}})
    return result.matched_count == 1


def submission_failure(error: SmsProviderError) -> tuple[str, str]:
    status_code = error.status_code
    if status_code is not None and 400 <= status_code < 500 and status_code != 408:
        return "failed", "provider_rejected"
    if status_code is not None and 200 <= status_code < 300 and isinstance(error.body, dict) and error.body.get("status") == "error":
        return "failed", "provider_rejected"
    return "submission_unknown", "provider_outcome_unknown"


async def sync_accepted_order(collections: dict, notification: dict, order_filter: dict) -> bool:
    # Keep the product/date guard during repair: never modify historical orders.
    match = {key: value for key, value in order_filter.items() if key != "sms_sent"}
    result = await collections["orders"].update_one(
        {**match, "_id": notification["order_id"]}, {"$set": {"sms_sent": True}},
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


async def recover_notifications(collections: dict, order_filter: dict, limit: int) -> dict:
    counts = {"recovered_unknown": 0, "repaired": 0, "repair_errors": 0}
    notifications = collections["notifications"]
    now = datetime.now(timezone.utc)
    cursor = notifications.find({"status": "sending", "lease_expires_at": {"$lte": now}}).sort("lease_expires_at", 1).limit(limit)
    async for notification in cursor:
        result = await notifications.update_one({
            "order_id": notification["order_id"], "status": "sending", "lease_expires_at": {"$lte": now},
        }, {"$set": {"status": "submission_unknown", "error_code": "interrupted_submission", "updated_at": now}})
        counts["recovered_unknown"] += result.modified_count
    cursor = notifications.find({
        "status": "accepted", "order_synced": {"$ne": True}, "order_sync_blocked": {"$exists": False},
    }).sort("accepted_at", 1).limit(limit)
    async for notification in cursor:
        try:
            if await sync_accepted_order(collections, notification, order_filter):
                counts["repaired"] += 1
        except PyMongoError:
            counts["repair_errors"] += 1
            logger.warning("Accepted order flag repair failed; no SMS will be resubmitted.")
    return counts


async def process_order(order: dict, collections: dict, settings: WorkerSettings, sms: FarazSmsClient,
                        order_filter: dict) -> str:
    current = await collections["orders"].find_one(
        {**order_filter, "_id": order["_id"]}, collation=Collation(locale="simple"),
    )
    if not current:
        return "skipped"
    order = current
    notifications = collections["notifications"]
    notification = await prepare_notification(notifications, order["_id"])
    if not notification or notification["status"] != "prepared":
        return "skipped"
    try:
        recipient = normalize_recipient(order.get(ORDER_MOBILE_FIELD))
        link = await ensure_selection(collections["selections"], order["_id"], settings)
        attributes = {settings.link_attribute: link}
        sms.build_pattern_payload(recipient=recipient, attributes=attributes)
    except (SmsValidationError, SmsConfigurationError):
        result = await notifications.update_one({"order_id": order["_id"], "status": "prepared"}, {
            "$set": {"status": "failed", "error_code": "invalid_order_data", "updated_at": datetime.now(timezone.utc)},
        })
        return "failed" if result.matched_count else "skipped"
    claim = await claim_notification(notifications, order["_id"], sms.config.timeout_seconds)
    if not claim:
        return "skipped"
    try:
        response = await asyncio.to_thread(sms.send_pattern, recipient=recipient, attributes=attributes)
    except SmsProviderError as error:
        status, code = submission_failure(error)
        await finish_notification(notifications, claim, status, error_code=code, response_status_code=error.status_code)
        return status
    except (SmsValidationError, SmsConfigurationError):
        await finish_notification(notifications, claim, "failed", error_code="invalid_send_configuration")
        return "failed"
    except Exception:
        await finish_notification(notifications, claim, "submission_unknown", error_code="unexpected_submission_error")
        return "submission_unknown"
    accepted = await finish_notification(
        notifications, claim, "accepted", accepted_at=datetime.now(timezone.utc),
        response_status_code=response.status_code, provider_data=response.data,
    )
    if not accepted:
        return "submission_unknown"
    try:
        await sync_accepted_order(collections, claim, order_filter)
    except PyMongoError:
        logger.warning("SMS accepted; order flag update is pending a later repair.")
    return "accepted"


async def run_once(db, settings: WorkerSettings, *, limit: int | None = None,
                   dry_run: bool = False, sms: FarazSmsClient | None = None) -> dict:
    limit = settings.batch_size if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise WorkerConfigurationError("--limit must be a positive integer.")
    collections = worker_collections(db, settings)
    match = order_filter(settings)
    readonly = dry_run or not settings.sms_enabled
    summary = {"eligible": 0, "accepted": 0, "failed": 0, "submission_unknown": 0, "skipped": 0, "database_errors": 0}
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
        if not re.fullmatch(r"[0-9]+", sms.config.line_number):
            raise WorkerConfigurationError("FARAZ_LINE_NUMBER must contain only digits.")
        # Validate global pattern settings without generating a token or sending.
        sms.build_pattern_payload(recipient="09120000000", attributes={settings.link_attribute: "https://validation.invalid/s"})
        await check_indexes(collections)
        summary.update(await recover_notifications(collections, match, limit))
    cursor = await collections["orders"].aggregate(
        eligible_pipeline(match, limit), batchSize=min(limit, settings.batch_size),
        allowDiskUse=True, collation=Collation(locale="simple"),
        hint={"product_id": 1, ORDER_DATE_FIELD: 1, "_id": 1},
    )
    async for order in cursor:
        summary["eligible"] += 1
        if readonly:
            continue
        try:
            status = await process_order(order, collections, settings, sms, match)
            summary[status] += 1
        except PyMongoError:
            # If dispatch already began, a durable sending claim remains fenced.
            summary["database_errors"] += 1
            logger.warning("Order database operation failed; any pending submission will not be retried.")
    summary["read_only"] = readonly
    return summary


async def run_command(args) -> dict:
    settings = WorkerSettings.from_environment(require_delivery=args.once and not args.dry_run)
    # Reuse the application's MongoDB settings without changing its lifespan.
    try:
        from app.config import MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
    except (KeyError, ValueError):
        raise WorkerConfigurationError("Valid MONGODB_URI, MONGODB_DB_NAME and MONGODB_TIMEOUT_MS settings are required.") from None

    client = AsyncMongoClient(
        MONGODB_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS, timeoutMS=MONGODB_TIMEOUT_MS, tz_aware=True,
    )
    try:
        await client.admin.command("ping")
        db = client[MONGODB_DB_NAME]
        if args.create_indexes:
            await create_indexes(worker_collections(db, settings))
            return {"indexes": "ready"}
        return await run_once(db, settings, limit=args.limit, dry_run=args.dry_run)
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a bounded batch of tester orders; one run then exit.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--create-indexes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not (args.once or args.dry_run or args.create_indexes):
        parser.error("Choose --once, --dry-run or --create-indexes.")
    if args.create_indexes and (args.dry_run or args.limit is not None):
        parser.error("--create-indexes cannot be combined with --dry-run or --limit.")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive.")
    try:
        print(json.dumps(asyncio.run(run_command(args)), sort_keys=True))
        return 0
    except (WorkerConfigurationError, SmsConfigurationError) as error:
        # Our configuration errors never include setting values or credentials.
        print(f"Worker configuration error: {error}", file=sys.stderr)
        return 1
    except PyMongoError:
        print("Worker MongoDB operation failed; no automatic SMS resubmission will occur.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
