# VYOM+ — End-to-End AI-Powered GST Invoice Intelligence System

> **Qualifier proposal:** This repository documents the proposed system. The qualifier requires a README-only submission; implementation, datasets, and notebooks belong in the final hackathon deliverable.

## 1. Project Name

**VYOM+** — an end-to-end invoice intelligence system for extracting, validating, reviewing, and exporting GST invoice data.

## 2. Problem Statement

Businesses receive invoices as spreadsheets, digital PDFs, scans, photographs, and handwritten documents. Manual entry is slow and error-prone. Basic OCR can read text but often loses the relationship between labels, values, and line items, and it does not establish whether GST fields or totals are consistent.

VYOM+ addresses this gap by turning supported invoice files into evidence-backed financial records, checking those records, and routing uncertain or inconsistent values for review before export.

## 3. Project Overview

VYOM+ is a GST-focused document-intelligence proposal. It accepts `.xlsx`, `.csv`, `.pdf`, `.jpg`, `.jpeg`, and `.png` files and routes structured tables and visual documents through suitable processing paths.

The planned output contains invoice headers, supplier and buyer details, GST identifiers, tax values, line items, validation results, confidence, and page or row provenance. The intended interface lets a reviewer inspect source evidence, correct flagged values, and export approved records as JSON or tabular data.

## 4. Proposed Solution

Use deterministic parsers for CSV and Excel, embedded-text extraction for digital PDFs, and an open-source OCR/document-AI pipeline for scans and images. A vision-language model handles difficult page-level interpretation and proposes structured fields. Python validation code then checks formats, relationships, and financial arithmetic.

Every field retains its source evidence and confidence. Missing or unreadable values stay null. Mismatches are reported with computed values; the system does not silently rewrite source amounts. Records below configurable confidence or validation thresholds enter a human review queue.

### Uniqueness

VYOM+ treats invoice processing as a financial data-quality workflow, not a text-recognition demo. Its distinguishing design combines:

- **Evidence-backed extraction:** each value points to its source page, row, text, and—where available—bounding box.
- **Field-level uncertainty:** a confident invoice number can pass while a weak GSTIN or handwritten amount is held for review.
- **GST-aware reconciliation:** line-item taxable values and CGST, SGST, IGST, cess, and totals are connected and checked with decimal arithmetic.
- **Hybrid routing:** spreadsheets avoid unnecessary model calls; OCR and visual understanding are used where the document requires them.
- **Safe correction loop:** reviewer edits are auditable and can form a versioned evaluation set before any model or rule is changed.

Potential extensions include an invoice confidence heatmap, supplier-specific field mapping, a visual GST roll-up graph, explainable supplier-period anomaly alerts, and assisted splitting of combined PDF batches. These are future ideas, not claimed MVP features.

## 5. Objectives

- Automatically identify supported file formats and route them to the right parser.
- Extract invoice, GST, financial, and line-item fields into a canonical schema.
- Prioritize reliable handling of handwritten invoices while maintaining printed and digital document accuracy.
- Validate GSTIN shape, tax relationships, and invoice arithmetic.
- Make uncertain values reviewable with confidence and source evidence.
- Detect exact duplicates and surface likely near-duplicates for confirmation.
- Export reviewed records in versioned JSON and CSV/XLSX formats.

## 6. Target Users / Use Case

**Target users:** accounts payable and receivable teams, small-business finance teams, accountants, and developers building GST-oriented accounting or procurement workflows.

**Primary use case:** a finance operator uploads a batch of invoices, checks extracted fields and reconciliation flags in a review screen, corrects uncertain values, then exports approved records for downstream accounting.

**Supported documents:** spreadsheet transactions, text-based or scanned PDFs (including multi-page and multi-invoice PDFs), and invoice photographs. English and regional scripts are supported only to the extent validated for the selected OCR/VLM model.

## 7. Open-Source AI Technology Selected

| Component | Proposed selection | Role |
|---|---|---|
| OCR and layout | PaddleOCR | Open-source text detection and recognition for printed/scanned pages; evaluate script and handwriting support on the project corpus. |
| Visual document extraction | Qwen2-VL-2B-Instruct, subject to license and hardware verification | Open-weight vision-language model to interpret page layout and propose invoice fields from difficult documents. |
| Handwriting recognition candidate | TrOCR handwritten checkpoints | Evaluate for handwritten text regions; use only if its accuracy and license fit the invoice languages and deployment constraints. |

