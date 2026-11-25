"""Optional-medium regression tests for the integrated Google Sheets report."""
from datetime import date

import pytest
from bson.decimal128 import Decimal128
from pydantic import ValidationError

from app.googlesheetapi.repository import totals_by_row
from app.googlesheetapi.schemas import RowFilter


class FakeCollection:
    def __init__(self, groups):
        self.groups = groups
        self.calls = []

    def aggregate(self, pipeline, **kwargs):
        self.calls.append((pipeline, kwargs))
        return iter(self.groups)


def group(source, medium, campaign, total, discount=None):
    return {
        "_id": {
            "day": "2026-11-16", "source": source, "medium": medium,
            "campaign": campaign, "discount_code": discount,
        },
        "total": Decimal128(str(total)),
        "bad_total_count": 0,
        "bad_date_count": 0,
    }


def test_source_required_but_medium_and_campaign_optional():
    assert RowFilter(row=2, source="bale").medium is None
    assert RowFilter(row=2, source="bale", medium="").medium == ""
    assert RowFilter(row=2, source="bale", campaign="bf").medium is None
    with pytest.raises(ValidationError):
        RowFilter(row=2, source=" ", medium="daily")
    with pytest.raises(ValidationError):
        RowFilter(row=2, source="")


def test_dynamic_utm_filters_and_discount_union_no_double_count():
    collection = FakeCollection([
        group("BALE", "Daily", "bf", 100, "konjed"),
        group("bale", "sms", "other", 200),
        group("bale", None, "bf", 300),
        group("instagram", "konnjed", "aban", 400, "konjed"),
        group("instagram", "ads", "bf", 500),
        group("other", None, None, 600, "hello-KONJED-123"),
    ])
    rows = [
        RowFilter(row=2, source="bale"),
        RowFilter(row=3, source="bale", medium="daily"),
        RowFilter(row=4, source="bale", campaign="bf"),
        RowFilter(row=5, source="bale", medium="daily", campaign="bf"),
        RowFilter(row=6, source="bale", medium="", discount_code="konjed"),
        RowFilter(row=7, source="instagram", campaign="aban"),
        RowFilter(row=8, source="other"),
        RowFilter(row=9, source="unknown", discount_code="konjed"),
        RowFilter(row=10, source="instagram", medium="ads"),
    ]
    today = date(2026, 11, 16)
    totals = totals_by_row(collection, rows, today, today, 60000)
    assert totals == {
        2: ["600"],      # all bale mediums / campaigns, even missing on orders
        3: ["100"],      # source + medium
        4: ["400"],      # source + campaign, regardless of medium
        5: ["100"],      # source + medium + campaign
        6: ["1600"],     # bale (600) OR discount-only (400+600), not 1700
        7: ["400"],      # instagram + campaign, no medium restriction
        8: ["600"],
        9: ["1100"],     # discount works regardless of unrelated UTM values
        10: ["500"],
    }
    assert len(collection.calls) == 1
