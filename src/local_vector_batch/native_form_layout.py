"""Narrow source-bound classifier for empty signature/form grids, not data tables."""
from __future__ import annotations

import hashlib
import json
import re


ORDINAL = re.compile(r"\d{1,2}[.)]")


def _cell_text(value: str) -> str:
    return "".join(ch.casefold() for ch in value if not ch.isspace())


def prove_empty_form_grid(source_page, table_records: list[dict], *, source_pdf_sha256: str,
                          page_text_sha256: str, physical_page: int,
                          source_geometry: dict) -> dict | None:
    """Require exact native cell geometry and glyphs; never fill blank cells.

    A form grid has one prose label and ordinal placeholders only. Any other
    populated cells, multi-value rows, or unaccounted source ink keep the page
    on the normal table review path.
    """
    if (not source_geometry.get("page_complete") or
            source_geometry.get("unaccounted_ink_pixels") != 0 or
            source_geometry.get("unresolved_visual_regions") or
            not source_geometry.get("reading_order_proven") or len(table_records) != 1):
        return None
    stored = table_records[0]
    matches = [table for table in source_page.find_tables()
               if [round(float(x), 2) for x in table.bbox] == stored.get("bbox")]
    if len(matches) != 1:
        return None
    native = matches[0]
    if native.extract() != stored.get("cells") or len(native.rows) != len(stored["cells"]):
        return None
    mapped = set()
    semantic = 0
    ordinals = 0
    for row_index, row in enumerate(native.rows):
        for column_index, bbox in enumerate(row.cells):
            if bbox is None:
                return None
            x0, top, x1, bottom = bbox
            found = []
            for char_index, char in enumerate(source_page.chars):
                if char_index in mapped or not str(char.get("text", "")).strip():
                    continue
                x = (char["x0"] + char["x1"]) / 2
                y = (char["top"] + char["bottom"]) / 2
                if x0 <= x <= x1 and top <= y <= bottom:
                    found.append(char["text"])
                    mapped.add(char_index)
            cell = str(stored["cells"][row_index][column_index] or "")
            if _cell_text(cell) != _cell_text("".join(found)):
                return None
            if not cell.strip():
                continue
            if ORDINAL.fullmatch(cell.strip()):
                ordinals += 1
            else:
                semantic += 1
                if semantic > 1:
                    return None
    if semantic != 1 or ordinals < 1:
        return None
    # Every visible glyph inside the source table must belong to exactly one
    # extracted cell. Page footer text outside the grid is checked separately.
    x0, top, x1, bottom = native.bbox
    for index, char in enumerate(source_page.chars):
        if not str(char.get("text", "")).strip():
            continue
        x = (char["x0"] + char["x1"]) / 2
        y = (char["top"] + char["bottom"]) / 2
        if x0 <= x <= x1 and top <= y <= bottom and index not in mapped:
            return None
    cells_hash = hashlib.sha256(json.dumps(stored["cells"], ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
    return {"kind": "native_empty_form_grid", "source_pdf_sha256": source_pdf_sha256,
            "physical_page": physical_page, "page_text_sha256": page_text_sha256,
            "table_id": stored["id"], "table_cells_sha256": cells_hash,
            "source_render_sha256": source_geometry["rendered_image_sha256"],
            "empty_cells_source_checked": True, "all_table_glyphs_mapped": True,
            "semantic_cells": semantic, "ordinal_placeholders": ordinals}
