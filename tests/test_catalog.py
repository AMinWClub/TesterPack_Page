"""Catalog unit and ASGI tests; no network or real database access."""

import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from pymongo.errors import ConnectionFailure

from app.catalog import (
    CATALOG_LIMIT, DETAIL_PROJECTION, PRODUCT_FILTER, CatalogCache, canonical_pid,
    display_product, pid_variants, router,
)
from app.catalog_labels import (
    ACCORDS, LONGEVITY, SILLAGE, accord_display, descriptive_tags_display, gender_display, meter_display,
    notes_display, safe_image_url, scent_display, season_display, season_keys, accord_labels,
)


def product(pid=12, **changes):
    return {"pid": pid, "name": "عطر تست | Test Perfume", "label": "TEST", "vol_idx": 1,
            "brand": "برند اصلی", "variations": {"a": {"in_stock": True}}, **changes}


def fragrance(pid="12", **changes):
    return {"pid": pid, "name": "Test Perfume", "brand": "Test Brand", "year": 2022,
            "genderVotes": {"votes": {"moreFemale": 20, "male": 1}},
            "seasonVotes": {"winter": 100, "autumn": 80, "spring": 30, "summer": 10, "day": 99999},
            "olfactory_preferences": {"temperature": "warm", "sweetness": "low"},
            "mainAccords": [{"name": "woody", "strength": 98.25, "color": "#774414"}], **changes}


class Cursor:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.sort_args = None
        self.limit_value = None

    def sort(self, *args):
        self.sort_args = args
        self.rows.sort(key=lambda row: row.get(args[0], 0))
        return self

    def limit(self, count):
        self.limit_value = count
        self.rows = self.rows[:count]
        return self

    async def to_list(self):
        await asyncio.sleep(0)
        return self.rows


def fake_db(products=None, fragrances=None):
    primary = Mock()
    primary.cursors = []
    def find_primary(*args):
        cursor = Cursor(products if products is not None else [product()])
        primary.cursors.append(cursor)
        return cursor
    primary.find.side_effect = find_primary
    primary.find_one = AsyncMock(return_value=product())
    enrichment = Mock()
    enrichment.find.side_effect = lambda *args: Cursor(fragrances if fragrances is not None else [fragrance()])
    return {"products_api": primary, "fragrantica_products": enrichment}


async def asgi_get(app, path):
    events = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(event):
        events.append(event)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
               "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
               "root_path": "", "headers": [], "server": ("test", 80), "client": ("test", 1234)}, receive, send)
    status = next(event["status"] for event in events if event["type"] == "http.response.start")
    body = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return status, json.loads(body)


