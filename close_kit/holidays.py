"""Japanese national holidays (Cabinet Office "国民の祝日" CSV) and due dates counted from a period end.

The CSV is read as it was downloaded (cp932, `1955/1/1,元日`). Its range is 1 January of the first year to 31 December
of the last year in the file; a business-day count that needs a day outside that range is not answered (it never
quietly counts wrong). Counting rules (ADR 0007). BlackLine's Task Management sheet says recurring tasks are scheduled "in relation to your period-end date";
none of the BlackLine documents says whether the period end is day 0 or day 1. The rule follows Oracle's Close Manager, where the base date is "Day Zero" and "if Day 0 is a Friday, the tasks for Day 1 are
placed on the following Monday if you selected to exclude weekend days". The base date here is the period end:

  暦日   period end + N days
  平日   Monday-Friday only. N = 0 is the period end itself; N > 0 is the N-th weekday after the period end;
         N < 0 is the |N|-th weekday before it.
  営業日 like 平日, but national holidays and the company's own holidays are skipped too.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from . import config


class HolidayTableError(Exception):
    pass


@dataclass(frozen=True)
class HolidayTable:
    names: dict[date, str]
    first: date   # 1 January of the first year in the file
    last: date    # 31 December of the last year in the file

    def covers(self, d: date) -> bool:
        return self.first <= d <= self.last


def parse_csv(raw: bytes) -> HolidayTable:
    try:
        text = raw.decode("cp932")
    except UnicodeDecodeError as e:
        raise HolidayTableError(f"祝日の CSV が cp932 として読めない: {e}") from None
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) != 2:
        raise HolidayTableError("祝日の CSV の見出し（2 列）が無い")
    names: dict[date, str] = {}
    for n, row in enumerate(rows[1:], start=2):
        if not row:
            continue
        try:
            y, m, d = (int(x) for x in row[0].strip().split("/"))
            names[date(y, m, d)] = row[1].strip()
        except (ValueError, IndexError):
            raise HolidayTableError(f"祝日の CSV の {n} 行目が読めない: {row!r}") from None
    if not names:
        raise HolidayTableError("祝日の CSV に日付が 1 つも無い")
    years = [d.year for d in names]
    return HolidayTable(names, date(min(years), 1, 1), date(max(years), 12, 31))


@lru_cache(maxsize=4)
def _load(path: str) -> HolidayTable:
    return parse_csv(Path(path).read_bytes())


def load(path: Path | str | None = None) -> HolidayTable:
    return _load(str(path or config.HOLIDAY_CSV))


@dataclass(frozen=True)
class Due:
    date: date | None
    note: str = ""


def due_date(period_end: date, counting: str, n: int, *, table: HolidayTable | None = None,
             company_holidays: frozenset[date] = frozenset()) -> Due:
    """The due date, or Due(None, "祝日表の範囲外") when a business-day count needs a day the holiday table lacks."""
    if counting == "暦日":
        return Due(period_end + timedelta(days=n))
    table = table or load()
    business = counting == "営業日"
    if business and not table.covers(period_end):
        return Due(None, "祝日表の範囲外")

    def counts(d: date) -> bool:
        if d.weekday() >= 5:
            return False
        if business and (d in table.names or d in company_holidays):
            return False
        return True

    cur, left = period_end, abs(n)
    step = 1 if n > 0 else -1
    while left:
        cur += timedelta(days=step)
        if business and not table.covers(cur):
            return Due(None, "祝日表の範囲外")
        if counts(cur):
            left -= 1
    return Due(cur)
