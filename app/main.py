from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import AsyncMongoClient

from app.config import APP_NAME, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI, TESTER_SELECTION_RATE_LIMIT_PER_MINUTE
from app.catalog import CatalogCache, router as catalog_router
from app.selections import router as selection_router
from app.selection_security import SelectionSecurity, install_access_log_redaction
from app.workers.tester_sms import WorkerConfigurationError, WorkerSettings


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
        try:
            app.state.selection_settings = WorkerSettings.from_environment()
        except WorkerConfigurationError:
            # A catalog-only development install can still start without worker settings.
            app.state.selection_settings = None
        yield
    finally:
        await client.close()
        app.state.mongo_client = None
        app.state.db = None
        app.state.catalog = None
        app.state.selection_settings = None


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(SelectionSecurity, requests_per_minute=TESTER_SELECTION_RATE_LIMIT_PER_MINUTE)
app.include_router(catalog_router)
app.include_router(selection_router)
install_access_log_redaction()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def frontend():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/frontend", StaticFiles(directory=FRONTEND), name="frontend")
app.add_api_route("/{token:selection_token}", frontend, methods=["GET"], include_in_schema=False)
