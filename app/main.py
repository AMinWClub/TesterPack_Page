from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import AsyncMongoClient

from app.config import APP_NAME, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
from app.catalog import CatalogCache, router as catalog_router


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
        app.state.catalog = CatalogCache(app.state.db)
        yield
    finally:
        await client.close()
        app.state.mongo_client = None
        app.state.db = None
        app.state.catalog = None


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.include_router(catalog_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def frontend():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/frontend", StaticFiles(directory=FRONTEND), name="frontend")
