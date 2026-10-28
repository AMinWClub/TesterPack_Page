"""Bounded payment-age release, independent of SMS and selection configuration."""

from datetime import datetime
import logging

from pymongo.collation import Collation
from pymongo.errors import PyMongoError

PAID_INDEX = "tester_pending_paid_release"
PAID_INDEX_KEYS = [("state", 1), ("tester_paid_release_at", 1), ("_id", 1)]
PAID_FIELD = "tester_paid_release_at"
SIMPLE = Collation(locale="simple")
PAID_MS = 40 * 60 * 1000
logger = logging.getLogger(__name__)


def paid_at_expression():
    return {"$convert": {"input": {"$cond": [
        {"$or": [{"$eq": [{"$type": "$date_paid"}, kind]} for kind in ("date", "string")]},
        "$date_paid", None,
    ]}, "to": "date", "onError": None, "onNull": None}}


def deadline_expression():
    return {"$ifNull": [{"$add": [paid_at_expression(), PAID_MS]}, False]}


def exact_pending():
    return {"$eq": [{"$type": "$state"}, "string"]}


async def create_index(orders):
    await orders.create_index(PAID_INDEX_KEYS, name=PAID_INDEX, collation=SIMPLE)


async def release_paid_orders(orders, *, limit=50, dry_run=False, normalize_only=False):
    """Normalize a bounded missing-field batch and release a bounded due batch.

    A false sentinel records invalid dates so one malformed row cannot starve
    the queue. The missing-field query also discovers orders created later by
    the external order importer. Each write rechecks the source document.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("Paid release limit must be a positive integer")
    result = {"normalized": 0, "invalid_paid_dates": 0, "paid_timeout_ready": 0,
              "released_paid_timeout": 0, "paid_timeout_errors": 0, "backfill_pending": False, "release_pending": False}
    try:
        index = (await orders.index_information()).get(PAID_INDEX, {})
        if (index.get("key") != PAID_INDEX_KEYS or index.get("sparse") or index.get("unique")
                or index.get("partialFilterExpression") is not None
                or index.get("collation", {}).get("locale", "simple") != "simple"):
            raise ValueError("Required paid release index is missing; run --create-paid-index first")
        # Capture MongoDB's clock, independent of the worker host's timezone/clock.
        clock = await orders.aggregate([
            {"$match": {"state": "pending"}}, {"$limit": 1}, {"$project": {"_id": 0, "now": "$$NOW"}},
        ], hint=PAID_INDEX, collation=SIMPLE)
        clocks = [row async for row in clock]
        if not clocks:
            return result
        now = clocks[0]["now"]
        rows = await orders.aggregate([
            {"$match": {"state": "pending", PAID_FIELD: None, "$expr": exact_pending()}},
            {"$sort": {"_id": 1}}, {"$limit": limit + 1},
            {"$project": {"_id": 1, "date_paid": 1, "deadline": deadline_expression()}},
        ], hint=PAID_INDEX, collation=SIMPLE)
        pending = [row async for row in rows]
        result["backfill_pending"] = len(pending) > limit
        for row in pending[:limit]:
            deadline = row.get("deadline", False)
            if not isinstance(deadline, datetime):
                result["invalid_paid_dates"] += 1
            if dry_run:
                if isinstance(deadline, datetime) and deadline <= now:
                    result["paid_timeout_ready"] += 1
                continue
            present = "date_paid" in row
            match = {"_id": row["_id"], "state": "pending", PAID_FIELD: None,
                     "date_paid": {"$exists": present}, "$expr": exact_pending()}
            if present:
                match["$expr"] = {"$and": [exact_pending(),
                    {"$eq": ["$date_paid", {"$literal": row["date_paid"]}]},
                    {"$eq": [{"$type": "$date_paid"}, {"$type": {"$literal": row["date_paid"]}}]},
                ]}
            written = await orders.update_one(match, {"$set": {PAID_FIELD: deadline}}, collation=SIMPLE)
            result["normalized"] += written.modified_count
            if not written.matched_count:
                result["backfill_pending"] = True
        if result["invalid_paid_dates"]:
            logger.warning("Payment release found %d missing or invalid payment dates; review the indexed invalid-date report.",
                           result["invalid_paid_dates"])
        if normalize_only:
            return result
        due = await orders.aggregate([
            {"$match": {"state": "pending", PAID_FIELD: {"$lte": now}, "$expr": exact_pending()}},
            {"$sort": {PAID_FIELD: 1, "_id": 1}}, {"$limit": limit + 1},
            {"$project": {"_id": 1, PAID_FIELD: 1, "date_paid": 1, "deadline": deadline_expression()}},
        ], hint=PAID_INDEX, collation=SIMPLE)
        due_rows = [row async for row in due]
        result["release_pending"] = len(due_rows) > limit
        for row in due_rows[:limit]:
            # Guard against a changed payment date and against state races. The
            # write's own $$NOW is authoritative at the exact boundary.
            match = {"_id": row["_id"], "state": "pending", PAID_FIELD: row[PAID_FIELD],
                     "$expr": {"$and": [exact_pending(),
                         {"$eq": [f"${PAID_FIELD}", deadline_expression()]},
                         {"$gte": ["$$NOW", f"${PAID_FIELD}"]}]}}
            if row[PAID_FIELD] != row["deadline"]:
                if not dry_run:
                    await orders.update_one({"_id": row["_id"], "state": "pending", PAID_FIELD: row[PAID_FIELD],
                        "$expr": {"$and": [exact_pending(), {"$eq": [deadline_expression(), {"$literal": row["deadline"]}]}]}},
                        {"$set": {PAID_FIELD: row["deadline"]}}, collation=SIMPLE)
                continue
            if dry_run:
                result["paid_timeout_ready"] += 1
                continue
            written = await orders.update_one(match, {"$set": {"state": "not_seen"}}, collation=SIMPLE)
            result["paid_timeout_ready"] += written.matched_count
            result["released_paid_timeout"] += written.modified_count
    except PyMongoError:
        result["paid_timeout_errors"] += 1
        logger.warning("Payment timeout release database operation failed; the next standalone run can resume.")
    return result
