from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymongo import AsyncMongoClient

from app.config import APP_NAME, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = AsyncMongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS,
        timeoutMS=MONGODB_TIMEOUT_MS,
    )
    try:
        await client.admin.command("ping")
        app.state.mongo_client = client
        app.state.db = client[MONGODB_DB_NAME]
        yield
    finally:
        await client.close()
        app.state.mongo_client = None
        app.state.db = None


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
