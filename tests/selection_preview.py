"""Isolated browser/manual flow. Never use production orders or send SMS.

Run: python -m tests.selection_preview --port 8789
Test-only links are written to ignored test-artifacts/selection-preview.json.
The owned temporary database is dropped on graceful shutdown.
"""

import argparse
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pymongo import AsyncMongoClient

from app.catalog import CatalogCache, router as catalog_router
from app.config import MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
from app.main import FRONTEND, frontend
from app.selections import router as selection_router
from app.selection_security import SelectionSecurity
from app.workers import tester_sms as worker
from tests.test_selections import order, settings


@asynccontextmanager
async def lifespan(app):
    name = "tester_selection_preview_" + uuid4().hex
    assert name != MONGODB_DB_NAME and name.startswith("tester_selection_preview_")
    client = AsyncMongoClient(MONGODB_URI, timeoutMS=MONGODB_TIMEOUT_MS, tz_aware=True)
    owned = False
    try:
        db = client[name]
        assert not await db.list_collection_names()
        owned = True
        config = replace(settings(), token_secret="preview-only-secret-" + uuid4().hex)
        await worker.create_indexes(worker.worker_collections(db, config))
        rows = [{"pid": i, "name": f"عطر آزمایشی شماره {i} | Original Source Perfume {i}", "label": f"TEST {i}",
                 "vol_idx": i, "variations": {"a": {"in_stock": True}}} for i in range(1, 31)]
        await db.products_api.insert_many(rows)
        await db.fragrantica_products.insert_many([{
            "pid": str(i), "name": f"Example Perfume {i}", "brand": "SORRAD Preview", "year": 2020,
            "genderVotes": {"votes": {"unisex": 10}}, "seasonVotes": {"winter": 10, "autumn": 8},
            "olfactory_preferences": {"temperature": "warm", "sweetness": "high"},
            "mainAccords": [{"name": "woody", "strength": 98.25, "color": "#774414"}],
            "notesTop": [{"name": "Rose", "noteId": "105"}],
            "descriptiveTags": [{"id": "luxurious", "weight": 3}, {"id": "bold", "weight": 2}],
            "longevity": {"votes": {"longLasting": 10}}, "sillage": {"votes": {"strong": 10}},
        } for i in range(1, 31)])
        fixtures = {}
        for scenario in ("success", "uncertain", "lost_response", "unavailable"):
            fixture = order(first_name="  مشتری  ", last_name=" آزمایشی ", phone="09120000000", id="OR-TEST-" + scenario)
            await db.preprocess_orders.insert_one(fixture)
            token = (await worker.ensure_selection(db[worker.SELECTIONS], fixture["_id"], config)).rsplit("/", 1)[1]
            fixtures[scenario] = {"path": "/s/" + token, "order_id": fixture["id"], "phone": fixture["phone"]}
        app.state.db, app.state.catalog, app.state.selection_settings = db, CatalogCache(db), config
        output = Path("test-artifacts/selection-preview.json")
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps({"database": name, "fixtures": fixtures}), encoding="utf-8")
        yield
    finally:
        if owned:
            assert name.startswith("tester_selection_preview_") and name != MONGODB_DB_NAME
            await client.drop_database(name)
        await client.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(SelectionSecurity, requests_per_minute=300)
app.include_router(catalog_router)
app.include_router(selection_router)
app.add_api_route("/", frontend, include_in_schema=False)
app.add_api_route("/s/{token}", frontend, include_in_schema=False)
app.mount("/frontend", StaticFiles(directory=FRONTEND), name="frontend")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Disposable tester-selection preview; no SMS is sent.")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
