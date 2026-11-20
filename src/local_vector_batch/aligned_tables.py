"""Tables without rulings, found from alignment in the page's printed lines (lines.jsonl rows).

Rows are lines that share a baseline band (straightened by the page slope on tilted scans). A table is a run of at
least three consecutive, closely set rows that each hold two or more separate cells. Its columns are the connected
blocks of the cells' x-intervals, so the gutters are bands no cell crosses, and no row may put two cells in one
column. To count, the run needs three or more columns, or two columns where one is
mostly figures. Prose set in two columns (long cells of many words) and label/value lines (one cell a row) are
not tables. Like ruled candidates, these need row and column review; nothing is inferred for empty cells.
"""
from __future__ import annotations

import re
from statistics import median

_FIGURE = re.compile(r"^[\s(]*[-+]?[₹$€£]?\s*[\d.,]+%?[)\s]*$")


def _rows(lines: list[dict], slope: float = 0.0) -> list[list[dict]]:
    """Lines sharing a baseline band. On a tilted scan each centre is straightened by the page slope first."""
    cy = lambda l: (l["bbox"][1] + l["bbox"][3]) / 2 - slope * (l["bbox"][0] + l["bbox"][2]) / 2
    rows: list[list[dict]] = []
    for line in sorted(lines, key=lambda l: (cy(l), l["bbox"][0])):
        h = line["bbox"][3] - line["bbox"][1]
        row = rows[-1] if rows else None
        if row and abs(cy(line) - median(cy(l) for l in row)) < 0.5 * max(h, 1e-6):
            row.append(line)
        else:
            rows.append([line])
    return [sorted(r, key=lambda l: l["bbox"][0]) for r in rows]


def _columns(run: list[list[dict]]):
    """Columns are the connected blocks of the cells' x-intervals across the run (the gutters are the empty bands),
    so a header set over its figures falls in their column. None when a row puts two cells in one column."""
    spans = sorted((c["bbox"][0], c["bbox"][2]) for row in run for c in row)
    blocks = [[*spans[0]]]
    for x0, x1 in spans[1:]:
        if x0 <= blocks[-1][1] + 0.002:
            blocks[-1][1] = max(blocks[-1][1], x1)
        else:
            blocks.append([x0, x1])
    assignment = []
    for row in run:
        placed, used = [], set()
        for cell in row:
            k = next(i for i, (a, b) in enumerate(blocks) if a - 1e-9 <= cell["bbox"][0] <= b)
            if k in used:
                return None
            used.add(k)
            placed.append((k, cell))
        assignment.append(placed)
    return [{"min": a, "max": b} for a, b in blocks], assignment


def _accept(run: list[list[dict]]):
    words = [len(c["text"].split()) for row in run for c in row]
    widths = [c["bbox"][2] - c["bbox"][0] for row in run for c in row]
    if median(words) >= 5 or max(widths) > 0.35:
        return None           # prose columns
    found = _columns(run)
    if not found:
        return None
    cols, assignment = found
    n = len(cols)
    support = [sum(1 for placed in assignment if any(k == i for k, _ in placed)) for i in range(n)]
    if n < 2 or min(support) < 0.5 * len(run):
        return None
    figures = [sum(1 for placed in assignment for k, c in placed if k == i and _FIGURE.match(c["text"]))
               for i in range(n)]
    if n == 2 and not any(f >= 0.6 * (s - 1) and f >= 2 for f, s in zip(figures, support)):
        return None
    return cols, assignment


def aligned_table_candidates(lines: list[dict], *, page: int, page_id: str, pdf_width: float, pdf_height: float,
                             slope: float = 0.0) -> list[dict]:
    rows = _rows([l for l in lines if str(l.get("text") or "").strip() and len(l.get("bbox") or []) == 4], slope)
    # Runs of multi-cell rows. A row with nothing in the first column, set close under the row above and lying
    # under its cells, is that row's cells wrapping onto another line (a continuation), not a new row.
    runs, current, conts = [], [], []

    def close_to(prev_rows, row):
        last = prev_rows[-1]
        return row[0]["bbox"][1] - max(c["bbox"][3] for c in last) <= 1.5 * max(c["bbox"][3] - c["bbox"][1] for c in last)

    def flush():
        if len(current) >= 3:
            runs.append((list(current), [list(c) for c in conts]))
        current.clear(); conts.clear()

    for row in rows:
        if current:
            tail = conts[-1] if conts[-1] else [current[-1]]
            first_col = (min(r[0]["bbox"][0] for r in current), max(r[0]["bbox"][2] for r in current))
            under = all(any(min(c["bbox"][2], o["bbox"][2]) - max(c["bbox"][0], o["bbox"][0]) > 0 for r in current for o in r[1:])
                        for c in row)
            if (close_to(tail, row) and under and len(current) >= 1 and
                    not any(c["bbox"][0] < first_col[1] and c["bbox"][2] > first_col[0] for c in row)):
                conts[-1].append(row)
                continue
        if len(row) >= 2 and (not current or close_to([current[-1]] + conts[-1], row)):
            current.append(row)
            conts.append([])
        else:
            flush()
            if len(row) >= 2:
                current.append(row)
                conts.append([])
    flush()
    out = []
    for run, run_conts in runs:
        accepted = _accept(run)
        if not accepted:
            continue
        cols, assignment = accepted
        cells = [[""] * len(cols) for _ in run]
        for r, placed in enumerate(assignment):
            for k, cell in placed:
                cells[r][k] = (cells[r][k] + " " + cell["text"]).strip()
            for extra in run_conts[r]:
                for cell in extra:
                    k = max(range(len(cols)), key=lambda i: min(cell["bbox"][2], cols[i]["max"]) - max(cell["bbox"][0], cols[i]["min"]))
                    cells[r][k] = (cells[r][k] + "\n" + cell["text"]).strip("\n")
        gx = [cols[0]["min"]] + [(cols[i]["max"] + cols[i + 1]["min"]) / 2 for i in range(len(cols) - 1)] + [cols[-1]["max"]]
        tops = [min(c["bbox"][1] for c in row) for row in run]
        bottoms = [max(c["bbox"][3] for c in [*row, *(c for extra in run_conts[i] for c in extra)]) for i, row in enumerate(run)]
        gy = [tops[0]] + [(bottoms[i] + tops[i + 1]) / 2 for i in range(len(run) - 1)] + [bottoms[-1]]
        out.append({"id": f"{page_id}:table{len(out) + 1}", "page": page,
                    "bbox": [round(gx[0] * pdf_width, 2), round(gy[0] * pdf_height, 2),
                             round(gx[-1] * pdf_width, 2), round(gy[-1] * pdf_height, 2)],
                    "bbox_coordinates": "PDF points, top-left", "extraction_basis": "aligned_text_columns",
                    "cells": cells, "status": "needs_row_column_review", "unassigned_words": [],
                    "grid_coordinates": "normalized top-left",
                    "grid_x": [round(v, 6) for v in gx], "grid_y": [round(v, 6) for v in gy]})
    return out
