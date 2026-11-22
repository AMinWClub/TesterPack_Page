from datetime import date
from decimal import Decimal

from bson.decimal128 import Decimal128

from app.googlesheetapi.calendar import to_jalali
from app.googlesheetapi.normalization import normalize_utm
from app.googlesheetapi.repository import build_pipeline, totals_by_row, utc_bounds
from app.googlesheetapi.schemas import RowFilter


class FakeCollection:
    def __init__(self, groups):
        self.groups = groups
        self.calls = []

    def aggregate(self, pipeline, **kwargs):
        self.calls.append((pipeline, kwargs))
        return iter(self.groups)


def group(day, source, medium, campaign, total):
    return {
        "_id": {"day": day, "source": source, "medium": medium,
                "campaign": campaign},
        "total": Decimal128(str(total)),
        "bad_total_count": 0,
        "bad_date_count": 0,
    }


def test_jalali_headers_and_boundaries():
    assert to_jalali(date(2026, 11, 16)) == "1405/08/25"
    assert to_jalali(date(2026, 11, 22)) == "1405/09/01"
    assert to_jalali(date(2026, 3, 21)) == "1405/01/01"
    assert utc_bounds(date(2026, 11, 16), date(2026, 11, 16)) == (
        "2026-11-15T20:30:00", "2026-11-16T20:30:00",
    )


def test_pipeline_filters_state_not_status_and_uses_paid_date():
    pipeline = build_pipeline(date(2026, 11, 16), date(2026, 11, 17))
    assert pipeline[0]["$match"]["state"] == {"$ne": "cancelled"}
    assert "status" not in pipeline[0]["$match"]
    assert "date_paid" in pipeline[0]["$match"]
    assert pipeline[2]["$group"]["_id"]["day"]["$dateToString"]["timezone"] == "Asia/Tehran"


def test_normalization_case_invisible_and_numbers():
    assert normalize_utm("  Daily  ") == "daily"
    assert normalize_utm("1405081\u200c3") == "14050813"
    assert normalize_utm("۲۷aban") == "27aban"
    assert normalize_utm("۲۶ABAN ") == "26aban"


def test_aggregates_different_raw_utms_and_campaign_wildcard():
    c = FakeCollection([
        group("2026-11-16", "BALE", "Daily", "bf", "2000000"),
        group("2026-11-16", "bale", "daily", "other", "1000000"),
        group("2026-11-17", "Bale", "DAILY", "bf", "700"),
        group("2026-11-16", "sms", "campaign", "1405081\u200c3", "35000"),
    ])
    filters = [
        RowFilter(row=2, source="bale", medium="daily"),
        RowFilter(row=4, source="bale", medium="daily", campaign="bf"),
        RowFilter(row=6, source="sms", medium="campaign", campaign="۱۴۰۵۰۸۱۳"),
        RowFilter(row=8, source="missing", medium="other"),
    ]
    sums = totals_by_row(c, filters, date(2026, 11, 16), date(2026, 11, 17), 60000)
    assert sums[2] == ["3000000", "700"]  # missing campaign filter -> all campaigns
    assert sums[4] == ["2000000", "700"]
    assert sums[6] == ["35000", "0"]
    assert sums[8] == ["0", "0"]
    assert len(c.calls) == 1


def test_duplicate_sheet_filters_are_allowed_but_each_gets_same_amount():
    c = FakeCollection([group("2026-11-16", "sms", "campaign", "mother-day", "20000")])
    filters = [
        RowFilter(row=22, source="sms", medium="campaign", campaign="mother-day"),
        RowFilter(row=25, source="sms", medium="campaign", campaign="mother-day"),
    ]
    sums = totals_by_row(c, filters, date(2026, 11, 16), date(2026, 11, 16), 60000)
    assert sums[22] == sums[25] == ["20000"]


