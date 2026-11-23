"""Regression tests for UTM OR discount-code attribution in the integrated API."""
from datetime import date

from bson.decimal128 import Decimal128

from app.googlesheetapi.repository import build_pipeline, totals_by_row
from app.googlesheetapi.schemas import RowFilter, ReportResponse


class FakeCollection:
    def __init__(self, groups):
        self.groups = groups
        self.calls = []

    def aggregate(self, pipeline, **kwargs):
        self.calls.append((pipeline, kwargs))
        return iter(self.groups)


def group(day, source, medium, campaign, total, discount=None):
    return {
        "_id": {"day": day, "source": source, "medium": medium,
                "campaign": campaign, "discount_code": discount},
        "total": Decimal128(str(total)),
        "bad_total_count": 0,
        "bad_date_count": 0,
    }


def test_pipeline_retains_discount_code_and_uses_paid_date_and_state():
    pipeline = build_pipeline(date(2026, 11, 16), date(2026, 11, 17))
    assert pipeline[0]["$match"]["state"] == {"$ne": "cancelled"}
    assert "status" not in pipeline[0]["$match"]
    assert "date_paid" in pipeline[0]["$match"]
    assert pipeline[2]["$group"]["_id"]["discount_code"] == "$discount_code"


def test_or_union_deduplicates_per_row_even_when_both_match():
    collection = FakeCollection([
        group("2026-11-16", "instagram", "konnjed", "aban", 100, "KONJED"),
        group("2026-11-16", "other", "medium", "other", 200, "hello-konjed-x"),
        group("2026-11-16", "instagram", "konnjed", "aban", 300, ""),
        group("2026-11-16", "other", "other", None, 400, "no-match"),
        group("2026-11-17", None, None, None, 500, "konjed"),
    ])
    rows = [
        RowFilter(row=3, source="instagram", medium="konnjed",
                  campaign="aban", discount_code="konjed"),
        RowFilter(row=30, source="instagram", medium="konnjed",
                  campaign="bf", discount_code="konjed"),
    ]
    totals = totals_by_row(collection, rows, date(2026, 11, 16), date(2026, 11, 17), 60000)
    assert totals[3] == ["600", "500"]  # 100+200+300; not 700
    assert totals[30] == ["300", "500"]  # 100+200, no UTM match
    assert len(collection.calls) == 1


def test_discount_normalization_and_regex_meta_are_literal():
    collection = FakeCollection([
        group("2026-11-16", "other", "other", None, 10, "SAVE.10"),
        group("2026-11-16", "other", "other", None, 20, "savea10"),
        group("2026-11-16", "other", "other", None, 30, "save.۱۰"),
        group("2026-11-16", "other", "other", None, 40, "save-10"),
    ])
    rows = [RowFilter(row=2, source="instagram", medium="x", discount_code="save.10")]
    assert totals_by_row(collection, rows, date(2026, 11, 16), date(2026, 11, 16), 60000)[2] == ["40"]


def test_blank_discount_keeps_utm_only_behavior():
    collection = FakeCollection([
        group("2026-11-16", "other", "else", None, 50, "konjed"),
        group("2026-11-16", "bale", "daily", "any", 80, ""),
    ])
    rows = [
        RowFilter(row=2, source="bale", medium="daily", discount_code=None),
        RowFilter(row=3, source="bale", medium="daily", discount_code=""),
    ]
    amounts = totals_by_row(collection, rows, date(2026, 11, 16), date(2026, 11, 16), 60000)
    assert amounts == {2: ["80"], 3: ["80"]}


def test_response_version_identifies_or_backend():
    response = ReportResponse(
        start_date="2026-11-16", end_date="2026-11-16",
        days=[], rows=[],
    )
    assert response.model_dump()["matching_logic"] == "utm_or_discount_regex_v1"
