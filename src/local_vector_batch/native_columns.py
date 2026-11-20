"""Native page text in column order (pdfplumber words + layout_order). None when the page is not two-column, so
callers keep pdfplumber's own text for every other page and nothing else changes."""
from __future__ import annotations

from .layout_order import column_order


def _has_rulings(page) -> bool:
    """Vector lines or thin rectangles: the page has ruled structure (a table or a form); leave its order alone."""
    try:
        lines = list(getattr(page, "lines", []) or [])
        thin = [r for r in (getattr(page, "rects", []) or []) if min(r["width"], r["height"]) < 2]
        return len(lines) + len(thin) >= 3
    except Exception:
        return True


def column_ordered_text(page) -> str | None:
    try:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    except Exception:
        return None
    items = [{"text": w["text"], "x0": float(w["x0"]), "x1": float(w["x1"]), "top": float(w["top"]),
              "bottom": float(w["bottom"])} for w in words if str(w.get("text", "")).strip()]
    rows = column_order(items, float(page.width), has_rulings=_has_rulings(page))
    if rows is None:
        return None
    return "\n".join(" ".join(i["text"] for i in row) for row in rows if row)
