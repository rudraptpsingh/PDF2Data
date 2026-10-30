"""Source-bound checks for OCR pages containing a table grid.

Docling proposes cell geometry and text; the primary OCR is an independent
reader.  A grid passes only when both readers account for every printed row
at the same physical page coordinates.  This never supplies missing words.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import re


TOKEN = re.compile(r"[^\W_]+(?:\.\d+)?", re.UNICODE)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _tokens(value: str) -> list[str]:
    return [word.casefold() for word in TOKEN.findall(value)]


def _render(source_pdf: Path, physical_page: int) -> tuple[str, tuple[float, float]]:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        page = pdf[physical_page - 1]
        try:
            size = tuple(page.get_size())
            image = page.render(scale=120 / 72).to_pil().copy()
        finally:
            page.close()
    output = BytesIO()
    image.save(output, format="PNG")
    return sha256(output.getvalue()).hexdigest(), size


def derive_table_grid_proof(page: dict, table: dict, source_pdf: Path) -> dict:
    """Verify Docling cell rows against source-bound OCR line geometry/text.

    The table must be a complete nonoverlapping grid.  Cell text is compared
    independently with the primary OCR by row and spatial column group, while
    every OCR line inside the grid must be accounted for.  Numeric tokens are
    checked in their source row, preventing a neighbouring row's value from
    filling a missing cell.
    """
    source_pdf = Path(source_pdf)
    source_sha = sha256(source_pdf.read_bytes()).hexdigest()
    if (table.get("source_pdf_sha256") != source_sha or
            table.get("page") != page.get("page") or
            table.get("page_text_sha256") != page.get("text_sha256") or
            table.get("extraction_basis") != "docling_tableformer_candidate"):
        raise ValueError("table and page source identity disagree")
    lines = page.get("ocr", {}).get("lines") or []
    if not lines or page.get("text") != "\n".join(line["text"] for line in lines):
        raise ValueError("primary OCR lines are unavailable")
    render_sha, (width, height) = _render(source_pdf, page["page"])
    if table.get("rendered_image_sha256") != render_sha:
        raise ValueError("table source raster changed")
    grid = table.get("cells") or []
    if len(grid) < 2 or not all(isinstance(row, list) and len(row) == len(grid[0]) for row in grid):
        raise ValueError("table grid is incomplete")
    if not 2 <= len(grid[0]) <= 12:
        raise ValueError("unsupported table column count")
    spans = table.get("cell_spans") or []
    occupied = set()
    row_boxes: list[list[list[float]]] = [[] for _ in grid]
    column_centers: list[list[float]] = [[] for _ in grid[0]]
    for cell in spans:
        row, col = cell["row"], cell["column"]
        if (not (0 <= row < len(grid) and 0 <= col < len(grid[0])) or
                (row, col) in occupied or cell.get("row_span") != 1 or
                cell.get("column_span") != 1 or
                cell.get("text") != grid[row][col]):
            raise ValueError("overlapping, spanned or inconsistent table cell")
        occupied.add((row, col))
        box = cell.get("bbox")
        if (not isinstance(box, list) or len(box) != 4 or
                not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
            raise ValueError("table cell is outside source page")
        row_boxes[row].append(box)
        column_centers[col].append((box[0] + box[2]) / 2)
    if occupied != {(r, c) for r, row in enumerate(grid) for c, text in enumerate(row) if text}:
        raise ValueError("not every populated grid cell has a source box")
    if any(not boxes for boxes in row_boxes) or any(not centers for centers in column_centers):
        raise ValueError("table row or column lacks source geometry")
    bands = [(min(box[1] for box in boxes), max(box[3] for box in boxes)) for boxes in row_boxes]
    if any(bands[i][1] >= bands[i + 1][0] for i in range(len(bands) - 1)):
        raise ValueError("table rows cross")
    centers = [sum(values) / len(values) for values in column_centers]
    if centers != sorted(centers):
        raise ValueError("table columns cross")
    bounds = table.get("bbox")
    if (not isinstance(bounds, list) or len(bounds) != 4 or
            any(box[0] < bounds[0] - 2 or box[2] > bounds[2] + 2 or
                box[1] < bounds[1] - 2 or box[3] > bounds[3] + 2
                for boxes in row_boxes for box in boxes)):
        raise ValueError("table cells lie outside detected table")
    # A narrow ordinal column may be merged with adjacent text by the primary
    # OCR.  Keep them as one spatial group while preserving the grid cells.
    merge_ordinal = (len(grid[0]) >= 3 and
                     all(not row[0] or re.fullmatch(r"\(?[A-Za-z0-9ivxlcdmIVXLCDM]{1,4}\)?[.)]?", row[0])
                         for row in grid))
    groups = [[0, 1]] + [[c] for c in range(2, len(grid[0]))] if merge_ordinal else [
        [c] for c in range(len(grid[0]))]
    group_centers = [sum(centers[c] for c in group) / len(group) for group in groups]
    cuts = [(a + b) / 2 for a, b in zip(group_centers, group_centers[1:])]
    assigned: dict[tuple[int, int], list[str]] = {(r, g): [] for r in range(len(grid)) for g in range(len(groups))}
    for line in lines:
        box = line.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("primary OCR line lost source geometry")
        x, y = (box[0] + box[2]) * width / 2, (box[1] + box[3]) * height / 2
        if not (bounds[0] - 2 <= x <= bounds[2] + 2 and bounds[1] - 2 <= y <= bounds[3] + 2):
            continue
        rows = [r for r, (top, bottom) in enumerate(bands) if top - 2 <= y <= bottom + 2]
        if len(rows) != 1:
            raise ValueError("primary OCR table line has ambiguous row")
        group = sum(x > cut for cut in cuts)
        assigned[rows[0], group].append(line["text"])
    cell_results = []
    for row in range(len(grid)):
        for group, cols in enumerate(groups):
            candidate = " ".join(grid[row][c] for c in cols if grid[row][c])
            primary = " ".join(assigned[row, group])
            if (not candidate or not primary or
                    Counter(_tokens(candidate)) != Counter(_tokens(primary)) or
                    Counter(NUMBER.findall(candidate)) != Counter(NUMBER.findall(primary))):
                raise ValueError(f"table row {row} columns {cols} disagree with primary OCR")
            cell_results.append({"row": row, "columns": cols,
                                 "candidate_tokens": len(_tokens(candidate)),
                                 "numeric_tokens": NUMBER.findall(candidate)})
    return {"kind": "source_bound_table_grid", "source_pdf_sha256": source_sha,
            "physical_page": page["page"], "page_text_sha256": page["text_sha256"],
            "source_render_sha256": render_sha, "table_id": table["id"],
            "grid_sha256": sha256(json.dumps(grid, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "rows": len(grid), "columns": len(grid[0]), "populated_cells": len(spans),
            "source_ocr_groups": cell_results, "table": table}


def validate_table_grid_proof(page: dict, proof: dict, source_pdf: Path | None) -> bool:
    if proof.get("kind") != "source_bound_table_grid" or source_pdf is None:
        return False
    try:
        return derive_table_grid_proof(page, proof["table"], source_pdf) == proof
    except (KeyError, TypeError, ValueError, OSError):
        return False
