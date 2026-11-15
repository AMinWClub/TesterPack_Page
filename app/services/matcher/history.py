"""Token-scoped paid purchase history, without leaking purchases to clients."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.catalog import canonical_pid, pid_variants
from app.services.sms.faraz import normalize_recipient, SmsValidationError
from app.services.matcher.scoring import profile

HISTORY_PROJECTION = {"_id": 1, "phone": 1, "date_paid": 1, "product_id": 1}
PROFILE_PROJECTION = {"_id": 0, "pid": 1, "genderVotes": 1, "mainAccords": 1,
                      "notesTop": 1, "notesMiddle": 1, "notesBase": 1,
                      "olfactory_preferences": 1}


def clean_pid(value):
    pid = canonical_pid(value)
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


def order_pids(order):
    raw = order.get("product_id")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(pid for item in raw if (pid := clean_pid(item)) is not None))


def paid_at(value):
    if isinstance(value, datetime):
        date = value
    elif isinstance(value, str) and value.strip():
        try:
            date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return None
    else:
        return None
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def phone_variants(phone):
    local = normalize_recipient(phone)
    return [local, "98" + local[1:], "+98" + local[1:]]


def _stable_shuffle(pids, *, source_order_id, current_order_id):
    """Random-looking but stable for repeated opens of the same tester link."""
    def sort_key(pid):
        seed = f"{current_order_id}:{source_order_id}:{pid}".encode("utf-8")
        return hashlib.sha256(seed).digest()
    return sorted(pids, key=sort_key)


async def load_sources(db, current_order, limit=3):
    """Find up to 3 *distinct* recent purchased perfumes with usable Fragrantica data.

    Scan all paid orders of this phone, newest first, until 3 usable references
    are found. No current-order product is used as a reference.
    Returns (reference profiles, all previously/currently bought PIDs).
    """
    phone = normalize_recipient(current_order["phone"])
    cursor = db["preprocess_orders"].find(
        {"phone": {"$in": phone_variants(phone)}, "_id": {"$ne": current_order["_id"]}},
        HISTORY_PROJECTION,
    )
    rows = await cursor.to_list()
    now = datetime.now(timezone.utc)
    valid_orders = []
    for row in rows:
        try:
            if normalize_recipient(row.get("phone")) != phone:
                continue
        except SmsValidationError:
            continue
        date = paid_at(row.get("date_paid"))
        if date is not None and date <= now:
            valid_orders.append((date, row))
    valid_orders.sort(key=lambda record: (record[0], str(record[1]["_id"])), reverse=True)

    bought = set(order_pids(current_order))
    for _, row in valid_orders:
        bought.update(order_pids(row))
    if not bought:
        return [], set()
    variants = list(dict.fromkeys(v for pid in bought for v in pid_variants(pid)))
    fragrances = await db["fragrantica_products"].find({"pid": {"$in": variants}}, PROFILE_PROJECTION).to_list()
    # String PIDs win over integer aliases (same convention as catalog enrichment).
    by_pid = {}
    for item in sorted(fragrances, key=lambda doc: isinstance(doc.get("pid"), str)):
        pid = clean_pid(item.get("pid"))
        if pid is not None:
            by_pid[pid] = profile(item)

    sources, used = [], set()
    for _, order in valid_orders:
        shuffled = _stable_shuffle(order_pids(order), source_order_id=order["_id"], current_order_id=current_order["_id"])
        for pid in shuffled:
            if pid in used or by_pid.get(pid) is None:
                continue
            used.add(pid)
            sources.append({**by_pid[pid], "pid": pid})
            if len(sources) == limit:
                return sources, bought
    return sources, bought
