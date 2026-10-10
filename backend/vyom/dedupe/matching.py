"""Auditable duplicate matching; every match is a suggestion for human review."""
from datetime import date
from decimal import Decimal,InvalidOperation
import re
from rapidfuzz import fuzz
from ..models import DuplicateMatch

def _value(record,name):
    f=record.invoice.get(name)
    return f.value if f else None
def _compact(value):return re.sub(r"[^A-Z0-9]","",str(value or "").upper())
def _date(value):
    try:return date.fromisoformat(str(value)[:10])
    except (TypeError,ValueError):return None
def _money(value):
    try:return Decimal(str(value)) if value is not None else None
    except InvalidOperation:return None
def find(rec,repo,*,exclude_job_id=None,other_records=()):
    gst=_compact(_value(rec,"supplier_gstin"));inv=_compact(_value(rec,"invoice_no"));dt=_date(_value(rec,"invoice_date"));total=_money(_value(rec,"total_amount"));name=_compact(_value(rec,"supplier_name"));sha=rec.source.sha256
    candidates=list(repo.candidates(exclude_job_id=exclude_job_id)) if repo is not None else []
    for job_id,index,other in other_records:
        candidates.append({"job_id":job_id,"doc_index":index,"sha256":other.source.sha256,"supplier_gstin":_compact(_value(other,"supplier_gstin")),"invoice_no_norm":_compact(_value(other,"invoice_no")),"invoice_date":_value(other,"invoice_date"),"total_amount":_value(other,"total_amount"),"supplier_name":_compact(_value(other,"supplier_name"))})
    matches=[];seen=set()
    for c in candidates:
        job=str(c.get("job_id",c.get("other_job_id","")));index=int(c.get("doc_index",c.get("other_doc_index",0)));identity=(job,index)
        if identity in seen:continue
        seen.add(identity);ogst=_compact(c.get("supplier_gstin"));oinv=_compact(c.get("invoice_no_norm",c.get("invoice_no")));odt=_date(c.get("invoice_date"));ototal=_money(c.get("total_amount"));oname=_compact(c.get("supplier_name"));osha=c.get("sha256")
        reasons=[];kind=None;score=0
        if sha and osha and sha==osha and job!=exclude_job_id:
            kind="exact_file";score=1.;reasons.append("same uploaded file SHA-256")
        elif gst and inv and ogst==gst and oinv==inv and ((dt and odt and dt==odt) or (total is not None and ototal is not None and total==ototal)):
            kind="business_key";score=.95
            reasons.append("same supplier GSTIN and normalized invoice number")
            if dt and odt and dt==odt:reasons.append("same invoice date")
            elif total is not None and ototal is not None:reasons.append("same invoice total")
        elif gst and ogst==gst and inv and oinv and dt and odt and total is not None and ototal is not None:
            day_diff=abs((dt-odt).days);amount_diff=abs(total-ototal);ratio=fuzz.ratio(inv,oinv)
            if day_diff<=3 and amount_diff<=Decimal("0.01") and ratio>=85:
                kind="near";score=max(.60,min(.90,ratio/100));reasons.extend(["same supplier GSTIN","invoice totals differ by at most 0.01",f"invoice dates are {day_diff} day(s) apart",f"invoice number similarity {ratio}%"])
        elif not gst and not ogst and inv and oinv and name and oname:
            ratio=fuzz.ratio(inv,oinv);name_ratio=fuzz.ratio(name,oname)
            if ratio>=85 and name_ratio>=90 and (total is None or ototal is None or abs(total-ototal)<=Decimal("0.01")):
                kind="near";score=max(.60,min(.90,(ratio+name_ratio)/200));reasons.extend([f"invoice number similarity {ratio}%",f"supplier name similarity {name_ratio}%","GSTIN unavailable"])
        if kind:matches.append(DuplicateMatch(other_job_id=job,other_doc_index=index,kind=kind,score=score,reasons=reasons))
    return matches
