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
from app.workers.tester_sms import TOKEN_VERSION, order_filter, worker_collections

router = APIRouter(prefix="/api/tester/selection", tags=["selection"])
logger = logging.getLogger(__name__)
COUNT = 10
ORDER_MARKER = "tester_selection_submission_id"
ORDER_PROJECTION = {key: 1 for key in ("_id", "id", "first_name", "last_name", "phone", "recommended_products", ORDER_MARKER)}
SIMPLE = Collation(locale="simple")


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24}", token):
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
    # Link validity is independent of the worker's eventual sms_sent sync.
    return {key: value for key, value in order_filter(settings).items() if key != "sms_sent"}


def order_display(order):
    parts = [order.get("first_name"), order.get("last_name")]
    if any(value is not None and not isinstance(value, str) for value in parts):
        fail(409, "inconsistent_order", "اطلاعات این سفارش نیاز به بررسی پشتیبانی دارد.")
    name = " ".join(value.strip() for value in parts if value and value.strip())
    identifier = order.get("id")
    try:
        phone = normalize_recipient(order.get("phone"))
    except SmsValidationError:
        fail(409, "inconsistent_order", "اطلاعات این سفارش نیاز به بررسی پشتیبانی دارد.")
    if not name or not isinstance(identifier, str) or not identifier.strip():
        fail(409, "inconsistent_order", "اطلاعات این سفارش نیاز به بررسی پشتیبانی دارد.")
    return {"id": identifier, "customer_name": name, "phone": phone, "tester_count": COUNT}


async def linked_order(db, settings, token):
    digest = validate_token(token)
    collections = worker_collections(db, settings)
    selection = await collections["selections"].find_one({"token_hash": digest}, collation=SIMPLE)
    if (not selection or not isinstance(selection.get("order_id"), ObjectId)
            or type(selection.get("token_version")) is not int or selection["token_version"] != TOKEN_VERSION
            or selection.get("status") not in {"pending", "submitting", "submitted"}):
        fail(404, "invalid_link", "لینک انتخاب تستر معتبر نیست یا سفارش در دسترس نیست.")
    order = await collections["orders"].find_one(
        {**eligible_order(settings), "_id": selection["order_id"]}, projection=ORDER_PROJECTION, collation=SIMPLE,
    )
    if order is None:
        fail(404, "invalid_link", "لینک انتخاب تستر معتبر نیست یا سفارش در دسترس نیست.")
    order_display(order)
    return collections, selection, order


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


def response_context(selection, order):
    return {"order": order_display(order), "selection": {
        "status": selection["status"], "selected_pids": selection.get("selected_pids", []),
        "selected_names": selection.get("selected_names", []),
    }}


def existing_order_error():
    fail(409, "already_selected", "این سفارش قبلاً انتخاب ثبت‌شده دارد و قابل بازنویسی نیست. در صورت نیاز با پشتیبانی تماس بگیرید.")


async def finish_intent(collections, settings, selection, order):
    """Only continue the winning intent. No lease, expiry or replacement of its choices."""
    if not intent_valid(selection):
        fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
    if not owns_order(selection, order):
        if "recommended_products" in order or ORDER_MARKER in order:
            existing_order_error()
        # Both fields are written in ONE atomic update. The marker distinguishes our
        # intent from pre-existing names, even if an old list happens to be identical.
        await collections["orders"].update_one({
            **eligible_order(settings), "_id": selection["order_id"],
            "recommended_products": {"$exists": False}, ORDER_MARKER: {"$exists": False},
        }, {"$set": {"recommended_products": selection["selected_names"], ORDER_MARKER: selection["submission_id"]}}, collation=SIMPLE)
        order = await collections["orders"].find_one(
            {**eligible_order(settings), "_id": selection["order_id"]}, projection=ORDER_PROJECTION, collation=SIMPLE,
        )
        if order is None:
            fail(404, "invalid_link", "سفارش در دسترس نیست.")
        if not owns_order(selection, order):
            existing_order_error()
    now = datetime.now(timezone.utc)
    finished = await collections["selections"].find_one_and_update({
        "_id": selection["_id"], "order_id": selection["order_id"], "status": "submitting",
        "submission_id": selection["submission_id"],
        "selected_pids": selection["selected_pids"], "selected_names": selection["selected_names"],
    }, {"$set": {"status": "submitted", "submitted_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER, collation=SIMPLE)
    if finished is None:
        finished = await collections["selections"].find_one({"_id": selection["_id"]}, collation=SIMPLE)
    if not finished or finished.get("status") != "submitted" or not owns_order(finished, order):
        fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
    return response_context(finished, order)


async def context(db, settings, token):
    collections, selection, order = await linked_order(db, settings, token)
    if selection["status"] == "submitting":
        return await finish_intent(collections, settings, selection, order)
    if selection["status"] == "submitted":
        if not owns_order(selection, order):
            fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
    elif "recommended_products" in order or ORDER_MARKER in order:
        existing_order_error()
    return response_context(selection, order)


async def submit(db, settings, token, pids):
    collections, selection, order = await linked_order(db, settings, token)
    validate_pids(pids)
    if selection["status"] in {"submitting", "submitted"}:
        if selection.get("selected_pids") != pids:
            fail(409, "different_submission", "انتخاب‌های این سفارش قبلاً برای ثبت دریافت شده‌اند و قابل تغییر نیستند.")
        if selection["status"] == "submitting":
            return await finish_intent(collections, settings, selection, order)
        if not owns_order(selection, order):
            fail(409, "inconsistent_selection", "وضعیت انتخاب‌های این سفارش نیاز به بررسی پشتیبانی دارد.")
        return response_context(selection, order)
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
    now = datetime.now(timezone.utc)
    selection = await collections["selections"].find_one_and_update({
        "_id": selection["_id"], "order_id": selection["order_id"], "token_hash": selection["token_hash"], "status": "pending",
    }, {"$set": {"status": "submitting", "submission_id": uuid4().hex,
                 "selected_pids": pids, "selected_names": names, "submitting_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER, collation=SIMPLE)
    if selection is None:
        # Another request won; reload its immutable intent and compare this request.
        return await submit(db, settings, token, pids)
    return await finish_intent(collections, settings, selection, order)


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
