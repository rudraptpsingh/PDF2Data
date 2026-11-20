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
            overlap = min(row["bottom"], item["bottom"]) - max(row["top"], item["top"])
            if overlap >= min_overlap * min(h, row["bottom"] - row["top"]):
                row["items"].append(item)
                row["top"], row["bottom"] = min(row["top"], item["top"]), max(row["bottom"], item["bottom"])
                break
        else:
            rows.append({"top": item["top"], "bottom": item["bottom"], "items": [item]})
    rows.sort(key=lambda r: r["top"])
    return [sorted(r["items"], key=lambda i: i["x0"]) for r in rows]


def find_gutter(rows: list[list[dict]], width: float, min_gap: float = 12.0) -> float | None:
    """The x of a central vertical gap that most rows leave empty and that splits prose on both sides, else None."""
    if len(rows) < 4 or width <= 0:
        return None
    lo, hi = width * 0.3, width * 0.7
    limit = 0.3 * len(rows)
    # The widest band of x positions that at most 30% of rows cross (titles, footers); its centre is the gutter.
    runs, start = [], None
    x = lo
    while x <= hi + 1.0:
        ok = x <= hi and sum(1 for r in rows if any(i["x0"] < x < i["x1"] for i in r)) <= limit
        if ok and start is None:
            start = x
        elif not ok and start is not None:
            runs.append((start, x - 1.0))
            start = None
        x += 1.0
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
    both = [r for r in free if any(i["x1"] <= gx for i in r) and any(i["x0"] >= gx for i in r)]
    if len(both) < 4:
        return None
    left_w = median(max(i["x1"] for i in r if i["x1"] <= gx) - min(i["x0"] for i in r if i["x1"] <= gx) for r in both)
    right_w = median(max(i["x1"] for i in r if i["x0"] >= gx) - min(i["x0"] for i in r if i["x0"] >= gx) for r in both)
    left_n = median(sum(1 for i in r if i["x1"] <= gx) for r in both)
    right_n = median(sum(1 for i in r if i["x0"] >= gx) for r in both)
    # Prose columns: several words a line on each side and real width. Label/value pairs and short cells are not.
    if min(left_n, right_n) < 3 or min(left_w, right_w) < 0.15 * width:
        return None
    return (left_edge + right_edge) / 2


def column_order(items: list[dict], width: float, has_rulings: bool = False):
    """Items in reading order as a list of rows (each a list of items), or None when the page is not two-column."""
    if has_rulings:
        return None
    rows = rows_by_overlap(items)
    gx = find_gutter(rows, width)
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
