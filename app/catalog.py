"""Read-only catalog: two batched MongoDB queries per refresh, one cache per process."""

import asyncio
import json
import logging
import re
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pymongo.errors import PyMongoError

from app.catalog_labels import (
    LONGEVITY, SILLAGE, accord_display, gender_display, meter_display,
    notes_display, safe_image_url, scent_display, season_display, text,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tester", tags=["catalog"])
CATALOG_LIMIT = 498
PRODUCT_FILTER = {
    "vol_idx": {"$exists": True},
    "name": {"$not": {"$regex": "شخصی"}},
    "$expr": {"$gt": [{"$size": {"$filter": {
        "input": {"$objectToArray": {"$ifNull": ["$variations", {}]}},
        "as": "variation", "cond": {"$eq": ["$$variation.v.in_stock", True]},
    }}}, 0]},
}
PRODUCT_PROJECTION = {"_id": 0, "pid": 1, "label": 1, "name": 1, "brand": 1, "url": 1, "vol_idx": 1, "imageUrl": 1, "image_url": 1}
SUMMARY_PROJECTION = {"_id": 0, **{key: 1 for key in (
    "pid", "name", "brand", "year", "imageUrl", "genderVotes", "seasonVotes", "olfactory_preferences", "mainAccords",
)}}
DETAIL_PROJECTION = {**SUMMARY_PROJECTION, **{key: 1 for key in ("notesTop", "notesMiddle", "notesBase", "longevity", "sillage")}}


def canonical_pid(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    return None


def pid_variants(value):
    """Only canonical decimal strings have an integer equivalent; 0012 stays distinct."""
    pid = canonical_pid(value)
    if pid is None:
        return []
    variants = [pid]
    if len(pid) <= 20 and re.fullmatch(r"(?:0|[1-9][0-9]*|-[1-9][0-9]*)", pid):
        number = int(pid)
        if -(2**63) <= number < 2**63:
            variants.append(number)
    return variants


def display_product(product, fragrance=None):
    fragrance = fragrance or {}
    gender, group = gender_display(fragrance.get("genderVotes"))
    fragrance_name = text(fragrance.get("name"))
    name = text(product.get("name")) or text(product.get("label")) or fragrance_name or "نام عطر موجود نیست"
    # Retain the real source name for display and search; never invent a perfume name/image.
    english = fragrance_name or (name.split("|", 1)[1].strip() if "|" in name else "")
    return {
        "pid": canonical_pid(product.get("pid")), "name": name, "label": text(product.get("label")),
        "fragranticaName": fragrance_name, "englishName": english or None,
        "brand": text(fragrance.get("brand")) or text(product.get("brand")) or None,
        "sourceBrand": text(product.get("brand")) or None,
        "imageUrl": (safe_image_url(product.get("imageUrl")) or safe_image_url(product.get("image_url"))
                     or safe_image_url(fragrance.get("imageUrl"))),
        "gender": gender, "genderGroup": group, "season": season_display(fragrance.get("seasonVotes")),
        "scent": scent_display(fragrance.get("olfactory_preferences"), fragrance.get("mainAccords") or []),
        "year": fragrance.get("year") if isinstance(fragrance.get("year"), int) and not isinstance(fragrance.get("year"), bool) else None,
        "vol_idx": product.get("vol_idx") if isinstance(product.get("vol_idx"), (str, int, float)) and not isinstance(product.get("vol_idx"), bool) else None,
    }


class CatalogCache:
    def __init__(self, db, ttl=60):
        self.db = db
        self.ttl = ttl
        self.expires_at = 0
        self.lock = asyncio.Lock()
        self.snapshot = None
        self.encoded = b""
        self.by_pid = {}
        self.stats = {"matched": 0, "unmatched": 0}

    async def get(self):
        if self.snapshot is not None and monotonic() < self.expires_at:
            return self.snapshot
        async with self.lock:
            if self.snapshot is not None and monotonic() < self.expires_at:
                return self.snapshot
            products = await self.db["products_api"].find(PRODUCT_FILTER, PRODUCT_PROJECTION).sort("vol_idx", 1).limit(CATALOG_LIMIT).to_list()
            variants = list({variant for product in products for variant in pid_variants(product.get("pid"))})
            fragrances = await self.db["fragrantica_products"].find({"pid": {"$in": variants}}, SUMMARY_PROJECTION).to_list() if variants else []
            by_pid = {}
            # Prefer the string form on duplicate enrichment records, deterministically.
            for fragrance in sorted(fragrances, key=lambda row: isinstance(row.get("pid"), str)):
                key = canonical_pid(fragrance.get("pid"))
                if key is not None:
                    by_pid[key] = fragrance
            items = []
            matched = 0
            for product in products:
                key = canonical_pid(product.get("pid"))
                if key is None:
                    continue
                fragrance = by_pid.get(key)
                matched += fragrance is not None
                items.append(display_product(product, fragrance))
            self.snapshot = {"products": items, "total": len(items)}
            self.encoded = json.dumps(self.snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            self.by_pid = {item["pid"]: item for item in items}
            self.stats = {"matched": matched, "unmatched": len(items) - matched}
            self.expires_at = monotonic() + self.ttl
            logger.info("Catalog refreshed: total=%d matched=%d unmatched=%d", len(items), matched, len(items) - matched)
            return self.snapshot

    async def detail(self, pid):
        await self.get()
        if pid not in self.by_pid:
            raise HTTPException(404, "این عطر در کاتالوگ قابل انتخاب نیست.")
        # Recheck live stock on every detail request, even while the catalog is cached.
        product = await self.db["products_api"].find_one({"$and": [PRODUCT_FILTER, {"pid": {"$in": pid_variants(pid)}}]}, PRODUCT_PROJECTION)
        if product is None:
            raise HTTPException(404, "این عطر دیگر قابل انتخاب نیست.")
        rows = await self.db["fragrantica_products"].find({"pid": {"$in": pid_variants(pid)}}, DETAIL_PROJECTION).to_list()
        rows.sort(key=lambda row: isinstance(row.get("pid"), str))
        fragrance = rows[-1] if rows else {}
        result = display_product(product, fragrance)
        result.update({
            "mainAccords": accord_display(fragrance.get("mainAccords")),
            **{key: notes_display(fragrance.get(key)) for key in ("notesTop", "notesMiddle", "notesBase")},
            "longevity": meter_display(fragrance.get("longevity"), LONGEVITY),
            "sillage": meter_display(fragrance.get("sillage"), SILLAGE),
        })
        return result


@router.get("/catalog")
async def catalog(request: Request):
    try:
        cache = request.app.state.catalog
        await cache.get()
        return Response(content=cache.encoded, media_type="application/json")
    except PyMongoError:
        logger.warning("Catalog database read failed")
        raise HTTPException(503, "دریافت کاتالوگ موقتاً ممکن نیست؛ دوباره تلاش کنید.") from None


@router.get("/perfumes/{pid}")
async def perfume_detail(pid: str, request: Request):
    try:
        return await request.app.state.catalog.detail(pid)
    except PyMongoError:
        logger.warning("Perfume detail database read failed")
        raise HTTPException(503, "دریافت جزئیات عطر موقتاً ممکن نیست؛ دوباره تلاش کنید.") from None
