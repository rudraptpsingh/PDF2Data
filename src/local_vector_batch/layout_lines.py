"""Printed lines with their geometry and type, for laying a document out again from what was extracted.

Each row of lines.jsonl is one line as printed on its page:
- text, and its exact span of the page text (char_start, char_length), or null offsets when the page text does
  not contain it verbatim;
- bbox as fractions of the page, top-left origin;
- size (font size in points; for OCR, the line height in points), bold, italic and font (null where the reader
  cannot tell);
- align (left, center or right) and indent (fraction of page width from the left edge of the line's column).

A row of words is cut into separate lines where the gap between two words is wider than twice the type size, so
columns and table cells are separate lines. Lines read in the page's reading order (columns left then right).
"""
from __future__ import annotations

import re
from statistics import median

from .layout_order import column_order, rows_by_overlap

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.I)
_ITALIC = re.compile(r"italic|oblique", re.I)


def _font(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def _segments(row: list[dict]) -> list[list[dict]]:
    out = [[row[0]]]
    for word in row[1:]:
        prev = out[-1][-1]
        if word["x0"] - prev["x1"] > 2 * max(prev.get("size") or 0, word.get("size") or 0, 4):
            out.append([word])
        else:
            out[-1].append(word)
    return out


def _locate(text: str, words: list[str], cursor: int):
    if not words:
        return None
    # Words may touch in the page text (a superscript "13 th" reads "13th"; "Limited ," reads "Limited,").
    pattern = re.compile(r"\s*".join(re.escape(w) for w in words))
    m = pattern.search(text, cursor) or pattern.search(text)
    return (m.start(), m.end()) if m else None


def _finish(page: dict, items: list[dict], width: float, height: float) -> list[dict]:
    """items: one per line, with x0/x1/top/bottom in points, words, size, bold, italic, font."""
    if not items:
        return []
    text = page.get("text") or ""
    rows, cursor = [], 0
    for it in items:
        span = _locate(text, it["words"], cursor)
        if span:
            cursor = span[1]
        line_text = text[span[0]:span[1]] if span else " ".join(it["words"])
        x0, x1 = it["x0"] / width, it["x1"] / width
        col_left = min(o["x0"] for o in items if o["x0"] < it["x1"] and o["x1"] > it["x0"]) / width
        others = [o for o in items if o is not it]
        page_left = min((o["x0"] for o in others), default=it["x0"]) / width
        right_edge = max((o["x1"] for o in others), default=it["x1"]) / width
        gap_l, gap_r = x0 - page_left, right_edge - x1
        on_page_centre = abs((x0 + x1) / 2 - 0.5) < 0.02 and gap_l > 0.05
        shared = sum(1 for o in others if abs(o["x0"] / width - x0) < 0.005)
        balanced = gap_l > 0.05 and gap_r > 0.05 and abs(gap_l - gap_r) < 0.25 * max(gap_l, gap_r) + 0.02
        if (balanced or on_page_centre) and shared < 2:
            align = "center"
        elif abs(gap_r) < 0.01 and gap_l > 0.3 * max(right_edge - page_left, 1e-6) and shared < 2:
            align = "right"
        else:
            align = "left"
        rows.append({"page": page["page"], "n": len(rows) + 1, "text": line_text,
                     "char_start": span[0] if span else None, "char_length": span[1] - span[0] if span else None,
                     "bbox": [round(max(0.0, min(1.0, v)), 6) for v in
                              (x0, it["top"] / height, x1, it["bottom"] / height)],
                     "size": round(it["size"], 1) if it["size"] else None, "bold": it["bold"], "italic": it["italic"],
                     "font": it["font"], "align": align, "indent": round(max(0.0, x0 - col_left), 6)})
    return rows


def native_lines(page: dict, pdf_page) -> list[dict]:
    width, height = float(pdf_page.width), float(pdf_page.height)
    words = pdf_page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False, use_text_flow=False)
    items = [{"text": w["text"], "x0": float(w["x0"]), "x1": float(w["x1"]), "top": float(w["top"]),
              "bottom": float(w["bottom"]), "size": float(w.get("size") or 0), "font": _font(str(w.get("fontname") or ""))}
             for w in words if str(w.get("text", "")).strip()]
    if not items:
        return []
    # Reading order over words (columns left then right where the page has them), then lines cut at wide gaps.
    ordered = column_order(items, width) or rows_by_overlap(items)
    segments = [seg for row in ordered for seg in _segments(sorted(row, key=lambda w: w["x0"]))]
    out = []
    for ws in segments:
        fonts = [w["font"] for w in ws]
        out.append({"x0": min(w["x0"] for w in ws), "x1": max(w["x1"] for w in ws),
                    "top": min(w["top"] for w in ws), "bottom": max(w["bottom"] for w in ws),
                    "words": [w["text"] for w in ws], "size": median(w["size"] for w in ws),
                    "bold": sum(bool(_BOLD.search(f)) for f in fonts) > len(fonts) / 2,
                    "italic": sum(bool(_ITALIC.search(f)) for f in fonts) > len(fonts) / 2,
                    "font": max(set(fonts), key=fonts.count)})
    return _finish(page, out, width, height)


def ocr_lines(page: dict, ocr_result: dict) -> list[dict]:
    """OCR lines in the order the reader returned them (already reading order); size is the line height in points."""
    width, height = float(page.get("pdf_width") or 1), float(page.get("pdf_height") or 1)
    items = []
    for line in (ocr_result or {}).get("lines", []):
        b = line.get("bbox") or []
        if len(b) != 4 or not str(line.get("text", "")).strip():
            continue
        items.append({"x0": b[0] * width, "x1": b[2] * width, "top": b[1] * height, "bottom": b[3] * height,
                      "words": str(line["text"]).split(), "size": (b[3] - b[1]) * height,
                      "bold": None, "italic": None, "font": None})
    return _finish(page, items, width, height)
