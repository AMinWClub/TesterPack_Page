"""Google Sheets daily sales routes, hosted by the tester application."""
import logging
import secrets
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pymongo.errors import PyMongoError

from .calendar import iter_dates, to_jalali
from .config import Settings, get_settings
from .repository import mongo_client, totals_by_row
from .schemas import DayHeader, ReportRequest, ReportResponse, RowTotals

log = logging.getLogger(__name__)
router = APIRouter(prefix="/googlesheetapi", tags=["Google Sheets"])


def authorize(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Settings = Depends(get_settings),
) -> Settings:
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return settings


@router.get("/health")
def health():
    return {"ok": True}


@router.post("/", response_model=ReportResponse)
@router.post("/api/v1/reports/utm-daily-sales", response_model=ReportResponse, include_in_schema=False)
def daily_sales(payload: ReportRequest, settings: Settings = Depends(authorize)):
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    end_day = payload.end_date or today
    if end_day < settings.start_date or end_day > today:
        raise HTTPException(status_code=422, detail="end_date outside report window")
    if (end_day - settings.start_date).days > 3650:
        raise HTTPException(status_code=422, detail="Report window exceeds 10 years")

    try:
        collection = mongo_client(settings.mongo_uri)[settings.mongo_db][settings.mongo_collection]
        amounts = totals_by_row(
            collection, payload.rows, settings.start_date, end_day,
            settings.mongo_max_time_ms,
        )
    except (PyMongoError, ValueError):
        log.exception("Daily UTM sales aggregation failed")
        raise HTTPException(status_code=503, detail="Report temporarily unavailable")

    return ReportResponse(
        start_date=settings.start_date.isoformat(),
        end_date=end_day.isoformat(),
        days=[DayHeader(date=d.isoformat(), jalali=to_jalali(d))
              for d in iter_dates(settings.start_date, end_day)],
        rows=[RowTotals(row=row.row, totals=amounts[row.row]) for row in payload.rows],
    )
