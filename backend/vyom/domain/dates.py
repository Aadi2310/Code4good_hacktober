from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .common import ascii_digits, clean_text

_MONTHS = {m.lower(): i for i, m in enumerate(("January February March April May June July August September October November December").split(), 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def parse_date(s: str | None, *, today: date | None = None) -> tuple[str | None, list[str]]:
    if s is None:
        return None, ["DATE_MISSING"]
    text = ascii_digits(clean_text(str(s))).replace(",", " ")
    text = re.sub(r"(?i)(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    notes: list[str] = []
    parsed: date | None = None
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            pass
    else:
        m = re.fullmatch(r"(\d{1,2})[ /.-]+(\d{1,2})[ /.-]+(\d{2,4})", text)
        if m:
            day, month, year = map(int, m.groups())
            if year < 100:
                year += 2000 if year <= 69 else 1900
            if month > 12 and 1 <= day <= 12:
                day, month = month, day
            elif day <= 12 and month <= 12 and day != month:
                notes.append("DATE_AMBIGUOUS_DAY_FIRST")
            try:
                parsed = date(year, month, day)
            except ValueError:
                pass
        else:
            m = re.fullmatch(r"(\d{1,2})[ -]+([A-Za-z]+)[ -]+(\d{2,4})", text)
            if m:
                day, mon, year = m.groups()
                month = _MONTHS.get(mon.lower())
                y = int(year)
                y = y + (2000 if y <= 69 else 1900) if y < 100 else y
                try:
                    parsed = date(y, month, int(day)) if month else None
                except ValueError:
                    pass
    if parsed is None:
        return None, notes + ["DATE_INVALID"]
    if parsed < date(2017, 7, 1):
        notes.append("DATE_BEFORE_GST")
    current = today or date.today()
    if parsed > current + timedelta(days=1):
        notes.append("DATE_IN_FUTURE")
    if parsed < current.replace(year=current.year - 8):
        notes.append("DATE_VERY_OLD")
    return parsed.isoformat(), notes


def fy(value: date) -> str:
    start = value.year if value.month >= 4 else value.year - 1
    return f"{start}-{(start + 1) % 100:02d}"
