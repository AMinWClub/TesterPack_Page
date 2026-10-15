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

_selection_rate = os.getenv("TESTER_SELECTION_RATE_LIMIT_PER_MINUTE", "60")
if not _selection_rate.isascii() or not _selection_rate.isdecimal() or not 1 <= len(_selection_rate) <= 4:
    raise ValueError("TESTER_SELECTION_RATE_LIMIT_PER_MINUTE must be an integer between 1 and 1000.")
TESTER_SELECTION_RATE_LIMIT_PER_MINUTE = int(_selection_rate)
if not 1 <= TESTER_SELECTION_RATE_LIMIT_PER_MINUTE <= 1000:
    raise ValueError("TESTER_SELECTION_RATE_LIMIT_PER_MINUTE must be an integer between 1 and 1000.")
