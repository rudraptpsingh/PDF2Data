"""Reading order from word geometry, shared by native text and OCR lines.

Words (or OCR lines) with boxes are grouped into rows by vertical overlap, not by exact top, so a cell a pixel higher
never jumps ahead. A page reads in columns only with clear evidence:
- a central gutter that no word crosses on most rows;
- text on both sides at prose width;
- at least four rows that use both sides.
Rows that cross the gutter (titles, running headers, footers, full-width paragraphs) split the page into sections, and
each section reads its left column, then its right. Without that evidence the page keeps its plain row order.

Boxes are in top-left coordinates: x0, top, x1, bottom.
"""
from __future__ import annotations

from statistics import median


def rows_by_overlap(items: list[dict], min_overlap: float = 0.5) -> list[list[dict]]:
    """Group items into rows: an item joins a row when it overlaps the row's vertical extent by min_overlap of its
    own height. Rows read top to bottom; items in a row read left to right."""
    rows: list[dict] = []
    for item in sorted(items, key=lambda i: (i["top"], i["x0"])):
        h = max(1e-6, item["bottom"] - item["top"])
        for row in reversed(rows[-3:]):
            # Compare with the row's first item only: a row's band never widens, so a skewed page cannot chain
            # its lines into one tall row.
            a_top, a_bottom = row["anchor"]
            overlap = min(a_bottom, item["bottom"]) - max(a_top, item["top"])
            if overlap >= min_overlap * min(h, a_bottom - a_top):
                row["items"].append(item)
                row["top"], row["bottom"] = min(row["top"], item["top"]), max(row["bottom"], item["bottom"])
                break
        else:
            rows.append({"top": item["top"], "bottom": item["bottom"], "anchor": (item["top"], item["bottom"]), "items": [item]})
    rows.sort(key=lambda r: r["top"])
    return [sorted(r["items"], key=lambda i: i["x0"]) for r in rows]


def find_gutter(rows: list[list[dict]], width: float, min_gap: float | None = None, min_side_items: int = 3) -> float | None:
    """The x of a central vertical gap that most rows leave empty and that splits prose on both sides, else None."""
    if len(rows) < 4 or width <= 0:
        return None
    min_gap = 0.02 * width if min_gap is None else min_gap
    step = width / 600
    lo, hi = width * 0.3, width * 0.7
    limit = 0.3 * len(rows)
    # The widest band of x positions that at most 30% of rows cross (titles, footers); its centre is the gutter.
    runs, start = [], None
    x = lo
    while x <= hi + step:
        ok = x <= hi and sum(1 for r in rows if any(i["x0"] < x < i["x1"] for i in r)) <= limit
        if ok and start is None:
            start = x
        elif not ok and start is not None:
            runs.append((start, x - step))
            start = None
        x += step
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    gx = (a + b) / 2
    # widen to the empty band around gx among non-crossing rows, and require a real gap
    free = [r for r in rows if not any(i["x0"] < gx < i["x1"] for i in r)]
    left_edge = max((i["x1"] for r in free for i in r if i["x1"] <= gx), default=None)
    right_edge = min((i["x0"] for r in free for i in r if i["x0"] >= gx), default=None)
    if left_edge is None or right_edge is None or right_edge - left_edge < min_gap:
        return None
    # Each side is judged on its own rows, so a skewed scan (whose columns never share a row) still qualifies.
    lefts = [[i for i in r if i["x1"] <= gx] for r in free]
    rights = [[i for i in r if i["x0"] >= gx] for r in free]
    lefts, rights = [r for r in lefts if r], [r for r in rights if r]
    if len(lefts) < 4 or len(rights) < 4:
        return None
    left_w = median(max(i["x1"] for i in r) - min(i["x0"] for i in r) for r in lefts)
    right_w = median(max(i["x1"] for i in r) - min(i["x0"] for i in r) for r in rights)
    left_n, right_n = median(len(r) for r in lefts), median(len(r) for r in rights)
    # Prose columns: several words a line on each side and real width. Label/value pairs and short cells are not.
    if min(left_n, right_n) < min_side_items or min(left_w, right_w) < 0.15 * width:
        return None
    # Prose is contiguous on each side; table cells have wide gaps between them.
    heights = [i["bottom"] - i["top"] for r in lefts + rights for i in r]
    limit_gap = 3 * median(heights)
    for side in lefts + rights:
        side = sorted(side, key=lambda i: i["x0"])
        if any(b["x0"] - a["x1"] > limit_gap for a, b in zip(side, side[1:])):
            return None
    return (left_edge + right_edge) / 2


def column_order(items: list[dict], width: float, has_rulings: bool = False, min_side_items: int = 3):
    """Items in reading order as a list of rows (each a list of items), or None when the page is not two-column."""
    if has_rulings:
        return None
    rows = rows_by_overlap(items)
    gx = find_gutter(rows, width, min_side_items=min_side_items)
    if gx is None:
        return None
    out, section = [], []

    def flush():
        if section:
            out.extend([[i for i in r if i["x1"] <= gx] for r in section if any(i["x1"] <= gx for i in r)])
            out.extend([[i for i in r if i["x0"] >= gx] for r in section if any(i["x0"] >= gx for i in r)])
            section.clear()
    for r in rows:
        if any(i["x0"] < gx < i["x1"] for i in r):
            flush()
            out.append(r)
        else:
            section.append(r)
    flush()
    return out


def page_slope(lines: list[dict], min_width: float = 0.03) -> float:
    """The page's skew as dy/dx in normalised units: the width-weighted median of the lines' baseline slopes (long
    lines dominate; short boxes often come back level). 0 when unknown or negligible."""
    pairs = sorted((float(l["baseline_slope"]), l["bbox"][2] - l["bbox"][0]) for l in lines
                   if isinstance(l.get("baseline_slope"), (int, float)) and l["bbox"][2] - l["bbox"][0] >= min_width)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return 0.0
    acc = 0.0
    for slope, w in pairs:
        acc += w
        if acc >= total / 2:
            return slope if abs(slope) >= 0.003 else 0.0
    return 0.0


def order_ocr_lines(lines: list[dict]) -> list[dict]:
    """OCR lines (bbox normalised top-left) in reading order: two text columns left then right when proven,
    otherwise rows by vertical overlap, each read left to right (so table rows stay whole)."""
    if len(lines) < 2:
        return list(lines)
    slope = page_slope(lines)
    items = []
    for l in lines:
        x0, top, x1, bottom = l["bbox"]
        shift = slope * ((x0 + x1) / 2 - 0.5)          # straighten a skewed scan before grouping rows
        items.append({"x0": x0, "top": top - shift, "x1": x1, "bottom": bottom - shift, "line": l})
    rows = column_order(items, 1.0, min_side_items=1) or rows_by_overlap(items)
    return [i["line"] for row in rows for i in row]
