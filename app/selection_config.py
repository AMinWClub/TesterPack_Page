"""Shared tester count for the API, HTML and release worker."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")


def read_selection_count():
    raw = os.getenv("TESTER_SELECTION_COUNT", "6")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 3 or not 1 <= int(raw) <= 498:
        raise ValueError("TESTER_SELECTION_COUNT must be an integer between 1 and 498.")
    return int(raw)


TESTER_SELECTION_COUNT = read_selection_count()
