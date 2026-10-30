"""Review-only cell candidates from ruled scanned tables and OCR word boxes.

This deliberately handles only visible rectangular grids. Unruled, merged, or
ambiguous cells stay for human review; no value is inferred from adjacent cells.
"""
from __future__ import annotations


def _runs(values: list[int], minimum: int, gap: int = 5) -> list[int]:
    positions = [i for i, value in enumerate(values) if value >= minimum]
    groups: list[list[int]] = []
    for position in positions:
        if not groups or position - groups[-1][-1] > gap:
            groups.append([position])
        else:
            groups[-1].append(position)
    return [round(sum(group) / len(group)) for group in groups]


def ruled_table_candidates(image, ocr_lines: list[dict], *, page: int,
                           page_id: str, pdf_width: float, pdf_height: float) -> list[dict]:
    """Map OCR words into geometrically bounded cells, never verified facts.

    OCR word boxes are normalized to the rendered page. A grid is accepted only
    if it has at least two rows and columns, three long lines on each axis, and
    each internal boundary crosses the opposite outer lines. Missing or
    overlapping word boxes produce blank cells or unassigned text, not guesses.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width < 100 or height < 100:
        return []
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, width // 35), 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, height // 45))))
    connected = cv2.dilate(cv2.bitwise_or(horizontal, vertical),
                           cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    words = []
    for line in ocr_lines:
        for word in line.get("words", []):
            box = word.get("bbox")
            if (word.get("text", "").strip() and isinstance(box, list) and len(box) == 4 and
                    all(isinstance(value, (int, float)) for value in box)):
                words.append((word["text"], tuple(box)))
    if not words:
        return []
    candidates = []
    for contour in contours:
        x0, y0, box_width, box_height = cv2.boundingRect(contour)
        if box_width < width * .12 or box_height < height * .025:
            continue
        x1, y1 = x0 + box_width, y0 + box_height
        h_region = horizontal[y0:y1, x0:x1]
        v_region = vertical[y0:y1, x0:x1]
        xs = _runs((v_region > 0).sum(axis=0).tolist(), max(12, round(box_height * .45)))
        ys = _runs((h_region > 0).sum(axis=1).tolist(), max(12, round(box_width * .45)))
        xs = [x0 + item for item in xs]
        ys = [y0 + item for item in ys]
        if len(xs) < 3 or len(ys) < 3 or len(xs) > 60 or len(ys) > 100:
            continue
        # Require boundaries to meet the opposite outer lines. This rejects
        # headings and underlines that merely touch a table on the same page.
        tolerance = max(5, round(min(width, height) * .003))
        if (xs[0] - x0 > tolerance or x1 - xs[-1] > tolerance or
                ys[0] - y0 > tolerance or y1 - ys[-1] > tolerance):
            continue
        cells: list[list[list[tuple[float, float, str]]]] = [
            [[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)
        ]
        unassigned = []
        for value, (left, top, right, bottom) in words:
            lx, rx = left * width, right * width
            ty, by = top * height, bottom * height
            cx, cy = (lx + rx) / 2, (ty + by) / 2
            if not (xs[0] < cx < xs[-1] and ys[0] < cy < ys[-1]):
                continue
            column = next((i for i in range(len(xs) - 1) if xs[i] < cx < xs[i + 1]), None)
            row = next((i for i in range(len(ys) - 1) if ys[i] < cy < ys[i + 1]), None)
            if row is None or column is None:
                unassigned.append(value)
                continue
            # Text crossing a printed boundary may belong to a merged cell;
            # keep it out of the row matrix rather than force an association.
            if (lx < xs[column] - tolerance or rx > xs[column + 1] + tolerance or
                    ty < ys[row] - tolerance or by > ys[row + 1] + tolerance):
                unassigned.append(value)
                continue
            cells[row][column].append((cy, cx, value))
        values = [[" ".join(word for _, _, word in sorted(cell)) for cell in row] for row in cells]
        if sum(bool(cell) for row in values for cell in row) < 2:
            continue
        candidates.append({
            "id": f"{page_id}:table{len(candidates)+1}", "page": page,
            "bbox": [round(xs[0] / width * pdf_width, 2), round(ys[0] / height * pdf_height, 2),
                     round(xs[-1] / width * pdf_width, 2), round(ys[-1] / height * pdf_height, 2)],
            "bbox_coordinates": "PDF points, top-left",
            "extraction_basis": "ocr_ruling_geometry_word_boxes", "cells": values,
            "status": "needs_row_column_review", "unassigned_words": unassigned,
            "grid_coordinates": "normalized top-left",
            "grid_x": [round(x / width, 6) for x in xs],
            "grid_y": [round(y / height, 6) for y in ys],
        })
    return sorted(candidates, key=lambda table: (table["bbox"][1], table["bbox"][0]))
