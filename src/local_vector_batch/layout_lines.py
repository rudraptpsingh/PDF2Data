"""Printed lines with their geometry and type, for laying a document out again from what was extracted.

Each row of lines.jsonl is one line as printed on its page:
- text, and its exact span of the page text (char_start, char_length), or null offsets when the page text does
  not contain it verbatim;
- bbox as fractions of the page, top-left origin;
- size (font size in points; for OCR, estimated from the line's width and its letters' standard widths), bold, italic and font (null where the reader
  cannot tell);
- align (left, center or right) and indent (fraction of page width from the left edge of the line's column);
- lead_bold, only on a native line that opens with a run-in heading (a bold phrase followed by regular text on the
  same line, "Background. The petitioner ..."): the number of characters of that bold phrase in the page text.

A row of words is cut into separate lines where the gap between two words is wider than twice the type size, so
columns and table cells are separate lines. Lines read in the page's reading order (columns left then right).
"""
from __future__ import annotations

import re
from statistics import median

from .layout_order import column_order, rows_by_overlap
from .structure_candidates import estimated_type_size

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.I)
_ITALIC = re.compile(r"italic|oblique", re.I)
# A bold prefix that is only a list marker: "1.", "1.2", "(a)", "(iv)", "[3]".
_MARKER = re.compile(r"[(\[]?(?:\d+(?:\.\d+)*|[A-Za-z]|[ivxlcdm]+|[IVXLCDM]+)[.):\]]*")
_OPENERS = "([\"'\u2018\u201c"
# A bold parenthetical or quoted term inside a sentence: "(“Appellant”)", "“Commission”".
_ASIDE = re.compile(r"[\"'\u2018\u201c]?\((?:[^()]|\([^()]*\))*\)[\"'\u2019\u201d]?[.,;]?|"
                    r"[\"'\u2018\u201c][^\"'\u2019\u201d]*[\"'\u2019\u201d][.,;]?")


def _unbalanced(text: str) -> bool:
    return text.count("(") != text.count(")") or text.count("[") != text.count("]")


def run_in_prefix(words: list) -> int:
    """Number of leading words forming a run-in heading, else 0. `words` is [(text, bold)] or
    [(text, bold, gap before the word in ems)] for one line.

    The first 1-10 words are bold and the rest of the line mostly is not. The bold run ends in ".", ":", a dash
    or ")"; or the next word starts with a capital, sits a normal word space away (a wider gap is a table's next
    column) and the rest is not in capitals (a heading set in two weights). The run has at least two letters,
    balanced brackets, and is not just a list marker, a parenthetical or a quoted term ("(“Appellant”)"). It opens
    with a capital, a digit or an opening bracket or quote: a bold run starting in lower case continues a sentence."""
    n = 0
    while n < len(words) and words[n][1]:
        n += 1
    if not 1 <= n <= 10 or n == len(words):
        return 0
    rest = words[n:]
    if 2 * sum(1 for w in rest if w[1]) >= len(rest):
        return 0
    prefix = " ".join(w[0] for w in words[:n])
    if (sum(c.isalpha() for c in prefix) < 2 or _MARKER.fullmatch(prefix) or _ASIDE.fullmatch(prefix)
            or _unbalanced(re.sub(r"^\w{1,4}\)\s*", "", prefix))):        # "A) Eligibility:" opens with a marker
        return 0
    if not (prefix[0].isupper() or prefix[0].isdigit() or prefix[0] in _OPENERS):
        return 0
    if prefix[-1] in ".:)-\u2013\u2014":
        return n
    gap = rest[0][2] if len(rest[0]) > 2 else None
    letters = [c for w in rest for c in w[0] if c.isalpha()]
    rest_caps = len(letters) >= 2 and all(c.isupper() for c in letters)
    return n if rest[0][0][:1].isupper() and (gap is None or gap <= 0.8) and not rest_caps else 0


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
        if it.get("lead_words") and span:
            lead = re.compile(r"\s*".join(re.escape(w) for w in it["lead_words"])).match(text, span[0], span[1])
            if lead:
                rows[-1]["lead_bold"] = lead.end() - span[0]
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
        styled = [(w["text"], bool(_BOLD.search(w["font"])),
                   (w["x0"] - ws[i - 1]["x1"]) / max(ws[i - 1]["size"] or 0, 1) if i else 0.0) for i, w in enumerate(ws)]
        out.append({"lead_words": [w["text"] for w in ws][:run_in_prefix(styled)],"x0": min(w["x0"] for w in ws), "x1": max(w["x1"] for w in ws),
                    "top": min(w["top"] for w in ws), "bottom": max(w["bottom"] for w in ws),
                    "words": [w["text"] for w in ws], "size": median(w["size"] for w in ws),
                    "bold": sum(bool(_BOLD.search(f)) for f in fonts) > len(fonts) / 2,
                    "italic": sum(bool(_ITALIC.search(f)) for f in fonts) > len(fonts) / 2,
                    "font": max(set(fonts), key=fonts.count)})
    # Where the body itself is set in bold, a bold opening phrase marks nothing.
    body = [it for it in out if len(it["words"]) >= 4]
    if 2 * sum(1 for it in body if it["bold"]) > len(body):
        for it in out:
            it["lead_words"] = []
    return _finish(page, out, width, height)


def ocr_lines(page: dict, ocr_result: dict) -> list[dict]:
    """OCR lines in the order the reader returned them (already reading order); size is estimated from width."""
    width, height = float(page.get("pdf_width") or 1), float(page.get("pdf_height") or 1)
    items = []
    for line in (ocr_result or {}).get("lines", []):
        b = line.get("bbox") or []
        if len(b) != 4 or not str(line.get("text", "")).strip():
            continue
        items.append({"x0": b[0] * width, "x1": b[2] * width, "top": b[1] * height, "bottom": b[3] * height,
                      "words": str(line["text"]).split(),
                      "size": estimated_type_size(str(line["text"]).strip(), (b[2] - b[0]) * width) or (b[3] - b[1]) * height,
                      "bold": None, "italic": None, "font": None})
    return _finish(page, items, width, height)
