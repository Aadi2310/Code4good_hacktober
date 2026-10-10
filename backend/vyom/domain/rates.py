from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .common import ascii_digits, clean_text, load_reference, numeric_lookalikes


def parse_rate(value: str | None) -> tuple[str | None, list[str]]:
    if value is None or not clean_text(str(value)):
        return None, ["RATE_MISSING"]
    text = ascii_digits(clean_text(str(value))).replace("%", "").replace(",", ".").strip()
    text, _ = numeric_lookalikes(text)
    try:
        rate = Decimal(text)
    except InvalidOperation:
        return None, ["RATE_INVALID"]
    if rate < 0 or rate > 100:
        return None, ["RATE_INVALID"]
    rates = [Decimal(str(x)) for x in load_reference("gst_rates.yaml")["all_known"]]
    nearest = min(rates, key=lambda known: abs(known - rate))
    if abs(nearest - rate) <= Decimal(str(load_reference("gst_rates.yaml")["snap_tolerance"])):
        rate = nearest
    return format(rate.normalize(), "f"), []