PaddleOCR and Qwen2-VL form the proposed MVP AI path. TrOCR is an evaluated candidate, not a dependency until benchmark results justify it. Model checkpoints and licenses must be verified before distribution or deployment. The system must not depend solely on a hosted-model API.

## 8. Why This Technology Was Selected

- **PaddleOCR:** provides a practical open-source OCR pipeline that can run locally and return text with page positions, which supports source-grounded review.
- **Qwen2-VL-2B-Instruct:** a comparatively compact open-weight VLM that can use visual layout context to associate labels, amounts, and table cells; the small variant is a more realistic hackathon inference target than a large hosted-only model.
- **TrOCR evaluation:** transformer-based recognition is a relevant handwriting candidate, but handwriting is too variable to promise accuracy without testing on representative GST invoices.
- **Deterministic validation alongside AI:** arithmetic and schema rules are easier to audit in code than in model-generated reasoning, so model output is treated as a candidate, never as the final authority.

Selection is driven by local execution, visual document handling, evidence retention, and hackathon feasibility. A labeled sample set must decide whether the VLM adds enough accuracy to justify its latency and compute cost.

## 9. AI's Role in the System

AI is used for text recognition, layout interpretation, handwriting candidate evaluation, and mapping visual evidence to canonical invoice fields. The model receives a rendered page or OCR/layout evidence and returns schema-constrained candidate values with evidence references where possible.

AI does **not** determine whether a tax treatment is legally correct, verify GST registration status, silently repair amounts, or approve a low-confidence record. Python validation applies explicit rules; a human resolves ambiguous values and exceptions.

## 10. System Architecture

<p align="center">
  <img src="https://github.com/Aadi2310/vyom-gst-invoice-intelligence/releases/download/v1.0.0/vyom-system-architecture.gif" width="100%" alt="VYOM+ system architecture with animated invoice flow, validation, storage, and human review">
</p>

**How to read the diagram:** Supported files enter through the upload UI and are routed by detected content. Spreadsheet files use a streaming parser, while PDFs and images go through page preparation and OCR/layout extraction. Both paths join at field extraction and normalization, then pass through GST checks, arithmetic reconciliation, and duplicate detection. Records and source evidence are stored separately; flagged records go to the reviewer before export.

## 11. Component-Level Architecture

| Component | Responsibility | Proposed technology |
|---|---|---|
| Upload and inspection UI | Upload batches, show job state, display source evidence, and let reviewers correct fields | React, TypeScript, PDF.js |
| API and schema boundary | Validate requests, create jobs, expose status and export endpoints | Python, FastAPI, Pydantic |
| File router | Detect content from signatures and route independently of filename extension | `libmagic` or signature inspection plus MIME checks |
| Tabular parser | Stream CSV rows and process `.xlsx` sheets while preserving row/sheet provenance | Python `csv`, `openpyxl` read-only mode |
| PDF and image preparation | Extract embedded PDF text; render pages; correct orientation and skew; assess image quality | PyMuPDF, Pillow/OpenCV |
| OCR and layout | Detect text and regions and return coordinates and confidence | PaddleOCR |
| Field extraction | Associate OCR/layout evidence with invoice schema fields | Qwen2-VL-2B-Instruct with constrained output |
| Validation and reconciliation | Check types, GSTIN shape, tax fields, line sums, invoice totals, and tolerances | Pydantic, Python `Decimal`, explicit GST rules |
| Duplicate detection | Find exact checksums and likely business-key/document matches | PostgreSQL indexes plus normalized identifiers and similarity features |
| Job processing | Run document tasks asynchronously, retry transient errors, and track state | Celery and Redis |
| Persistence and artifacts | Store versioned records separately from originals and page evidence | PostgreSQL; S3-compatible object storage such as MinIO |
| Monitoring | Track job failures, latency, model usage, review rate, and cost | OpenTelemetry, Prometheus, Grafana |

## 12. Data / Information Flow

1. Accept a file and record its checksum, declared extension, detected type, and job ID.
2. Store the original artifact and enforce file, page, and processing limits.
3. Route CSV/XLSX to streaming parsers; route PDFs/images to text extraction, rendering, and preprocessing as needed.
4. Run PaddleOCR on visual pages and retain recognized text, coordinates, and OCR confidence.
5. Ask the selected VLM to map page evidence into candidate invoice fields and line items using the canonical schema.
6. Normalize dates, currency, decimal notation, GSTIN text, and HSN/SAC while preserving source text.
7. Apply deterministic schema, GST-format, tax arithmetic, and total reconciliation checks.
8. Score exact/near duplicate candidates and attach explanations and match features.
9. Persist extracted values, evidence, confidence, validation outcomes, model/rule versions, and review history.
10. Present uncertain or inconsistent records for review; export only according to the configured approval policy.

