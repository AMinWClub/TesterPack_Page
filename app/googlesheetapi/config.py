"""Server-side settings. Never send MongoDB credentials or the API key to Sheets."""
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import os

from app.config import MONGODB_URI, MONGODB_DB_NAME


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_db: str
    mongo_collection: str
    api_key: str
    start_date: date
    mongo_max_time_ms: int


@lru_cache

def get_settings() -> Settings:
    required = ("GOOGLESHEET_MONGO_COLLECTION", "SALES_API_KEY")
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    start_date = date.fromisoformat(os.getenv("START_DATE", "2026-11-16"))
    if start_date != date(2026, 11, 16):
        raise RuntimeError("START_DATE must be 2026-11-16 (1405/08/25) for this report")
    max_time_ms = int(os.getenv("MONGO_MAX_TIME_MS", "60000"))
    if max_time_ms < 1000:
        raise RuntimeError("MONGO_MAX_TIME_MS must be >= 1000")
    return Settings(
        mongo_uri=os.getenv("GOOGLESHEET_MONGO_URI", MONGODB_URI),
        mongo_db=os.getenv("GOOGLESHEET_MONGO_DB", MONGODB_DB_NAME),
        mongo_collection=os.environ["GOOGLESHEET_MONGO_COLLECTION"],
        api_key=os.environ["SALES_API_KEY"],
        start_date=start_date,
        mongo_max_time_ms=max_time_ms,
    )