class DisplayTests(unittest.TestCase):
    def test_pid_identity_and_equivalents(self):
        self.assertEqual(canonical_pid(12), "12")
        self.assertEqual(pid_variants("12"), ["12", 12])
        self.assertEqual(pid_variants("0012"), ["0012"])
        self.assertEqual(pid_variants("12.0"), ["12.0"])
        self.assertEqual(pid_variants("۱۲"), ["۱۲"])
        self.assertEqual(pid_variants("A-12"), ["A-12"])
        self.assertEqual(pid_variants(str(2**63)), [str(2**63)])
        self.assertEqual(pid_variants("9" * 5000), ["9" * 5000])
        for value in (None, True, 12.0, "", "  "):
            self.assertIsNone(canonical_pid(value))

    def test_gender_dominant_group_and_unknown_tie(self):
        self.assertEqual(gender_display({"votes": {"moreFemale": 7, "unisex": 4}}), ("متمایل به زنانه", "زنانه"))
        self.assertEqual(gender_display({"votes": {"moreMale": 7}}), ("متمایل به مردانه", "مردانه"))
        for data in ({}, {"votes": {}}, {"votes": {"female": True}}, {"votes": {"female": 2, "male": 2}}):
            self.assertEqual(gender_display(data), (None, None))

    def test_seasons_use_relative_support_not_day_night(self):
        self.assertEqual(season_display(fragrance()["seasonVotes"]), "پاییز و زمستان")
        self.assertEqual(season_display({"spring": 10, "summer": 9, "autumn": 8, "winter": 7}), "چهار فصل")
        self.assertEqual(season_display({"winter": 10, "summer": 1, "day": 999}), "زمستان")
        self.assertEqual(season_display({"votes": {"summer": 10, "spring": 9}}), "بهار و تابستان")
        self.assertIsNone(season_display({"day": 999, "winter": 0}))

    def test_scent_does_not_infer_bitter_from_low_sweetness(self):
        self.assertEqual(scent_display({"temperature": "warm", "sweetness": "high"}, []), "گرم و شیرین")
        self.assertEqual(scent_display({"temperature": "cool", "freshness": "high"}, []), "خنک و تازه")
        self.assertEqual(scent_display({"temperature": "warm"}, [{"name": "warm spicy", "strength": 100}]), "گرم و ادویه‌ای")
        self.assertNotIn("تلخ", scent_display({"sweetness": "low"}, fragrance()["mainAccords"]))
        self.assertIsNone(scent_display({}, []))

    def test_season_filter_keys_share_display_threshold_and_ignore_time_of_day(self):
        votes = {"spring": 100, "summer": 65, "autumn": 64.99, "winter": 0, "day": 9999, "night": 9999}
        for data in (votes, {"votes": votes}):
            self.assertEqual(season_keys(data), ["spring", "summer"])
            self.assertEqual(season_display(data), "بهار و تابستان")
        self.assertEqual(season_keys(dict.fromkeys(("spring", "summer", "autumn", "winter"), 1)),
                         ["spring", "summer", "autumn", "winter"])
        for data in (None, [], "winter", {}, {"votes": None}, {"day": 1},
                     {"spring": True, "summer": "10", "autumn": float("nan"), "winter": float("inf")}):
            self.assertEqual(season_keys(data), [])
            self.assertIsNone(season_display(data))

    def test_accord_filter_identity_translation_normalization_and_invalid_values(self):
        rows = [{"name": "  Warm   SPICY  ", "strength": 80}, {"name": "warm spicy", "strength": 20},
                {"name": "ＷＯＯＤＹ", "strength": 1}, {"name": "Mystery Accord", "strength": .5}]
        self.assertEqual(accord_labels(rows), {"warm spicy": "ادویه‌ای گرم", "woody": "چوبی", "mystery accord": "Mystery Accord"})
        for value in (None, "woody", 10, {}):
            self.assertEqual(accord_labels(value), {})
        invalid = [None, "woody", {}, {"name": 12, "strength": 1}, {"name": "123", "strength": 1},
                   {"name": "bad\nname", "strength": 1}, {"name": " ", "strength": 1}]
        invalid += [{"name": "woody", "strength": strength} for strength in (None, True, False, 0, -1, "50", float("nan"), float("inf"))]
        self.assertEqual(accord_labels(invalid), {})
        for data in (None, {}, "bad", 123, invalid):
            result = display_product(product(), fragrance(mainAccords=data, seasonVotes=None))
            self.assertEqual((result["seasonKeys"], result["accordKeys"]), ([], []))

    def test_accords_strength_colors_and_specialist_fallback(self):
        data = accord_display([{ "name": "woody", "strength": 80.24, "color": "#774414"},
                              {"name": "unknown specialist", "strength": 91, "color": "red;url(x)"}])
        self.assertEqual(data[0]["name"], "unknown specialist")
        self.assertIsNone(data[0]["color"])
        self.assertEqual(data[1], {"name": "چوبی", "originalName": "woody", "strength": 80.24, "color": "#774414"})
        self.assertEqual(accord_display([None, {}, {"name": "woody", "strength": float('nan')}]), [])

    def test_notes_translate_keep_identity_and_real_image(self):
        result = notes_display([{"name": "Rose", "noteId": 105, "imageUrl": "https://fimgs.net/a.jpg", "weight": .8, "url": "private-unused"},
                                {"name": "Akigalawood", "noteId": "697", "imageUrl": "javascript:alert(1)"}])
        self.assertEqual(result[0], {"name": "رز", "originalName": "Rose", "noteId": "105", "image": "https://fimgs.net/a.jpg"})
        self.assertEqual(result[1]["name"], "Akigalawood")
        self.assertIsNone(result[1]["image"])
        self.assertEqual(notes_display(None), [])

    def test_descriptive_tags_translate_preserve_identity_and_handle_missing_data(self):
        result = descriptive_tags_display([
            {"id": "luxurious", "weight": 3}, {"id": "calming", "weight": 2},
            {"id": "LUXURIOUS", "weight": 2}, {"id": "future_tag", "weight": 3},
            None, "bold", {}, {"id": "  "}, {"id": 12},
        ])
        self.assertEqual(result, [{"id": "luxurious", "label": "لوکس"},
                                  {"id": "calming", "label": "آرامش‌بخش"},
                                  {"id": "future_tag", "label": "future_tag"}])
        for tags in (None, {}, "bold", []):
            self.assertEqual(descriptive_tags_display(tags), [])

    def test_meters_use_votes_instead_of_disagreeing_average(self):
        data = meter_display({"average": 1, "votes": {"eternal": 10, "weak": 1}, "total": 11}, LONGEVITY)
        self.assertEqual(data["level"], 5)
        self.assertEqual(data["label"], "بسیار ماندگار")
        self.assertNotIn("votes", data)
        self.assertNotIn("average", data)
        self.assertEqual(meter_display({"votes": {"strong": 10}}, SILLAGE)["level"], 3)
        for data in ({"average": 4.5}, {"votes": {"strong": 2, "enormous": 2}}, {"votes": {"strong": -1}}):
            self.assertIsNone(meter_display(data, SILLAGE)["level"])

    def test_safe_images(self):
        self.assertEqual(safe_image_url("https://fimgs.net/mdimg/a.jpg"), "https://fimgs.net/mdimg/a.jpg")
        for url in (None, "", "javascript:x", "data:image/svg+xml,x", "http://fimgs.net/a.jpg", "//fimgs.net/a", "https://user:secret@example.com/a",
                    "https://localhost/x", "https://10.1.1.1/x", "https://127.0.0.1/x", "https://[::1]/x", "https://host.local/x",
                    "https://fimgs.net:8000/x", "https://fimgs.net/a\nb", "https://fimgs.net\\@localhost/a"):
            self.assertIsNone(safe_image_url(url), repr(url))

    def test_product_image_uses_real_sources_and_rejects_unsafe_urls(self):
        image = "https://fimgs.net/mdimg/perfume-thumbs/375x500.62318.jpg"
        self.assertEqual(display_product(product(), fragrance(imageUrl=image))["imageUrl"], image)
        primary_image = "https://example.com/product.jpg"
        self.assertEqual(display_product(product(imageUrl=primary_image), fragrance(imageUrl=image))["imageUrl"], primary_image)
        self.assertEqual(display_product(product(imageUrl="javascript:x"), fragrance(imageUrl=image))["imageUrl"], image)
        self.assertIsNone(display_product(product(), fragrance(imageUrl="http://fimgs.net/a.jpg"))["imageUrl"])

    def test_missing_enrichment_keeps_primary_data(self):
        result = display_product(product("0012"))
        self.assertEqual(result["pid"], "0012")
        self.assertEqual(result["brand"], "برند اصلی")
        self.assertEqual(result["sourceBrand"], "برند اصلی")
        self.assertIsNone(result["gender"])
        self.assertIsNone(result["imageUrl"])
        self.assertEqual(result["englishName"], "Test Perfume")
        self.assertEqual((result["seasonKeys"], result["accordKeys"]), ([], []))


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_sort_limit_and_no_per_product_query(self):
        db = fake_db([product(i, vol_idx=600-i) for i in range(600)], [fragrance(str(i)) for i in range(600)])
        cache = CatalogCache(db)
        result = await cache.get()
        self.assertEqual(result["total"], CATALOG_LIMIT)
        self.assertEqual(result["products"][0]["vol_idx"], 1)
        self.assertEqual(result["products"][-1]["vol_idx"], 498)
        self.assertEqual(db["products_api"].find.call_args.args[0], PRODUCT_FILTER)
        self.assertEqual(db["products_api"].cursors[0].sort_args, ("vol_idx", 1))
        self.assertEqual(db["products_api"].cursors[0].limit_value, 498)
        self.assertEqual(db["products_api"].find.call_count, 1)
        self.assertEqual(db["fragrantica_products"].find.call_count, 1)
        self.assertIn("$in", db["fragrantica_products"].find.call_args.args[0]["pid"])
        self.assertNotIn("mainAccords", result["products"][0])
        self.assertNotIn("genderVotes", result["products"][0])
        self.assertNotIn("notesTop", result["products"][0])

    async def test_mixed_identity_missing_enrichment_and_no_padding(self):
        db = fake_db([product(12), product("0012", vol_idx=2), product("other", vol_idx=3)], [fragrance("12")])
        cache = CatalogCache(db)
        result = await cache.get()
        self.assertEqual(result["total"], 3)
        self.assertEqual(cache.stats, {"matched": 1, "unmatched": 2})
        self.assertEqual(result["products"][0]["brand"], "Test Brand")
        self.assertEqual(result["products"][1]["brand"], "برند اصلی")

    async def test_cache_empty_ttl_and_concurrent_refresh(self):
        db = fake_db([], [])
        cache = CatalogCache(db)
        results = await asyncio.gather(*(cache.get() for _ in range(20)))
        self.assertTrue(all(row == {"products": [], "total": 0, "accordLabels": {}} for row in results))
        self.assertEqual(db["products_api"].find.call_count, 1)
        await cache.get()
        self.assertEqual(db["products_api"].find.call_count, 1)
        cache.expires_at = 0
        await cache.get()
        self.assertEqual(db["products_api"].find.call_count, 2)

    async def test_api_detail_rechecks_eligibility_and_omits_raw_fields(self):
        image = "https://fimgs.net/mdimg/perfume-thumbs/375x500.62318.jpg"
        db = fake_db(fragrances=[fragrance(imageUrl=image, descriptiveTags=[{"id": "bold", "weight": 3}])])
        app = FastAPI()
        app.include_router(router)
        app.state.catalog = CatalogCache(db)
        status, body = await asgi_get(app, "/api/tester/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(body["products"][0]["pid"], "12")
        self.assertEqual(body["products"][0]["imageUrl"], image)
        self.assertEqual(body["products"][0]["seasonKeys"], ["autumn", "winter"])
        self.assertEqual(body["products"][0]["accordKeys"], ["woody"])
        self.assertEqual(body["accordLabels"], {"woody": "چوبی"})
        self.assertNotIn("descriptiveTags", body["products"][0])
        self.assertNotIn("descriptiveTags", db["fragrantica_products"].find.call_args.args[1])
        self.assertEqual(db["fragrantica_products"].find.call_args.args[1]["imageUrl"], 1)
        status, body = await asgi_get(app, "/api/tester/perfumes/12")
        self.assertEqual(status, 200)
        self.assertEqual(body["mainAccords"][0]["strength"], 98.25)
        self.assertEqual(body["imageUrl"], image)
        self.assertEqual(body["descriptiveTags"], [{"id": "bold", "label": "جسور"}])
        self.assertEqual(db["fragrantica_products"].find.call_args.args[1]["descriptiveTags"], 1)
        for field in ("descriptionText", "pros", "cons", "similarPerfumes", "genderVotes", "seasonVotes", "olfactory_preferences"):
            self.assertNotIn(field, body)
        for field in ("descriptionText", "pros", "cons", "similarPerfumes", "purchase_count", "popularityScore"):
            self.assertNotIn(field, DETAIL_PROJECTION)
        self.assertIn(PRODUCT_FILTER, db["products_api"].find_one.call_args.args[0]["$and"])
        self.assertEqual((await asgi_get(app, "/api/tester/perfumes/unknown"))[0], 404)
        db["products_api"].find_one.return_value = None
        self.assertEqual((await asgi_get(app, "/api/tester/perfumes/12"))[0], 404)

    async def test_api_database_failure_is_generic_and_retry_recovers(self):
        db = fake_db()
        find = db["products_api"].find.side_effect
        db["products_api"].find.side_effect = ConnectionFailure("credential-or-host-must-not-leak")
        app = FastAPI()
        app.include_router(router)
        app.state.catalog = CatalogCache(db)
        status, body = await asgi_get(app, "/api/tester/catalog")
        self.assertEqual(status, 503)
        self.assertNotIn("credential", str(body))
        db["products_api"].find.side_effect = find
        self.assertEqual((await asgi_get(app, "/api/tester/catalog"))[0], 200)
