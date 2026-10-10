from typing import Literal
from pydantic import BaseModel

CONTRACT_VERSION = 1
CONF_OK, CONF_LOW = 0.90, 0.70           # field accepted >= CONF_OK ; doc needs_review if overall < CONF_LOW

HEADER_FIELDS = ["invoice_no","invoice_date","due_date","document_type","currency","place_of_supply",
  "purchase_order_no","reverse_charge","price_includes_tax",
  "supplier_name","supplier_address","supplier_gstin","buyer_name","buyer_address","buyer_gstin",
  "subtotal","discount","taxable_value","cgst","sgst","igst","cess","total_tax","round_off",
  "total_amount","amount_in_words"]
LINE_FIELDS = ["line_no","description","hsn_sac","quantity","unit","unit_price","discount",
  "taxable_value","tax_rate","cgst","sgst","igst","cess","line_total"]
# tax_rate = TOTAL GST rate in percent (e.g. "18"). Intra-state: cgst=sgst=taxable*rate/200. Inter-state: igst=taxable*rate/100.

Status = Literal["accepted","needs_review","corrected","derived","missing","human_verified"]

class BBox(BaseModel):
    page: int                       # 1-based
    x0: float; y0: float; x1: float; y1: float     # normalized 0..1, origin top-left of the page image

class Alt(BaseModel):
    value: str | None; confidence: float; source: str

class FV(BaseModel):                # one extracted field ("FieldValue")
    value: str | None = None        # normalized. money/qty/rate = Decimal STRING ("1180.00"); dates ISO "YYYY-MM-DD"; absent = None (never "" or 0)
    raw: str | None = None          # text as read
    confidence: float = 0.0         # 0..1
    status: Status = "missing"      # accepted if conf>=CONF_OK; needs_review below; corrected/derived set by repair; human_verified by reviewer
    sources: list[str] = []         # qr|excel|csv|pdf_text|ocr|trocr|vlm|vlm_crop|rules|derived|human
    alternatives: list[Alt] = []    # <=3, best first
    source_page: int | None = None  # 1-based page (documents)
    source_ref: str | None = None   # tabular: "Sheet1!R12" (sheet + 1-based row)
    bbox: BBox | None = None
    evidence_text: str | None = None
    note: str | None = None

class Check(BaseModel):
    code: str; severity: Literal["error","warning","info"]; passed: bool
    field_paths: list[str] = []     # e.g. "invoice.total_amount", "line_items[2].taxable_value"
    message: str = ""; expected: str | None = None; actual: str | None = None; difference: str | None = None

class Correction(BaseModel):
    field_path: str; original: str | None; corrected: str | None
    method: str; reason: str; confidence_before: float; confidence_after: float

class Validation(BaseModel):
    status: Literal["valid","valid_with_warnings","needs_review","failed"] = "needs_review"
    score: float = 0.0
    checks: list[Check] = []; corrections: list[Correction] = []; repair_candidates: list[dict] = []

class ReviewEvent(BaseModel):
    ts: str; actor: str; action: Literal["edit","approve","reject","reopen"]
    field_path: str | None = None; old: str | None = None; new: str | None = None

class Review(BaseModel):
    required: bool = True; state: Literal["pending","approved","rejected"] = "pending"
    history: list[ReviewEvent] = []

class DuplicateMatch(BaseModel):
    other_job_id: str; other_doc_index: int
    kind: Literal["exact_file","business_key","near"]; score: float; reasons: list[str]

class RecordSource(BaseModel):
    filename: str; sha256: str; detected_type: str       # detected_type = MIME e.g. "application/pdf"
    input_kind: Literal["xlsx","csv","pdf_digital","pdf_scanned","pdf_mixed","image_printed","image_handwritten","image_mixed"]
    page_range: list[int] | None = None

class Quality(BaseModel):
    overall_confidence: float = 0.0; handwriting_ratio: float = 0.0
    degraded: bool = False; degraded_reasons: list[str] = []
    engines: dict[str, str] = {}; timings_ms: dict[str, int] = {}; page_flags: list[dict] = []

class InvoiceRecord(BaseModel):
    schema_version: str = "1.0"
    record_id: str                                   # uuid4
    source: RecordSource
    invoice: dict[str, FV]                           # keys = HEADER_FIELDS (all present; missing -> FV())
    line_items: list[dict[str, FV]]                  # each dict keys = LINE_FIELDS
    validation: Validation = Validation()
    review: Review = Review()
    quality: Quality = Quality()
    duplicates: list[DuplicateMatch] = []

# ---- Hand-off from Document AI (P2) to extraction (P3) ----
class RawField(BaseModel):                           # one hypothesis for one field from one source
    value: str | None; confidence: float; source: str
    page: int | None = None; bbox: BBox | None = None; evidence_text: str | None = None

class RawInvoice(BaseModel):
    source: str                                      # qr | rules | vlm
    invoice: dict[str, list[RawField]] = {}          # field -> hypotheses (strings as READ, not normalized)
    line_items: list[dict[str, RawField]] = []
    illegible: list[str] = []

class Token(BaseModel):
    text: str; conf: float
    box: list[float]                                 # [x0,y0,x1,y1] normalized 0..1
    page: int; line_id: int
    kind: Literal["printed","handwritten","mixed"] = "printed"
    source: str = "ocr"                              # ocr | pdf_text | trocr

class PageData(BaseModel):
    index: int                                       # 1-based
    kind: Literal["digital","scanned","mixed","skipped"]
    original_path: str; enhanced_path: str           # files in work_dir: page-{n}-original.jpg, page-{n}.jpg
    width: int; height: int
    tokens: list[Token]; layout_text: str            # layout_text: lines "L001| cell | cell"
    flags: list[str] = []; quality: dict = {}

class Segment(BaseModel):                            # one invoice inside the file (page_range inclusive)
    page_range: list[int]; qr: RawInvoice | None = None; rules: RawInvoice | None = None

class Bundle(BaseModel):
    input_kind: Literal["pdf_digital","pdf_scanned","pdf_mixed","image_printed","image_handwritten","image_mixed"]
    pages: list[PageData]; segments: list[Segment]
    handwriting_ratio: float = 0.0
    degraded_reasons: list[str] = []; engines: dict[str, str] = {}; timings_ms: dict[str, int] = {}

class TabularOutput(BaseModel):
    records: list[InvoiceRecord]; table_rows: list[dict]      # flat table rows, columns = Section 0.7 FLAT_COLUMNS
    mapping: list[dict]; unmapped_columns: list[dict]; warnings: list[Check]