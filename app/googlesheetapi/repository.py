"""MongoDB aggregation and safe per-row matching.

IMPORTANT: This project assumes date_paid is a UTC ISO-8601 STRING (as provided in
sample orders). Both bounds use UTC string comparisons for an efficient index range
scan. If the underlying schema changes to BSON Date, adjust the $match bounds.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

from bson.decimal128 import Decimal128
from pymongo import MongoClient

from .calendar import iter_dates
from .config import Settings
from .normalization import normalize_utm
from .schemas import RowFilter

TEHRAN = ZoneInfo("Asia/Tehran")
UTC = timezone.utc


_clients: list[MongoClient] = []


@lru_cache
def mongo_client(uri: str) -> MongoClient:
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, appname="utm-daily-sales")
    _clients.append(client)
    return client


def close_mongo_clients() -> None:
    for client in _clients:
        client.close()
    _clients.clear()
    mongo_client.cache_clear()


def utc_bounds(start_day: date, end_day: date) -> tuple[str, str]:
    """Inclusive Tehran start day, exclusive day after end, as UTC ISO strings."""
    left = datetime.combine(start_day, datetime.min.time(), tzinfo=TEHRAN)
    right = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=TEHRAN)
    # Match ISO strings with or without fractional seconds at an exact boundary.
    return (
        left.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        right.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )


def build_pipeline(start_day: date, end_day: date) -> list[dict]:
    utc_start, utc_end = utc_bounds(start_day, end_day)
    return [
        {"$match": {
            "date_paid": {"$gte": utc_start, "$lt": utc_end},
            "state": {"$ne": "cancelled"},  # NOT the `status` field.
        }},
        {"$set": {
            "_report_paid": {"$dateFromString": {
                "dateString": "$date_paid", "onError": None, "onNull": None,
            }},
            "_report_total": {"$convert": {
                "input": "$total", "to": "decimal", "onError": None, "onNull": None,
            }},
        }},
        {"$group": {
            "_id": {
                "day": {"$dateToString": {
                    "format": "%Y-%m-%d", "date": "$_report_paid",
                    "timezone": "Asia/Tehran", "onNull": None,
                }},
                "source": "$utm_source",
                "medium": "$utm_medium",
                "campaign": "$utm_campaign",
            },
            "total": {"$sum": {"$ifNull": ["$_report_total", Decimal128("0")]}},
            "bad_total_count": {"$sum": {"$cond": [{"$eq": ["$_report_total", None]}, 1, 0]}},
            "bad_date_count": {"$sum": {"$cond": [{"$eq": ["$_report_paid", None]}, 1, 0]}},
        }},
    ]


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal128):
        value = value.to_decimal()
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("MongoDB returned invalid total") from exc
    if not result.is_finite():
        raise ValueError("MongoDB returned non-finite total")
    return result


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f")


def totals_by_row(
    collection, rows: list[RowFilter], start_day: date, end_day: date, max_time_ms: int
) -> dict[int, list[str]]:
    """One query for all supplied rows. Missing campaign => NO campaign constraint."""
    days = [day.isoformat() for day in iter_dates(start_day, end_day)]
    full_key_to_rows: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    source_medium_to_rows: dict[tuple[str, str], list[int]] = defaultdict(list)
    results: dict[int, dict[str, Decimal]] = {row.row: defaultdict(Decimal) for row in rows}

    for row in rows:
        source, medium, campaign = map(
            normalize_utm, (row.source, row.medium, row.campaign)
        )
        if campaign:
            full_key_to_rows[(source, medium, campaign)].append(row.row)
        else:
            source_medium_to_rows[(source, medium)].append(row.row)

    if rows:
        for group in collection.aggregate(
            build_pipeline(start_day, end_day), allowDiskUse=True, maxTimeMS=max_time_ms
        ):
            keys = group["_id"]
            src = normalize_utm(keys.get("source"))
            med = normalize_utm(keys.get("medium"))
            camp = normalize_utm(keys.get("campaign"))
            if not src or not med:
                continue
            matched = (
                full_key_to_rows.get((src, med, camp), [])
                + source_medium_to_rows.get((src, med), [])
            )
            if not matched:
                continue
            if group.get("bad_total_count") or group.get("bad_date_count"):
                raise ValueError("Matched order contains an invalid date_paid or total")
            day = keys.get("day")
            if day is None:
                raise ValueError("MongoDB returned null reporting date")
            # DB dates should be bounded by the $match; fail if unexpected.
            if day < start_day.isoformat() or day > end_day.isoformat():
                raise ValueError("MongoDB returned a date outside the requested window")
            amount = _decimal(group["total"])
            for row_number in matched:
                results[row_number][day] += amount

    return {
        row.row: [_decimal_string(results[row.row][day]) for day in days]
        for row in rows
    }
