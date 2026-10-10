"""Small P1 fallback normalizers; replace with P3 domain implementation at integration."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date
import re

def parse_money(value):
    if value is None: return None, ["missing"]
    s = str(value).strip().replace("₹", "").replace(",", "").replace("/-", "")
    s = re.sub(r"(?i)\b(?:INR|Rs\.?|Rupees)\b", "", s).strip()
    try: return format(Decimal(s).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP), ".2f"), []
    except (InvalidOperation, ValueError): return None, ["invalid_money"]

def parse_date(value):
    if not value: return None, ["missing"]
    s=str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try: return datetime.strptime(s,fmt).date().isoformat(), []
        except ValueError: pass
    return None,["invalid_date"]

def normalize_field(name, raw, *, locale="in"):
    if raw is None or str(raw).strip()=="": return None,["missing"]
    s=str(raw).strip()
    if name in {"subtotal","discount","taxable_value","cgst","sgst","igst","cess","total_tax","round_off","total_amount","quantity","unit_price","tax_rate","line_total"}:
        if name not in {"quantity","tax_rate"}:return parse_money(s)
        try:
            value=Decimal(s.replace("%", "").strip())
            if name=="quantity":value=value.quantize(Decimal("0.0001"),rounding=ROUND_HALF_UP).normalize()
            elif name=="tax_rate":value=value.quantize(Decimal("0.001"),rounding=ROUND_HALF_UP).normalize()
            return format(value,"f"),[]
        except InvalidOperation:return None,["invalid_number"]
    if name in {"invoice_date","due_date"}: return parse_date(s)
    if "gstin" in name: return s.upper(), ([] if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]",s.upper()) else ["invalid_gstin"])
    return s,[]
def _decimal_ok(s):
    try: Decimal(s); return True
    except InvalidOperation: return False
