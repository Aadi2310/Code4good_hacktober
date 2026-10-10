"""Digital PDF words, tokens, reading lines, and table cell grids."""

from __future__ import annotations

from statistics import median
from typing import Any
import re

from vyom.models import Token


def _norm(value: float, extent: float) -> float:
    return min(1.0, max(0.0, value / extent)) if extent else 0.0


def extract_digital_page(page: Any, page_number: int) -> tuple[list[Token], str, list[list[str]]]:
    """Return sorted PDF text tokens, layout text, and best-effort cell grid."""
    words = page.extract_words(
        use_text_flow=False,
        keep_blank_chars=False,
        x_tolerance=2,
        y_tolerance=3,
    ) or []
    words = sorted(words, key=lambda word: ((float(word["top"]) + float(word["bottom"])) / 2, float(word["x0"])))
    heights = [max(1.0, float(w["bottom"]) - float(w["top"])) for w in words]
    tolerance = max(1.0, median(heights) / 2) if heights else 2.0
    lines: list[list[dict]] = []
    for word in words:
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        target = next((line for line in reversed(lines) if abs(line[0]["_cy"] - center_y) <= tolerance), None)
        word = {**word, "_cy": center_y}
        if target is None:
            lines.append([word])
        else:
            target.append(word)
    lines.sort(key=lambda line: min(float(item["top"]) for item in line))

    tokens: list[Token] = []
    layout: list[str] = []
    for line_id, line in enumerate(lines, 1):
        line.sort(key=lambda item: float(item["x0"]))
        for word in line:
            tokens.append(Token(
                text=str(word.get("text", "")), conf=0.98,
                box=[_norm(float(word["x0"]), page.width), _norm(float(word["top"]), page.height),
                     _norm(float(word["x1"]), page.width), _norm(float(word["bottom"]), page.height)],
                page=page_number, line_id=line_id, kind="printed", source="pdf_text",
            ))
        layout.append(f"L{line_id:03d}| " + " ".join(str(w.get("text", "")) for w in line))

    grid = _extract_table(page)
    if not grid:
        grid = infer_column_grid(words)
    if grid:
        layout.extend("| " + " | ".join(cell for cell in row) + " |" for row in grid)
    return tokens, "\n".join(layout), grid


def infer_column_grid(words: list[dict]) -> list[list[str]]:
    """Infer table bands from header word centers when PDF rules are absent."""
    if not words:
        return []
    aliases = {
        "line_no": {"sr", "sno", "sl", "serial"},
        "description": {"description", "particulars", "item", "product"},
        "hsn_sac": {"hsn", "sac"}, "quantity": {"qty", "quantity"},
        "unit_price": {"rate", "price"}, "tax_rate": {"gst", "tax"},
        "taxable_value": {"amount", "taxable"}, "cgst": {"cgst"}, "sgst": {"sgst"},
        "igst": {"igst"}, "cess": {"cess"}, "line_total": {"total", "value"},
    }
    ordered = sorted(words, key=lambda word: ((float(word["top"]) + float(word["bottom"])) / 2, float(word["x0"])))
    heights = [max(1.0, float(word["bottom"]) - float(word["top"])) for word in ordered]
    tolerance = max(1.0, median(heights) / 2)
    lines: list[list[dict]] = []
    for word in ordered:
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        line = next((candidate for candidate in reversed(lines)
                     if abs((float(candidate[0]["top"]) + float(candidate[0]["bottom"])) / 2 - center_y) <= tolerance), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    lines.sort(key=lambda line: min(float(word["top"]) for word in line))
    header_index = -1
    headers: list[tuple[float, str, str]] = []
    for index, line in enumerate(lines):
        found: list[tuple[float, str, str]] = []
        for word in line:
            label = re.sub(r"[^a-z0-9]+", "", str(word.get("text", "")).casefold())
            matches = [field for field, choices in aliases.items() if label in choices]
            if matches:
                found.append(((float(word["x0"]) + float(word["x1"])) / 2, matches[0], str(word.get("text", ""))))
        if len({field for _, field, _ in found}) >= 3:
            header_index, headers = index, sorted(found, key=lambda item: item[0])
            break
    if header_index < 0 or len(headers) < 3:
        return []
    centers = [center for center, _, _ in headers]
    rows: list[list[str]] = [[label for _, _, label in headers]]
    numeric_fields = {"line_no", "quantity", "unit_price", "tax_rate", "taxable_value", "cgst", "sgst", "igst", "cess", "line_total"}
    for line in lines[header_index + 1:]:
        line = sorted(line, key=lambda word: float(word["x0"]))
        text = " ".join(str(word.get("text", "")) for word in line)
        if re.search(r"\b(?:subtotal|grand total|net payable|amount payable|round off)\b", text, re.IGNORECASE):
            break
        cells = [""] * len(headers)
        for word in line:
            word_text = str(word.get("text", "")).strip()
            if not word_text:
                continue
            center_x = (float(word["x0"]) + float(word["x1"])) / 2
            candidates = [(i, abs((float(word["x1"]) if headers[i][1] in numeric_fields else center_x) - headers[i][0])) for i in range(len(headers))]
            cell_index = min(candidates, key=lambda item: item[1])[0]
            cells[cell_index] = (cells[cell_index] + " " + word_text).strip()
        if any(cells):
            rows.append(cells)
    return rows if len(rows) > 1 else []


def _extract_table(page: Any) -> list[list[str]]:
    """Use pdfplumber ruling-line tables, then its text strategy."""
    for strategy in ("lines", "text"):
        try:
            tables = page.find_tables(table_settings={
                "vertical_strategy": strategy,
                "horizontal_strategy": strategy,
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 3,
            })
        except Exception:
            continue
        for table in tables or []:
            try:
                rows = [[(cell or "").strip() for cell in row] for row in table.extract() or []]
            except Exception:
                continue
            if len(rows) >= 2 and max((len(row) for row in rows), default=0) >= 3:
                return rows
    return []