### Canonical fields

| Group | Fields |
|---|---|
| Invoice | `invoice_no`, `invoice_date`, `due_date`, `currency`, `place_of_supply`, `purchase_order_no`, `reverse_charge` |
| Supplier | `supplier_name`, `supplier_address`, `supplier_gstin` |
| Buyer | `buyer_name`, `buyer_address`, `buyer_gstin` |
| Line item | `description`, `hsn_sac`, `quantity`, `unit`, `unit_price`, `discount`, `taxable_value`, `tax_rate`, `cgst`, `sgst`, `igst`, `cess`, `line_total` |
| Invoice totals | `subtotal`, `discount`, `taxable_value`, `total_tax`, `round_off`, `total_amount` |
| Provenance and review | `confidence`, `validation_status`, `review_status`, `source_page_or_row`, `bounding_box`, `evidence_text` |

## 13. Agentic Workflow (if applicable)

The MVP does not require autonomous agents. Invoice extraction and financial validation benefit from a fixed, auditable sequence; an open-ended agent would add unpredictable tool calls without improving the core accounting checks.

The planned workflow is an orchestrated pipeline of bounded steps: detect → parse/OCR → extract → normalize → validate → review/export. Each step has typed inputs and outputs, retry rules, and a recorded status. A later version may add a narrowly scoped review assistant that explains validation failures, but it must not change financial fields or approve records autonomously.

## 14. Technology Stack

| Layer | Choice | Why it fits |
|---|---|---|
| Frontend | React, TypeScript, PDF.js | Supports an accessible upload/review UI and page-linked evidence. |
| Backend | Python 3.12, FastAPI, Pydantic | Integrates the Python document-AI ecosystem and provides typed API contracts. |
| File routing | `libmagic`/signature checks | Detects actual file content and catches mislabeled extensions. |
| Tabular processing | Python `csv`, `openpyxl` read-only mode | Processes large files incrementally and supports multi-sheet workbooks. |
| PDF/image | PyMuPDF, Pillow/OpenCV | Extracts digital text, renders pages, and supports image cleanup. |
| OCR | PaddleOCR; Tesseract fallback only if evaluation supports it | Local OCR with coordinates; fallback can trade accuracy for simpler CPU deployment. |
| VLM | Qwen2-VL-2B-Instruct | Open-weight visual extraction candidate; verify license, memory use, and measured accuracy. |
| Validation | Pydantic, Python `Decimal`, versioned GST checks | Keeps schemas and monetary arithmetic deterministic and auditable. |
| Database | PostgreSQL with JSONB | Stores queryable invoice records, review state, and versioned extraction payloads. |
| Artifact storage | S3-compatible storage / MinIO | Keeps source files and rendered evidence out of relational rows. |
| Queue | Celery, Redis | Runs long jobs asynchronously with retries and worker scaling. |
| Deployment | Docker Compose for the demo; container deployment for scale | Reproducible local setup with a clear deployment path. |
| Observability | OpenTelemetry, Prometheus, Grafana | Measures latency, errors, queue depth, model load, and review outcomes. |

<p align="center">
  <img src="https://github.com/Aadi2310/vyom-gst-invoice-intelligence/releases/download/v1.0.0/vyom-techstack.gif" width="100%" alt="VYOM+ technology stack with subtle animated data-flow and processing highlights">
</p>

**How to read the diagram:** This figure groups the proposed tools by system responsibility and shows how they surround the canonical invoice schema. PaddleOCR and Qwen2-VL are the proposed MVP AI components; Tesseract, TrOCR, and LayoutLMv3 are alternatives or evaluation candidates, not mandatory MVP dependencies. Technology choices remain subject to license checks and benchmarking on representative GST invoices.

### Animation and Preview

