"""Conservative document gate for blank pages, inserts, and non-invoices."""

from __future__ import annotations

import re

from vyom.models import PageData

INVOICE_SIGNALS = (
    "tax invoice", "invoice", "bill", "cash memo", "gstin", "gst", "hsn", "total", "amount", "qty",
)
CARBON_PHRASES = (
    "original for recipient", "duplicate for transporter", "triplicate for supplier", "office copy",
    "customer copy", "terms and conditions", "e-way bill", "packing list", "delivery challan",
)
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
AMOUNT_RE = re.compile(r"(?:₹|\bINR\b|\bRs\.?\s*)\s*\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE)


def page_is_carbon_copy(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(phrase in normalized for phrase in CARBON_PHRASES)


def invoice_signals(text: str) -> set[str]:
    folded = text.casefold()
    seen = {signal for signal in INVOICE_SIGNALS if signal in folded}
    if GSTIN_RE.search(text.upper()):
        seen.add("gstin-shaped")
    if AMOUNT_RE.search(text):
        seen.add("currency-amount")
    return seen


def gate_pages(pages: list[PageData]) -> set[str]:
    """Mark carbon/blank pages and return signal names for the whole document."""
    for page in pages:
        text = page.layout_text
        if page_is_carbon_copy(text) and len(invoice_signals(text)) < 3:
            page.kind = "skipped"
            if "PAGE_SKIPPED" not in page.flags:
                page.flags.append("PAGE_SKIPPED")
    active = "\n".join(page.layout_text for page in pages if page.kind != "skipped")
    if not active.strip():
        for page in pages:
            if page.kind != "skipped":
                page.kind = "skipped"
                page.flags.append("PAGE_SKIPPED")
        return set()
    return invoice_signals(active)
