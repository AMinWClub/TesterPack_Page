"""Token context and resumable, immutable tester submission on standalone MongoDB."""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pymongo import ReturnDocument
from pymongo.collation import Collation
from pymongo.errors import PyMongoError

from app.catalog import canonical_pid, eligible_products
from app.services.sms import SmsValidationError
from app.services.sms.faraz import normalize_recipient
from app.selection_security import TOKEN_PATTERN
from app.workers.tester_sms import TOKEN_VERSION, order_filter, selection_deadline, sms_full_name, worker_collections

router = APIRouter(prefix="/api/tester/selection", tags=["selection"])
logger = logging.getLogger(__name__)
COUNT = 10
ORDER_MARKER = "tester_selection_submission_id"
ORDER_WRITTEN_AT = "tester_selection_written_at"
ORDER_PROJECTION = {key: 1 for key in ("_id", "state", "id", "first_name", "last_name", "phone", "recommended_products", ORDER_MARKER, ORDER_WRITTEN_AT)}
SIMPLE = Collation(locale="simple")


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(TOKEN_PATTERN, token):
        fail(400, "invalid_token", "لینک انتخاب تستر معتبر نیست. لطفاً از لینک پیامک استفاده کنید.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def validate_pids(pids):
    if not isinstance(pids, list) or len(pids) != COUNT:
        fail(422, "invalid_count", "لطفاً دقیقاً ۱۰ عطر انتخاب کنید.")
    if any(not isinstance(pid, str) or not pid.strip() or pid != pid.strip() or len(pid) > 128
           or any(ord(char) < 32 for char in pid) for pid in pids):
        fail(422, "invalid_products", "شناسهٔ یکی از عطرها معتبر نیست.")
    if len(set(pids)) != COUNT:
        fail(422, "duplicate_products", "هر عطر را فقط یک بار انتخاب کنید.")
    return pids


def eligible_order(settings):
    # Only new order writes require pending; receipts remain readable after release.
    return order_filter(settings)


def require_pending(order):
    if order.get("state") != "pending":
        fail(409, "order_not_pending", "این سفارش از مرحلهٔ انتخاب تستر خارج شده و امکان ثبت انتخاب جدید ندارد.")


def order_display(order):
    name = sms_full_name(order)
    identifier = order.get("id")
    try:
        phone = normalize_recipient(order.get("phone"))
    except SmsValidationError:
        fail(409, "inconsistent_order", "اطلاعات این سفارش نیاز به بررسی پشتیبانی دارد.")
    if not isinstance(identifier, str) or not identifier.strip():
        fail(409, "inconsistent_order", "اطلاعات این سفارش نیاز به بررسی پشتیبانی دارد.")
    return {"id": identifier, "customer_name": name, "phone": phone, "tester_count": COUNT}


async def linked_order(db, settings, token):
    digest = validate_token(token)
    collections = worker_collections(db, settings)
    selection = await timed_selection(collections, {"token_hash": digest})
    if (not selection or not isinstance(selection.get("order_id"), ObjectId)
            or type(selection.get("token_version")) is not int or selection["token_version"] != TOKEN_VERSION
            or selection.get("status") not in {"pending", "submitting", "submitted", "review_required"}):
        fail(404, "invalid_link", "لینک انتخاب تستر معتبر نیست یا سفارش در دسترس نیست.")
    order = await collections["orders"].find_one(
        {"_id": selection["order_id"]}, projection=ORDER_PROJECTION, collation=SIMPLE,
    )
    if order is None:
        fail(404, "invalid_link", "لینک انتخاب تستر معتبر نیست یا سفارش در دسترس نیست.")
    order_display(order)
    return collections, selection, order


async def timed_selection(collections, match):
    # Use MongoDB's clock for both Context and atomic write predicates.
    cursor = await collections["selections"].aggregate([
        {"$match": match}, {"$limit": 1}, {"$set": {"_server_now": "$$NOW"}},
    ], collation=SIMPLE)
    async for row in cursor:
        return row
    return None


def utc_date(value):
    if not isinstance(value, datetime):
        return None
    # BSON dates decoded by the application's default client are naive UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def link_window(collections, selection, settings):
    notification = await collections["notifications"].find_one(
        {"order_id": selection["order_id"], "status": "accepted"}, projection={"accepted_at": 1},
    )
    now = utc_date(selection["_server_now"])
    accepted_at = utc_date(notification.get("accepted_at")) if notification else None
    if accepted_at is None or accepted_at > now:
        return {"now": now, "accepted_at": None, "expires_at": None}
    expires_at = selection_deadline(accepted_at, settings, selection)
    return {"now": now, "accepted_at": accepted_at, "expires_at": expires_at}


def deadline_guard(window):
    return {"$lt": ["$$NOW", window["expires_at"]]}


def require_active(window):
    if window["expires_at"] is None:
        fail(409, "sms_not_accepted", "تأیید ارسال پیامک هنوز ثبت نشده است. لطفاً کمی بعد دوباره بررسی کنید.")
    if window["now"] >= window["expires_at"]:
        expired_error()


def expired_error():
    fail(410, "selection_expired", "مهلت انتخاب تسترهای شما به پایان رسیده است.")


def intent_valid(selection):
    pids, names = selection.get("selected_pids"), selection.get("selected_names")
    return (isinstance(selection.get("submission_id"), str)
            and re.fullmatch(r"[a-f0-9]{32}", selection["submission_id"]) is not None
            and isinstance(pids, list) and len(pids) == COUNT
            and all(isinstance(pid, str) and pid.strip() for pid in pids) and len(set(pids)) == COUNT
            and isinstance(names, list) and len(names) == COUNT
            and all(isinstance(name, str) and name.strip() for name in names))


def owns_order(selection, order):
    return (intent_valid(selection) and order.get(ORDER_MARKER) == selection["submission_id"]
            and order.get("recommended_products") == selection["selected_names"])


def response_context(selection, order, window=None):
    if selection["status"] == "submitted":
        availability, code = "submitted", None
        expires_at = utc_date(selection.get("expires_at"))
        remaining = 0
    else:
        expires_at = window["expires_at"]
        remaining = max(0, (expires_at - window["now"]).total_seconds()) if expires_at else 0
        if selection["status"] == "review_required":
            availability, code = "review_required", "selection_review_required"
        elif expires_at is None:
            availability, code = "awaiting_sms", "sms_not_accepted"
        elif remaining == 0:
            availability, code = "expired", "selection_expired"
        else:
            availability, code = "active", None
    return {"order": order_display(order), "selection": {
        "status": selection["status"], "selected_pids": selection.get("selected_pids", []),
        "selected_names": selection.get("selected_names", []),
        "availability": availability, "code": code,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "remaining_seconds": remaining, "requires_support": selection["status"] == "review_required",
    }}


def existing_order_error():
    fail(409, "already_selected", "این سفارش قبلاً انتخاب ثبت‌شده دارد و قابل بازنویسی نیست. در صورت نیاز با پشتیبانی تماس بگیرید.")


def written_in_time(selection, order, window):
    started, written = utc_date(selection.get("submitting_at")), utc_date(order.get(ORDER_WRITTEN_AT))
    return (window["expires_at"] is not None and started is not None and written is not None
            and window["accepted_at"] <= started <= written < window["expires_at"])


def order_write_pipeline(selection, window):
    # The authoritative decision and timestamp share ONE pipeline's constant NOW.
    # Do not rely on the earlier query predicate or a separate $currentDate clock.
    # Expired commands leave the entire document unchanged, even if matched.
    return [{"$replaceWith": {"$cond": [
        {"$and": [deadline_guard(window), {"$eq": ["$state", "pending"]}]},
        {"$mergeObjects": ["$$ROOT", {
            "recommended_products": {"$literal": selection["selected_names"]},
            ORDER_MARKER: {"$literal": selection["submission_id"]},
            ORDER_WRITTEN_AT: "$$NOW",
        }]},
        "$$ROOT",
    ]}}]


async def review_incomplete(collections, settings, selection, order, window, reason):
    await collections["selections"].update_one({
        "_id": selection["_id"], "status": "submitting", "submission_id": selection["submission_id"],
    }, {"$set": {"status": "review_required", "review_reason": reason}, "$currentDate": {"updated_at": True}})
    latest = await timed_selection(collections, {"_id": selection["_id"]})
    if latest is None:
        fail(404, "invalid_link", "سفارش در دسترس نیست.")
    window = await link_window(collections, latest, settings)
    order = await collections["orders"].find_one(
        {"_id": selection["order_id"]}, projection=ORDER_PROJECTION, collation=SIMPLE,
    )
    if order is None:
        fail(404, "invalid_link", "سفارش در دسترس نیست.")
    # An on-time write racing this diagnostic update may still be finalized.
    if owns_order(latest, order) and written_in_time(latest, order, window):
        return await finish_intent(collections, settings, latest, order, window)
    return response_context(latest, order, window)


async def finish_intent(collections, settings, selection, order, window=None):
    """Resume the frozen intent, but never create order choices after its deadline."""
    if not intent_valid(selection):
        fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
    window = window or await link_window(collections, selection, settings)
    if not owns_order(selection, order):
        if "recommended_products" in order or ORDER_MARKER in order:
            existing_order_error()
        if selection["status"] == "review_required" or window["expires_at"] is None:
            return response_context(selection, order, window)
        if window["now"] >= window["expires_at"]:
            return await review_incomplete(collections, settings, selection, order, window, "deadline_before_order_write")
        require_pending(order)
        started = utc_date(selection.get("submitting_at"))
        if started is None or not window["accepted_at"] <= started < window["expires_at"]:
            return await review_incomplete(collections, settings, selection, order, window, "unverified_intent_time")
        match = eligible_order(settings)
        match["$expr"] = {"$and": [match["$expr"], deadline_guard(window)]}
        # Deadline and absence guard are evaluated by MongoDB in the SAME write.
        await collections["orders"].update_one({
            **match, "_id": selection["order_id"],
            "recommended_products": {"$exists": False}, ORDER_MARKER: {"$exists": False},
        }, order_write_pipeline(selection, window), collation=SIMPLE)
        order = await collections["orders"].find_one(
            {"_id": selection["order_id"]}, projection=ORDER_PROJECTION, collation=SIMPLE,
        )
        if order is None:
            fail(404, "invalid_link", "سفارش در دسترس نیست.")
        if not owns_order(selection, order):
            if "recommended_products" in order or ORDER_MARKER in order:
                existing_order_error()
            latest = await timed_selection(collections, {"_id": selection["_id"]})
            window = await link_window(collections, latest, settings)
            if window["expires_at"] is not None and window["now"] < window["expires_at"]:
                require_pending(order)
            # A matched pipeline can intentionally be a no-op after expiration.
            return await review_incomplete(collections, settings, latest, order, window, "deadline_before_order_write")
    if not written_in_time(selection, order, window):
        return await review_incomplete(collections, settings, selection, order, window, "unverified_order_write_time")
    finished = await collections["selections"].find_one_and_update({
        "_id": selection["_id"], "order_id": selection["order_id"], "status": {"$in": ["submitting", "review_required"]},
        "submission_id": selection["submission_id"],
        "selected_pids": selection["selected_pids"], "selected_names": selection["selected_names"],
    }, {"$set": {"status": "submitted", "submitted_at": order[ORDER_WRITTEN_AT]}, "$currentDate": {"updated_at": True}},
        return_document=ReturnDocument.AFTER, collation=SIMPLE)
    if finished is None:
        finished = await collections["selections"].find_one({"_id": selection["_id"]}, collation=SIMPLE)
    if not finished or finished.get("status") != "submitted" or not owns_order(finished, order):
        fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
    return response_context(finished, order)


async def context(db, settings, token):
    collections, selection, order = await linked_order(db, settings, token)
    if selection["status"] == "submitted":
        if not owns_order(selection, order):
            fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
        return response_context(selection, order)
    window = await link_window(collections, selection, settings)
    if selection["status"] in {"submitting", "review_required"}:
        return await finish_intent(collections, settings, selection, order, window)
    if window["expires_at"] is not None and window["now"] >= window["expires_at"]:
        return response_context(selection, order, window)
    require_pending(order)
    if "recommended_products" in order or ORDER_MARKER in order:
        existing_order_error()
    return response_context(selection, order, window)


async def submit(db, settings, token, pids):
    collections, selection, order = await linked_order(db, settings, token)
    validate_pids(pids)
    if selection["status"] in {"submitting", "submitted", "review_required"}:
        if selection.get("selected_pids") != pids:
            fail(409, "different_submission", "انتخاب‌های این سفارش قبلاً برای ثبت دریافت شده‌اند و قابل تغییر نیستند.")
        if selection["status"] == "submitted":
            if not owns_order(selection, order):
                fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
            return response_context(selection, order)
        result = await finish_intent(collections, settings, selection, order)
        if result["selection"]["status"] == "submitted":
            return result
        if result["selection"]["code"] == "selection_expired":
            expired_error()
        if result["selection"]["code"] == "sms_not_accepted":
            fail(409, "sms_not_accepted", "تأیید ارسال پیامک هنوز ثبت نشده است. لطفاً دوباره بررسی کنید.")
        fail(409, "selection_review_required", "وضعیت انتخاب‌های شما نیاز به بررسی پشتیبانی دارد.")
    window = await link_window(collections, selection, settings)
    require_active(window)
    require_pending(order)
    if "recommended_products" in order or ORDER_MARKER in order:
        existing_order_error()
    # One fresh primary-source query, including the current sorted 498-product cap.
    products = await eligible_products(db)
    by_pid = {}
    for product in products:
        pid = canonical_pid(product.get("pid"))
        if pid in pids:
            if pid in by_pid:
                fail(409, "invalid_products", "اطلاعات یکی از عطرها نیاز به بررسی دارد. لطفاً دوباره تلاش کنید.")
            by_pid[pid] = product.get("name")
    if any(pid not in by_pid for pid in pids):
        fail(409, "products_unavailable", "یکی از عطرهای انتخابی دیگر موجود نیست. لطفاً کاتالوگ را به‌روز و انتخاب‌ها را بررسی کنید.")
    names = [by_pid[pid] for pid in pids]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        fail(409, "invalid_products", "نام یکی از عطرها در دسترس نیست. لطفاً انتخاب‌ها را بررسی کنید.")
    selection = await collections["selections"].find_one_and_update({
        "_id": selection["_id"], "order_id": selection["order_id"], "token_hash": selection["token_hash"], "status": "pending",
        "$expr": deadline_guard(window),
    }, {"$set": {"status": "submitting", "submission_id": uuid4().hex,
                 "selected_pids": pids, "selected_names": names, "expires_at": window["expires_at"],
                 "sms_accepted_at": window["accepted_at"]}, "$currentDate": {"submitting_at": True, "updated_at": True}},
        return_document=ReturnDocument.AFTER, collation=SIMPLE)
    if selection is None:
        # Another request won; reload its immutable intent and compare this request.
        latest = await timed_selection(collections, {"token_hash": validate_token(token)})
        if latest is None:
            fail(404, "invalid_link", "سفارش در دسترس نیست.")
        if latest["status"] == "pending":
            expired_error()
        return await submit(db, settings, token, pids)
    # Refresh server time after catalog validation and the intent write.
    return await submit(db, settings, token, pids)


async def read_body(request, *, submitting):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8192:
            fail(413, "request_too_large", "حجم درخواست بیش از حد مجاز است.")
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        fail(400, "invalid_request", "ساختار درخواست معتبر نیست.")
    allowed = {"token", "pids"} if submitting else {"token"}
    if not isinstance(body, dict) or set(body) != allowed:
        fail(400, "invalid_request", "ساختار درخواست معتبر نیست.")
    validate_token(body["token"])
    return body


async def handle(request, *, submitting=False):
    body = await read_body(request, submitting=submitting)
    settings = getattr(request.app.state, "selection_settings", None)
    if settings is None:
        fail(503, "configuration_unavailable", "سرویس انتخاب تستر موقتاً در دسترس نیست. لطفاً بعداً تلاش کنید.")
    try:
        if submitting:
            return await submit(request.app.state.db, settings, body["token"], body["pids"])
        return await context(request.app.state.db, settings, body["token"])
    except PyMongoError:
        logger.warning("Selection database operation unavailable; retry context to recover.")
        fail(503, "database_unavailable", "ارتباط با سرویس برقرار نشد. وضعیت ثبت را دوباره بررسی کنید؛ انتخاب‌های قبلی بازنویسی نمی‌شوند.")


@router.post("/context")
async def selection_context(request: Request):
    return await handle(request)


@router.post("/submit")
async def selection_submit(request: Request):
    return await handle(request, submitting=True)
