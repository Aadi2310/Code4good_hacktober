"""Shared VYOM+ contract models (Contract v1)."""
from typing import Literal
from pydantic import BaseModel, Field

CONTRACT_VERSION = 1
CONF_OK, CONF_LOW = 0.90, 0.70
HEADER_FIELDS = ["invoice_no", "invoice_date", "due_date", "document_type", "currency", "place_of_supply", "purchase_order_no", "reverse_charge", "price_includes_tax", "supplier_name", "supplier_address", "supplier_gstin", "buyer_name", "buyer_address", "buyer_gstin", "subtotal", "discount", "taxable_value", "cgst", "sgst", "igst", "cess", "total_tax", "round_off", "total_amount", "amount_in_words"]
LINE_FIELDS = ["line_no", "description", "hsn_sac", "quantity", "unit", "unit_price", "discount", "taxable_value", "tax_rate", "cgst", "sgst", "igst", "cess", "line_total"]
FLAT_COLUMNS = ["invoice_no", "invoice_date", "supplier_name", "supplier_gstin", "buyer_name", "buyer_gstin", "place_of_supply", "line_no", "description", "hsn_sac", "quantity", "unit", "unit_price", "discount", "taxable_value", "tax_rate", "cgst", "sgst", "igst", "cess", "line_total", "invoice_total", "source_sheet", "source_row", "extra"]
Status = Literal["accepted", "needs_review", "corrected", "derived", "missing", "human_verified"]

class BBox(BaseModel):
    page: int
    x0: float; y0: float; x1: float; y1: float
class Alt(BaseModel):
    value: str | None = None; confidence: float = 0.0; source: str = ""
class FV(BaseModel):
    value: str | None = None; raw: str | None = None; confidence: float = 0.0
    status: Status = "missing"; sources: list[str] = Field(default_factory=list)
    alternatives: list[Alt] = Field(default_factory=list); source_page: int | None = None
    source_ref: str | None = None; bbox: BBox | None = None; evidence_text: str | None = None; note: str | None = None
class Check(BaseModel):
    code: str; severity: Literal["error", "warning", "info"]; passed: bool
    field_paths: list[str] = Field(default_factory=list); message: str = ""
    expected: str | None = None; actual: str | None = None; difference: str | None = None
class Correction(BaseModel):
    field_path: str; original: str | None = None; corrected: str | None = None
    method: str = ""; reason: str = ""; confidence_before: float = 0; confidence_after: float = 0
class Validation(BaseModel):
    status: Literal["valid", "valid_with_warnings", "needs_review", "failed"] = "needs_review"
    score: float = 0; checks: list[Check] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list); repair_candidates: list[dict] = Field(default_factory=list)
class ReviewEvent(BaseModel):
    ts: str; actor: str; action: Literal["edit", "approve", "reject", "reopen"]
    field_path: str | None = None; old: str | None = None; new: str | None = None
class Review(BaseModel):
    required: bool = True; state: Literal["pending", "approved", "rejected"] = "pending"
    history: list[ReviewEvent] = Field(default_factory=list)
class DuplicateMatch(BaseModel):
    other_job_id: str; other_doc_index: int; kind: Literal["exact_file", "business_key", "near"]
    score: float; reasons: list[str] = Field(default_factory=list)
class RecordSource(BaseModel):
    filename: str; sha256: str; detected_type: str
    input_kind: Literal["xlsx", "csv", "pdf_digital", "pdf_scanned", "pdf_mixed", "image_printed", "image_handwritten", "image_mixed"]
    page_range: list[int] | None = None
class Quality(BaseModel):
    overall_confidence: float = 0; handwriting_ratio: float = 0; degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list); engines: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict); page_flags: list[dict] = Field(default_factory=list)
class InvoiceRecord(BaseModel):
    schema_version: str = "1.0"; record_id: str; source: RecordSource
    invoice: dict[str, FV]; line_items: list[dict[str, FV]] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation); review: Review = Field(default_factory=Review)
    quality: Quality = Field(default_factory=Quality); duplicates: list[DuplicateMatch] = Field(default_factory=list)
class TabularOutput(BaseModel):
    records: list[InvoiceRecord]; table_rows: list[dict]; mapping: list[dict]
    unmapped_columns: list[dict] = Field(default_factory=list); warnings: list[Check] = Field(default_factory=list)

# ---- Hand-off from Document AI (P2) to extraction (P3) ----
class RawField(BaseModel):
    """One source hypothesis for a field; value is the text as read."""
    value: str | None
    confidence: float
    source: str
    page: int | None = None
    bbox: BBox | None = None
    evidence_text: str | None = None

class RawInvoice(BaseModel):
    source: str
    invoice: dict[str, list[RawField]] = Field(default_factory=dict)
    line_items: list[dict[str, RawField]] = Field(default_factory=list)
    illegible: list[str] = Field(default_factory=list)

class Token(BaseModel):
    text: str
    conf: float
    box: list[float]
    page: int
    line_id: int
    kind: Literal["printed", "handwritten", "mixed"] = "printed"
    source: str = "ocr"

class PageData(BaseModel):
    index: int
    kind: Literal["digital", "scanned", "mixed", "skipped"]
    original_path: str
    enhanced_path: str
    width: int
    height: int
    tokens: list[Token] = Field(default_factory=list)
    layout_text: str
    flags: list[str] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)

class Segment(BaseModel):
    page_range: list[int]
    qr: RawInvoice | None = None
    rules: RawInvoice | None = None

class Bundle(BaseModel):
    input_kind: Literal["pdf_digital", "pdf_scanned", "pdf_mixed", "image_printed", "image_handwritten", "image_mixed"]
    pages: list[PageData]
    segments: list[Segment]
    handwriting_ratio: float = 0.0
    degraded_reasons: list[str] = Field(default_factory=list)
    engines: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict)