The architecture and technology-stack figures are animated GIFs attached to the public `v1.0.0` GitHub Release. GitHub renders them directly in this README; the repository itself can therefore remain README-only. Open either figure in a browser to view its loop, or download the GIF from the [release assets](https://github.com/Aadi2310/vyom-gst-invoice-intelligence/releases/tag/v1.0.0).

## 15. Expected Features

- Multi-file upload with progress and per-file processing status.
- Automatic signature-based routing for `.xlsx`, `.csv`, `.pdf`, `.jpg`, `.jpeg`, and `.png`.
- OCR/layout extraction and field mapping for printed, digital, and handwritten invoice candidates.
- Invoice header and line-item extraction with page/row evidence and confidence.
- Editable review screen with validation messages and source-page highlighting.
- GSTIN format checks, tax-component checks, and configurable arithmetic tolerances.
- Total reconciliation that reports variance without silently changing extracted values.
- Exact duplicate detection and reviewable near-duplicate suggestions.
- Versioned JSON output and CSV/XLSX export after the configured review gate.
- Job history and audit trail for extraction, validation, reviewer corrections, and export.

## 16. Implementation Approach

The final hackathon implementation should be delivered in four practical milestones:

1. **Working slice:** implement upload, file routing, CSV/XLSX parsing, canonical schema, a simple UI, and JSON export.
2. **Document path:** add PDF text extraction, page rendering, image preprocessing, PaddleOCR, and evidence-linked field extraction.
3. **Validation and review:** add GST/Decimal checks, confidence thresholds, review corrections, duplicate candidates, and audit history.
4. **Evaluation and demo hardening:** benchmark representative files, measure latency and review rate, package with Docker, and document limitations.

Keep extraction, validation, and export behind separate interfaces so model changes do not alter accounting rules or the output contract. Start with one representative handwritten and printed dataset; expand only after the end-to-end slice works. Do not claim production accuracy without measured evaluation.

## 17. Expected Final Output

The system should emit one versioned JSON record per invoice, with line items, field confidence, source evidence, and validation status. Monetary values should be serialized as decimal strings to avoid binary floating-point ambiguity.

```json
{
  "schema_version": "1.0",
  "source": {"filename": "invoice.pdf", "detected_type": "application/pdf"},
  "invoice": {
    "invoice_no": {"value": "INV-1042", "confidence": 0.98, "status": "accepted", "source_page": 1},
    "invoice_date": {"value": "2026-04-12", "confidence": 0.94, "status": "accepted", "source_page": 1},
    "supplier_gstin": {"value": null, "confidence": 0.31, "status": "needs_review", "source_page": 1},
    "buyer_gstin": {"value": "27ABCDE1234F1Z5", "confidence": 0.99, "status": "accepted"},
    "currency": "INR",
    "taxable_value": "1000.00",
    "cgst": "90.00",
    "sgst": "90.00",
    "igst": "0.00",
    "total_amount": "1180.00"
  },
  "line_items": [{
    "description": "Consulting services", "hsn_sac": "998314", "quantity": "1",
    "taxable_value": "1000.00", "tax_rate": "18.00", "cgst": "90.00",
    "sgst": "90.00", "igst": "0.00", "line_total": "1180.00"
  }],
  "validation": {"status": "needs_review", "checks": [
    {"code": "GSTIN_FORMAT", "status": "passed"},
    {"code": "TOTAL_RECONCILIATION", "status": "passed", "variance": "0.00"}
  ]},
  "review": {"required": true, "state": "pending"}
}
```

CSV/XLSX export should flatten invoice-level fields and provide a separate line-item table or stable invoice key so repeated invoice header values do not obscure item rows.

## 18. Future Scope / Scalability

- Add validated language packs and handwriting fine-tuning using permissioned, labeled examples.
- Learn supplier-specific aliases and layouts from approved corrections, with tenant isolation and reviewer control.
- Add assisted links between invoices, purchase orders, credit notes, and debit notes.
- Add a confidence heatmap, GST roll-up visualization, and explainable period-over-period anomaly alerts.
- Scale API and workers independently; use GPU worker pools for VLM inference and queue-based backpressure.
- Add accounting/ERP connectors and permitted GSTIN status checks where authoritative interfaces and access allow.
- Add tenant-level retention, regional storage, and model-provider policies.

## 19. Open-Source Dependencies / Components

| Component | Selection / verification note | Planned use |
|---|---|---|
| PaddleOCR | Use the upstream project and verify selected model/checkpoint terms | OCR and layout recognition |
| Qwen2-VL model/checkpoint | Verify the exact checkpoint terms; open-weight does not automatically mean OSI-approved open source | Visual field extraction candidate |
| TrOCR checkpoints | Verify checkpoint and training-data terms individually | Optional handwriting benchmark |
| FastAPI and Pydantic | Verify pinned package versions and transitive dependencies | API framework and schema validation |
| PyMuPDF | Review its current licensing model for compatibility before adoption | PDF text and rendering; choose a compatible alternative if needed |
| OpenCV and Pillow | Verify the exact package and version | Image preprocessing |
| PostgreSQL | Verify distribution and extension terms for the deployment | Structured storage |
| Celery and Redis/Valkey | Verify versions and distribution terms; select a broker with compatible terms | Background job processing |
| React and PDF.js | Verify pinned package versions and transitive dependencies | Review UI and in-browser PDF inspection |

Pin dependency versions and review model, training-data, and transitive dependency terms before implementation. This list describes proposed components, not installed dependencies.

## 20. Expected Challenges and Mitigation

| Challenge / edge case | Mitigation |
|---|---|
| Handwritten versus printed versus digital invoices | Use embedded PDF text for reliable digital text, OCR for scans, and benchmark handwriting-specific recognition; lower confidence and request review for uncertain handwriting. |
| Rotated, skewed, low-resolution, or blurred scans | Estimate page quality, correct orientation and skew, use conservative enhancement, retain originals, and flag unreadable fields. |
| Multi-page PDFs and PDFs containing multiple invoices | Preserve page provenance; propose invoice boundaries using identifiers and repeated headers; request review when split confidence is low. |
| Mixed English and regional scripts | Detect scripts where possible and use only evaluated language support; preserve source text and flag unsupported or uncertain content. |
| Missing or illegible GSTIN, HSN/SAC, or tax fields | Return null with missing/illegible status and evidence; never infer a value from unrelated fields. |
| Line-item sum differs from invoice total | Compute and show variance with configurable rounding tolerance; do not auto-adjust source amounts. |
| Duplicate and near-duplicate invoices | Detect exact file/business-key matches; present similarity candidates with reasons for human confirmation. |
| Currency ambiguity | Preserve currency text and require configured currency or review before cross-document amount comparisons. |
| Date-format ambiguity | Preserve source representation; apply tenant locale only when configured; otherwise show possible interpretations for review. |
| Number-format ambiguity | Avoid guessing decimal and digit grouping (for example `1,234`); keep raw text and request locale or human confirmation. |
| Corrupt, password-protected, unsupported, or mislabeled files | Verify signatures, route supported content by detected type, reject corrupt/unsupported files with reason codes, and request an accessible password-free PDF. |
| Very large CSV/XLSX files | Stream rows and sheets, set configurable row/size/time limits, and report progress without loading entire workbooks into memory. |
| Empty rows, merged cells, and multiple sheets | Skip empty rows, preserve sheet and row coordinates, inspect merged ranges, and flag layouts where a value-to-header mapping is ambiguous. |
| Low-confidence fields | Expose field-level confidence and evidence; route below-threshold values and affected records to human review. |
| Model hallucination or malformed output | Constrain output to schema, require evidence references, reject invalid structures, and run deterministic validation before persistence or export. |
| Model latency, memory, or cost | Benchmark the 2B candidate on target hardware, batch within memory limits, route simple documents around the VLM, and measure cost/latency per page. |
| Privacy and sensitive invoice data | Prefer local inference; restrict access, encrypt artifacts, set retention/deletion policies, and avoid invoice contents in logs. |
| GST rules and legal interpretation | Treat implemented rules as configurable validation checks, not tax advice; have domain reviewers verify current requirements. |

## References

- Hacktober Fest Technical Document and Guidelines (provided for this proposal) — qualifier format, required README sections, final-project expectations, and evaluation criteria.
- [GSTN — Goods and Services Tax Network](https://www.gstn.org.in/) — GST system information.
- [CBIC GST Portal](https://www.gst.gov.in/) — official GST portal.
- [GST Invoice Rules, CGST Rules 2017](https://cbic-gst.gov.in/pdf/CGST-Rules-2017-Updated.pdf) — invoice particulars; verify current applicable rules before implementation.
- [InvoiceNet](https://arxiv.org/abs/1908.01851) — invoice information extraction research.
- [LayoutLMv3](https://arxiv.org/abs/2204.08387) — document AI using text and visual layout.
- [Donut](https://arxiv.org/abs/2111.15664) — OCR-free document understanding research.
- [TrOCR](https://arxiv.org/abs/2109.10282) — transformer text recognition research.
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — open-source OCR toolkit.
- [Qwen2-VL](https://github.com/QwenLM/Qwen2-VL) — model repository and documentation; check selected checkpoint terms.
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF extraction and rendering documentation.
- [FastAPI](https://fastapi.tiangolo.com/) — API framework documentation.
- [OpenTelemetry](https://opentelemetry.io/docs/) — observability documentation.
