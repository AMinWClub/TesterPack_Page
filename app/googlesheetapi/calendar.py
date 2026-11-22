"""Simple Gregorian -> Jalali conversion for daily report headers.

Algorithm is valid for the modern Gregorian/Persian calendar dates used here.
All date boundaries are calculated separately with ZoneInfo(Asia/Tehran).
"""
from datetime import date, timedelta

_GREGORIAN_MONTH_DAY_OFFSETS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def to_jalali(value: date) -> str:
    gy, gm, gd = value.year, value.month, value.day
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
        + (gy2 + 399) // 400 - 80 + gd + _GREGORIAN_MONTH_DAY_OFFSETS[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
