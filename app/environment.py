"""Resolve the database and selection origin for the explicitly selected environment."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class EnvironmentSettings:
    name: str
    mongodb_db_name: str
    selection_base_url: str


def read_environment() -> EnvironmentSettings:
    name = os.getenv("APP_ENV", "local").strip()
    if name not in {"local", "prod"}:
        raise ValueError("APP_ENV must be 'local' or 'prod'.")
    suffix = "LOCAL" if name == "local" else "PRODUCTION"
    db_key = f"MONGODB_DB_NAME_{suffix}"
    url_key = f"TESTER_SELECTION_BASE_URL_{suffix}"
    database = os.getenv(db_key, "").strip()
    if not database or len(database.encode("utf-8")) > 63 or re.search(r'[\s/\\."$\x00]', database):
        raise ValueError(f"{db_key} must be a valid, non-empty MongoDB database name.")
    base_url = os.getenv(url_key, "").strip()
    try:
        url = urlsplit(base_url)
        valid = (url.scheme in {"http", "https"} and url.hostname and url.path in {"", "/"}
                 and not (url.username or url.password or url.query or url.fragment)
                 and not re.search(r"\s", base_url) and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{url_key} must be an HTTP(S) origin without a path, credentials, query or fragment.")
    return EnvironmentSettings(name, database, base_url.rstrip("/"))