def test_invalid_total_fails_closed():
    bad = group("2026-11-16", "bale", "daily", None, 200)
    bad["bad_total_count"] = 1
    c = FakeCollection([bad])
    try:
        totals_by_row(c, [RowFilter(row=2, source="bale", medium="daily")],
                      date(2026, 11, 16), date(2026, 11, 16), 60000)
        assert False, "Expected error for invalid totals"
    except ValueError:
        pass


def test_endpoint_auth_and_json_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from app.googlesheetapi.config import Settings, get_settings
    from app.main import app
    import app.googlesheetapi.routes as main_module

    c = FakeCollection([group("2026-11-16", "BALE", "Daily", "bf", "1234000")])

    class FakeDatabase:
        def __getitem__(self, key):
            return c

    class FakeMongo:
        def __getitem__(self, key):
            return FakeDatabase()

    monkeypatch.setattr(main_module, "mongo_client", lambda uri: FakeMongo())
    settings = Settings(
        mongo_uri="mongodb://dummy", mongo_db="dummy", mongo_collection="dummy",
        api_key="unit-test-key", start_date=date(2026, 11, 16), mongo_max_time_ms=60000,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.get("/googlesheetapi/health").json() == {"ok": True}
        assert client.get("/health").json() == {"status": "ok"}
        payload = {"rows": [{"row": 2, "source": "bale", "medium": "daily"}],
                   "end_date": "2026-11-16"}
        assert client.post("/googlesheetapi/", json=payload).status_code == 401
        assert client.post(
            "/googlesheetapi/", json=payload, headers={"X-API-Key": "wrong"},
        ).status_code == 401
        response = client.post(
            "/googlesheetapi/", json=payload,
            headers={"X-API-Key": "unit-test-key"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["days"] == [{"date": "2026-11-16", "jalali": "1405/08/25"}]
        assert response.json()["rows"] == [{"row": 2, "totals": ["1234000"]}]
        alias = client.post(
            "/googlesheetapi/api/v1/reports/utm-daily-sales", json=payload,
            headers={"X-API-Key": "unit-test-key"},
        )
        assert alias.json() == response.json()
        assert client.post(
            "/googlesheetapi/", json={**payload, "rows": payload["rows"] * 2},
            headers={"X-API-Key": "unit-test-key"},
        ).status_code == 422
        from pymongo.errors import ConnectionFailure

        def unavailable(uri):
            raise ConnectionFailure("Unavailable")

        monkeypatch.setattr(main_module, "mongo_client", unavailable)
        failed = client.post(
            "/googlesheetapi/", json=payload, headers={"X-API-Key": "unit-test-key"},
        )
        assert failed.status_code == 503
        assert failed.json() == {"detail": "Report temporarily unavailable"}
    finally:
        app.dependency_overrides.clear()


def test_settings_use_tester_database_unless_explicitly_overridden(monkeypatch):
    import app.googlesheetapi.config as config

    monkeypatch.setenv("GOOGLESHEET_MONGO_COLLECTION", "orders")
    monkeypatch.setenv("SALES_API_KEY", "test-key")
    monkeypatch.setenv("START_DATE", "2026-11-16")
    monkeypatch.delenv("GOOGLESHEET_MONGO_URI", raising=False)
    monkeypatch.delenv("GOOGLESHEET_MONGO_DB", raising=False)
    try:
        config.get_settings.cache_clear()
        settings = config.get_settings()
        assert settings.mongo_uri == config.MONGODB_URI
        assert settings.mongo_db == config.MONGODB_DB_NAME
        monkeypatch.setenv("GOOGLESHEET_MONGO_URI", "mongodb://report-host:27017")
        monkeypatch.setenv("GOOGLESHEET_MONGO_DB", "report_db")
        config.get_settings.cache_clear()
        settings = config.get_settings()
        assert settings.mongo_uri == "mongodb://report-host:27017"
        assert settings.mongo_db == "report_db"
    finally:
        config.get_settings.cache_clear()
