"""Isolated browser/manual flow. Never use production orders or send SMS.

Run: python -m tests.selection_preview --port 8789
Test-only links are written to ignored test-artifacts/selection-preview.json.
The owned temporary database is dropped on graceful shutdown.
"""

import argparse
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pymongo import AsyncMongoClient

from app.catalog import CatalogCache, router as catalog_router
from app.config import MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
from app.main import FRONTEND, frontend, health
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
        for scenario in ("success", "uncertain", "lost_response", "unavailable", "expiry_expired", "expiry_waiting", "expiry_live", "expiry_review"):
            # Exercise exact-case HMAC/Hash lookup and both URL-safe symbols.
            for _ in range(1000):
                fixture = order(first_name="  مشتری  ", last_name=" آزمایشی ", phone="09120000000", id="OR-TEST-" + scenario)
                token = worker.token_for_order(fixture["_id"], config.token_secret)
                if scenario != "success" or ("-" in token and "_" in token
                        and any("a" <= char <= "z" for char in token)
                        and any("A" <= char <= "Z" for char in token)):
                    break
            else:
                raise RuntimeError("Could not prepare a mixed-case URL-safe preview fixture.")
            await db.preprocess_orders.insert_one(fixture)
            token = (await worker.ensure_selection(db[worker.SELECTIONS], fixture["_id"], config)).rsplit("/", 1)[1]
            await db[worker.NOTIFICATIONS].insert_one({
                "order_id": fixture["_id"], "status": "prepared" if scenario == "expiry_waiting" else "accepted",
                "accepted_at": None if scenario == "expiry_waiting" else datetime.now(timezone.utc)
                    - timedelta(minutes=31 if scenario in {"expiry_expired", "expiry_review"} else 0, seconds=1),
            })
            if scenario == "expiry_review":
                intent_id = uuid4().hex
                names = [row["name"] for row in rows[:10]]
                await db[worker.SELECTIONS].update_one({"order_id": fixture["_id"]}, {"$set": {
                    "status": "review_required", "review_reason": "unverified_order_write_time",
                    "submission_id": intent_id, "selected_pids": [str(i) for i in range(1, 11)], "selected_names": names,
                }})
                await db.preprocess_orders.update_one({"_id": fixture["_id"]}, {"$set": {
                    "recommended_products": names, "tester_selection_submission_id": intent_id,
                }})
            fixtures[scenario] = {"path": "/" + token, "order_id": fixture["id"], "phone": fixture["phone"]}
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


async def set_preview_window(scenario, remaining_seconds):
    """Browser-test helper; can only touch a named synthetic, owned preview order."""
    metadata = json.loads(Path("test-artifacts/selection-preview.json").read_text(encoding="utf-8"))
    name = metadata["database"]
    assert name.startswith("tester_selection_preview_") and name != MONGODB_DB_NAME
    assert len(name.removeprefix("tester_selection_preview_")) == 32
    assert scenario in {"expiry_live", "expiry_expired", "expiry_waiting"}
    assert -60 <= remaining_seconds <= 1800
    client = AsyncMongoClient(MONGODB_URI, timeoutMS=MONGODB_TIMEOUT_MS, tz_aware=True)
    try:
        db = client[name]
        order_id = metadata["fixtures"][scenario]["order_id"]
        assert order_id == "OR-TEST-" + scenario
        fixture = await db.preprocess_orders.find_one({"id": order_id})
        assert fixture is not None
        now = (await db.command("hello"))["localTime"]
        result = await db[worker.NOTIFICATIONS].update_one({"order_id": fixture["_id"]}, {"$set": {
            "status": "accepted", "accepted_at": now - timedelta(minutes=30) + timedelta(seconds=remaining_seconds),
        }})
        assert result.matched_count == 1
    finally:
        await client.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(SelectionSecurity, requests_per_minute=300)
app.include_router(catalog_router)
app.include_router(selection_router)
app.add_api_route("/", frontend, include_in_schema=False)
app.add_api_route("/health", health, include_in_schema=False)
app.mount("/frontend", StaticFiles(directory=FRONTEND), name="frontend")
app.add_api_route("/{token:selection_token}", frontend, include_in_schema=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Disposable tester-selection preview; no SMS is sent.")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
