"""Opt-in real MongoDB tests; all writes are restricted to an owned temporary database."""

import asyncio
import os
import unittest
from uuid import uuid4

from fastapi import FastAPI
from pymongo import AsyncMongoClient, monitoring

from app.catalog import CatalogCache, router
from tests.test_catalog import asgi_get, fragrance, product


class FindCounter(monitoring.CommandListener):
    def __init__(self, database_name):
        self.database_name = database_name
        self.finds = []

    def started(self, event):
        if event.database_name == self.database_name and event.command_name == "find":
            self.finds.append(event.command["find"])

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@unittest.skipUnless(os.getenv("TESTER_RUN_CATALOG_INTEGRATION") == "1", "Set TESTER_RUN_CATALOG_INTEGRATION=1 for isolated MongoDB tests")
class CatalogMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
        self.uri = MONGODB_URI
        self.production_database = MONGODB_DB_NAME
        self.timeout = MONGODB_TIMEOUT_MS
        self.database_name = "tester_catalog_it_" + uuid4().hex
        self.counter = FindCounter(self.database_name)
        self.owned = False
        self.client = self.new_client()
        self.addAsyncCleanup(self.cleanup)
        await self.client.admin.command("ping")
        self.assertNotEqual(self.database_name, self.production_database)
        self.db = self.client[self.database_name]
        self.assertEqual(await self.db.list_collection_names(), [])
        self.owned = True
        rows = [product(1000 + index, vol_idx=index + 1) for index in range(510)]
        rows[0]["pid"] = 12
        rows[1]["pid"] = "0012"
        rows[2]["pid"] = "no-fragrance"
        missing_index = product("no-index")
        missing_index.pop("vol_idx")
        rows += [
            product("private", name="عطر شخصی", vol_idx=0),
            product("sold-out", variations={"a": {"in_stock": False}}, vol_idx=0),
            product("numeric-stock", variations={"a": {"in_stock": 1}}, vol_idx=0),
            product("text-stock", variations={"a": {"in_stock": "true"}}, vol_idx=0),
            product("root-stock", in_stock=True, variations={}, vol_idx=0),
            product("null-variations", variations=None, vol_idx=0), missing_index,
        ]
        await self.db.products_api.insert_many(rows)
        enrichment = []
        for index, row in enumerate(rows[:510]):
            if index in (1, 2):
                continue
            pid = str(row["pid"]) if index % 2 == 0 else row["pid"]
            enrichment.append(fragrance(pid, imageUrl="https://fimgs.net/product.jpg", notesTop=[{"name": "Rose", "noteId": 105, "imageUrl": "https://fimgs.net/rose.jpg"}],
                                        longevity={"votes": {"longLasting": 10}}, sillage={"votes": {"strong": 10}},
                                        descriptiveTags=[{"id": "romantic", "weight": 3}], descriptionText="must not leak"))
        await self.db.fragrantica_products.insert_many(enrichment)
        self.cache = CatalogCache(self.db)
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.state.catalog = self.cache
        self.counter.finds.clear()

    def new_client(self):
        return AsyncMongoClient(self.uri, serverSelectionTimeoutMS=self.timeout, timeoutMS=self.timeout, event_listeners=[self.counter])

    async def cleanup(self):
        try:
            if self.owned:
                assert self.database_name.startswith("tester_catalog_it_") and self.database_name != self.production_database
                cleanup_client = self.new_client()
                try:
                    await cleanup_client.drop_database(self.database_name)
                finally:
                    await cleanup_client.close()
        finally:
            await self.client.close()

    async def test_filter_real_bson_boolean_sort_limit_and_pid_matching(self):
        result = await self.cache.get()
        self.assertEqual(result["total"], 498)
        self.assertEqual([row["vol_idx"] for row in result["products"]], list(range(1, 499)))
        self.assertEqual([row["pid"] for row in result["products"][:3]], ["12", "0012", "no-fragrance"])
        self.assertEqual(self.cache.stats, {"matched": 496, "unmatched": 2})
        self.assertEqual(result["products"][0]["brand"], "Test Brand")
        self.assertEqual(result["products"][0]["imageUrl"], "https://fimgs.net/product.jpg")
        self.assertNotIn("descriptiveTags", result["products"][0])
        self.assertIsNone(result["products"][1]["imageUrl"])
        self.assertEqual(result["products"][1]["brand"], "برند اصلی")
        self.assertEqual(self.counter.finds, ["products_api", "fragrantica_products"])
        self.assertEqual((await asgi_get(self.app, "/api/tester/perfumes/1509"))[0], 404)

    async def test_concurrent_cache_hit_and_refresh(self):
        results = await asyncio.gather(*(self.cache.get() for _ in range(20)))
        self.assertTrue(all(row["total"] == 498 for row in results))
        self.assertEqual(self.counter.finds, ["products_api", "fragrantica_products"])
        await self.cache.get()
        self.assertEqual(len(self.counter.finds), 2)
        self.cache.expires_at = 0
        await self.cache.get()
        self.assertEqual(self.counter.finds, ["products_api", "fragrantica_products"] * 2)

    async def test_detail_live_stock_privacy_translation_and_missing_enrichment(self):
        status, result = await asgi_get(self.app, "/api/tester/perfumes/12")
        self.assertEqual(status, 200)
        self.assertEqual(result["notesTop"][0]["name"], "رز")
        self.assertEqual(result["imageUrl"], "https://fimgs.net/product.jpg")
        self.assertEqual(result["longevity"]["level"], 4)
        self.assertEqual(result["sillage"]["level"], 3)
        self.assertEqual(result["descriptiveTags"], [{"id": "romantic", "label": "رمانتیک"}])
        self.assertNotIn("descriptionText", result)
        self.assertNotIn("votes", result["longevity"])
        self.assertEqual((await asgi_get(self.app, "/api/tester/perfumes/0012"))[0], 200)
        self.assertEqual((await self.cache.detail("no-fragrance"))["mainAccords"], [])
        self.assertEqual((await self.cache.detail("no-fragrance"))["descriptiveTags"], [])
        # Fixture only: stock changes must invalidate detail even on a cache hit.
        await self.db.products_api.update_one({"pid": 12}, {"$set": {"variations": {"a": {"in_stock": False}}}})
        self.assertEqual((await asgi_get(self.app, "/api/tester/perfumes/12"))[0], 404)

    async def test_connection_failure_and_reconnect(self):
        await self.cache.get()
        await self.client.close()
        self.cache.expires_at = 0
        self.assertEqual((await asgi_get(self.app, "/api/tester/catalog"))[0], 503)
        self.client = self.new_client()
        self.cache.db = self.client[self.database_name]
        self.assertEqual((await asgi_get(self.app, "/api/tester/catalog"))[0], 200)
