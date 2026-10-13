import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")

APP_NAME = os.getenv("APP_NAME", "Minimal FastAPI")
MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB_NAME = os.environ["MONGODB_DB_NAME"]
MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))

if not MONGODB_URI or not MONGODB_DB_NAME:
    raise ValueError("MONGODB_URI and MONGODB_DB_NAME must not be empty.")
if MONGODB_TIMEOUT_MS <= 0:
    raise ValueError("MONGODB_TIMEOUT_MS must be a positive integer.")
