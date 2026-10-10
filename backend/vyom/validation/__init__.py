from decimal import Decimal, InvalidOperation
import re
from ..models import Check

def validate(rec, *, repair=True):
    # Keep checks emitted by upstream deterministic stages (e.g. tabular totals and duplicates).
    checks=list(rec.validation.checks)
    inv=rec.invoice
    for key in ("invoice_no", "supplier_gstin", "total_amount"):
        if not inv.get(key) or inv[key].value is None:
            checks.append(Check(code="REQUIRED_FIELD_MISSING",severity="warning",passed=False,field_paths=[f"invoice.{key}"],message=f"{key} is missing"))
    gst=inv.get("supplier_gstin")
    if gst and gst.value and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]",gst.value.upper()):
        checks.append(Check(code="INVALID_GSTIN",severity="error",passed=False,field_paths=["invoice.supplier_gstin"],message="Supplier GSTIN does not match the expected structure"))
    uncertain=[f"invoice.{k}" for k,f in inv.items() if f.value is not None and (f.status=="needs_review" or f.confidence<.70)]
    uncertain += [f"line_items[{i}].{k}" for i,row in enumerate(rec.line_items) for k,f in row.items() if f.value is not None and (f.status=="needs_review" or f.confidence<.70)]
    if uncertain:checks.append(Check(code="UNCERTAIN_FIELDS",severity="warning",passed=False,field_paths=uncertain,message="One or more extracted fields need human review"))
    for side,other in (("cgst","sgst"),):
        a,b=inv.get(side),inv.get(other)
        if a and b and a.value is not None and b.value is not None:
            try:
                if Decimal(a.value)!=Decimal(b.value): checks.append(Check(code="TAX_SPLIT_MISMATCH",severity="warning",passed=False,field_paths=[f"invoice.{side}",f"invoice.{other}"],message="CGST and SGST differ"))
            except InvalidOperation: pass
    # Do not multiply prior validation checks if this function is called again.
    unique={(c.code,tuple(c.field_paths),c.actual,c.expected):c for c in checks}
    checks=list(unique.values())
    rec.validation.checks=checks
    errs=any(c.severity=="error" and not c.passed for c in checks)
    warnings=any(not c.passed for c in checks)
    review_codes={"REQUIRED_FIELD_MISSING","UNCERTAIN_FIELDS"}
    must_review=any(not c.passed and c.code in review_codes for c in checks)
    rec.validation.status="failed" if errs else "needs_review" if must_review else "valid_with_warnings" if warnings else "valid"
    rec.validation.score=max(0.0,1.0-sum(not c.passed for c in checks)*0.1)
    vals=[f.confidence for f in list(inv.values())+ [v for row in rec.line_items for v in row.values()] if f.value is not None]
    rec.quality.overall_confidence=sum(vals)/len(vals) if vals else 0.0
    if rec.quality.overall_confidence < .70: rec.validation.status="needs_review"
    return rec
