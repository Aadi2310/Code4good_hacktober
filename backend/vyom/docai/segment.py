"""Page grouping for continued invoices and repeated copy pages."""

from __future__ import annotations

import re
from collections import Counter

from vyom.models import PageData, RawInvoice, Segment
from vyom.extraction.rules import extract_rules


_PAGE_MARKER = re.compile(r"\bpage\s*(\d+)\s*(?:of|/)\s*(\d+)\b", re.IGNORECASE)
_INVOICE_TITLE = re.compile(r"\b(?:tax\s+)?invoice\b", re.IGNORECASE)
_COPY = re.compile(r"\b(?:original for recipient|duplicate for transporter|triplicate for supplier|office copy|customer copy)\b", re.IGNORECASE)


def segment_pages(pages: list[PageData], qrs: dict[int, list[RawInvoice]]) -> list[Segment]:
    """Group adjacent pages by invoice fingerprints; retain copy disagreements as hypotheses."""
    active = [page for page in pages if page.kind != "skipped"]
    if not active:
        return []
    segments: list[Segment] = []
    group: list[PageData] = []
    previous_invoice: str | None = None
    previous_gstin: str | None = None
    for page in active:
        one_page_rules = extract_rules([page])
        invoice_no = _first(one_page_rules, "invoice_no") or _qr_first(qrs.get(page.index, []), "invoice_no")
        supplier = _first(one_page_rules, "supplier_gstin") or _qr_first(qrs.get(page.index, []), "supplier_gstin")
        text = page.layout_text
        marker = _PAGE_MARKER.search(text)
        explicit_continuation = bool(marker and int(marker.group(1)) > 1)
        starts_invoice = bool(_INVOICE_TITLE.search(text)) and bool(invoice_no)
        changed = bool(group and ((invoice_no and previous_invoice and invoice_no != previous_invoice)
                                  or (supplier and previous_gstin and supplier != previous_gstin)))
        same_fingerprint = bool(group and invoice_no and invoice_no == previous_invoice and supplier == previous_gstin)
        if group and changed:
            segments.append(_make_segment(group, qrs))
            group = []
        elif group and starts_invoice and same_fingerprint and _COPY.search(text):
            # Same-copy pages stay in a segment; the hypotheses are retained per page in the rules pass.
            pass
        elif group and not explicit_continuation and starts_invoice and invoice_no and not same_fingerprint:
            segments.append(_make_segment(group, qrs))
            group = []
        elif group and not explicit_continuation and not invoice_no and not supplier and _INVOICE_TITLE.search(text):
            segments.append(_make_segment(group, qrs))
            group = []
        group.append(page)
        previous_invoice = invoice_no or previous_invoice
        previous_gstin = supplier or previous_gstin
    if group:
        segments.append(_make_segment(group, qrs))
    return segments


def _make_segment(pages: list[PageData], qrs: dict[int, list[RawInvoice]]) -> Segment:
    first = pages[0].index
    last = pages[-1].index
    qr_candidates = [raw for page in pages for raw in qrs.get(page.index, [])]
    rules = extract_rules(pages)
    copy_pages = [page for page in pages if _COPY.search(page.layout_text)]
    if copy_pages:
        page_hypotheses = [extract_rules([page]) for page in pages]
        fields = set().union(*(raw.invoice.keys() for raw in page_hypotheses))
        for field in fields:
            candidates = [value for raw in page_hypotheses for value in raw.invoice.get(field, []) if value.value is not None]
            counts = Counter(value.value for value in candidates)
            if len(counts) > 1:
                first_seen = {value.value: index for index, value in enumerate(candidates)}
                ordered_values = sorted(counts, key=lambda value: (-counts[value], first_seen[value]))
                representatives = []
                for value in ordered_values:
                    representatives.append(next(candidate for candidate in candidates if candidate.value == value))
                rules.invoice[field] = representatives
        # Copies represent one invoice's line items; do not append the same lines once per copy.
        first_rules = page_hypotheses[0] if page_hypotheses else None
        if first_rules:
            rules.line_items = first_rules.line_items
    return Segment(
        page_range=[first, last],
        qr=qr_candidates[0] if qr_candidates else None,
        rules=rules,
    )


def _first(raw: RawInvoice, key: str) -> str | None:
    fields = raw.invoice.get(key) or []
    return fields[0].value if fields else None


def _qr_first(invoices: list[RawInvoice], key: str) -> str | None:
    return next((_first(raw, key) for raw in invoices if _first(raw, key)), None)
