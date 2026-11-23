from datetime import date
from pydantic import BaseModel, Field, model_validator

from .normalization import normalize_utm


class RowFilter(BaseModel):
    row: int = Field(ge=2, le=5000)
    source: str = Field(min_length=1, max_length=250)
    medium: str = Field(min_length=1, max_length=250)
    campaign: str | None = Field(default=None, max_length=250)
    discount_code: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def validate_utms(self):
        if not normalize_utm(self.source) or not normalize_utm(self.medium):
            raise ValueError("source and medium must be non-empty after normalization")
        return self


class ReportRequest(BaseModel):
    rows: list[RowFilter] = Field(max_length=500)
    end_date: date | None = None  # Optional for testing/backfills; can't be after local today.

    @model_validator(mode="after")
    def validate_rows(self):
        ids = [row.row for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError("Repeated sheet row number")
        return self


class DayHeader(BaseModel):
    date: str
    jalali: str


class RowTotals(BaseModel):
    row: int
    totals: list[str]  # Decimal strings: no floating-point precision loss over JSON.


class ReportResponse(BaseModel):
    matching_logic: str = "utm_or_discount_regex_v1"
    start_date: str
    end_date: str
    timezone: str = "Asia/Tehran"
    days: list[DayHeader]
    rows: list[RowTotals]
