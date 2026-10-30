"""Import Docling table observations into a source-bound evidence package.

The imported rows are review candidates. Neither page text nor facts are
replaced, and no table cell becomes a verified value by this operation.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

import hashlib
import json
from pathlib import Path
import shutil
import uuid

from .evidence_package import create_package, verify_package
from .structure_candidates import table_candidates


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in jsonl_physical_lines(path.read_text(encoding="utf-8")) if line.strip(' \t\r')]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _top_left_box(box: dict, width: float, height: float) -> list[float]:
    values = [box.get(key) for key in ("l", "t", "r", "b")]
    if any(not isinstance(value, (int, float)) for value in values):
        raise ValueError("Docling table box has invalid coordinates")
    left, top, right, bottom = values
    if box.get("coord_origin") == "BOTTOMLEFT":
        top, bottom = height - top, height - bottom
    elif box.get("coord_origin") != "TOPLEFT":
        raise ValueError("Docling table box has unknown coordinate origin")
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("Docling table box is outside the page")
    return [round(left, 3), round(top, 3), round(right, 3), round(bottom, 3)]


def _table_from_docling(raw: dict, *, page: dict, source_sha: str,
                        comparison_sha: str, index: int, page_width: float, page_height: float,
                        engine_version: str, language: str, device: str) -> dict:
    prov = raw.get("prov") or []
    if len(prov) != 1 or prov[0].get("page_no") != page["page"]:
        raise ValueError("Docling table lost original physical page provenance")
    data = raw.get("data") or {}
    nr, nc = data.get("num_rows"), data.get("num_cols")
    if type(nr) is not int or type(nc) is not int or nr < 1 or nc < 1 or nr * nc > 100000:
        raise ValueError("Docling table grid is invalid")
    cells = [["" for _ in range(nc)] for _ in range(nr)]
    spans = []
    for cell in data.get("table_cells") or []:
        row, col = cell.get("start_row_offset_idx"), cell.get("start_col_offset_idx")
        re, ce = cell.get("end_row_offset_idx"), cell.get("end_col_offset_idx")
        if (any(type(value) is not int for value in (row, col, re, ce)) or
                not (0 <= row < re <= nr and 0 <= col < ce <= nc) or cells[row][col]):
            raise ValueError("Docling table cell is out of bounds or duplicated")
        value = str(cell.get("text") or "").strip()
        cells[row][col] = value
        spans.append({"row": row, "column": col, "row_span": re-row,
                      "column_span": ce-col, "text": value,
                      "bbox": _top_left_box(cell["bbox"], page_width, page_height) if cell.get("bbox") else None,
                      "column_header": bool(cell.get("column_header")),
                      "row_header": bool(cell.get("row_header"))})
    return {"id": f"{page['id']}:table-docling{index}", "page": page["page"],
            "bbox": _top_left_box(prov[0]["bbox"], page_width, page_height),
            "bbox_coordinates": "PDF points, top-left", "cells": cells,
            "cell_spans": spans, "extraction_basis": "docling_tableformer_candidate",
            "status": "needs_row_column_review", "source_pdf_sha256": source_sha,
            "page_text_sha256": page["text_sha256"],
            "comparison_sha256": comparison_sha,
            "candidate_engine": "docling", "candidate_engine_version": engine_version,
            "candidate_language": language, "requested_device": device}


def import_layout_candidates(package: Path, source_pdf: Path, comparison_output: Path) -> dict:
    """Atomically add only source-matched, review-only Docling table candidates."""
    package, source_pdf, comparison_output = map(Path, (package, source_pdf, comparison_output))
    verify_package(package, source_pdf=source_pdf)
    source_sha = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    comparison_path = comparison_output / "page-comparisons.jsonl"
    report_path = comparison_output / "comparison-report.json"
    comparison_report = json.loads(report_path.read_text(encoding="utf-8"))
    if (comparison_report.get("source_pdf_sha256") != source_sha or
            comparison_report.get("candidate_status") != "review_required" or
            comparison_report.get("pages_promoted") != 0):
        raise ValueError("comparison receipt does not match original review-only PDF")
    page_rows = {row["page"]: row for row in _read_jsonl(package / "pages.jsonl")}
    old_tables = json.loads((package / "tables.json").read_text(encoding="utf-8"))
    old_ids = {table["id"] for table in old_tables}
    additions = []
    seen_pages = set()
    for candidate in _read_jsonl(comparison_path):
        if candidate.get("status") != "review_required" or candidate.get("source_pdf_sha256") != source_sha:
            raise ValueError("comparison contains failed or foreign page")
        number = candidate.get("source_page")
        page = page_rows.get(number)
        if (page is None or number in seen_pages or
                candidate.get("selected_text_sha256") != page["text_sha256"]):
            raise ValueError("comparison page or selected page text hash mismatch")
        seen_pages.add(number)
        raw_path = comparison_output / f"page-{number}.docling.json"
        raw_bytes = raw_path.read_bytes()
        raw_data = json.loads(raw_bytes)
        raw_tables = raw_data.get("tables") or []
        if raw_tables != candidate.get("candidate_tables") or str(number) not in raw_data.get("pages", {}):
            raise ValueError("comparison table differs from raw Docling page receipt")
        size = raw_data["pages"][str(number)].get("size") or {}
        width, height = size.get("width"), size.get("height")
        if (not isinstance(width, (int, float)) or width <= 0 or
                not isinstance(height, (int, float)) or height <= 0):
            raise ValueError("Docling physical page size is missing")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        for index, raw_table in enumerate(raw_tables, 1):
            table = _table_from_docling(raw_table, page=page, source_sha=source_sha,
                comparison_sha=digest, index=index, page_width=width, page_height=height,
                engine_version=candidate["candidate_engine_version"],
                language=candidate["candidate_language"], device=candidate["requested_device"])
            if table["id"] in old_ids:
                raise ValueError("Docling table candidate was already imported")
            additions.append(table)
    if len(seen_pages) != comparison_report.get("pages_requested"):
        raise ValueError("comparison page count does not match receipt")
    if not additions:
        return {"source_pdf_sha256": source_sha, "pages_checked": len(seen_pages),
                "tables_added": 0, "status": "no_table_candidates"}
    temporary = package.parent / f".{package.name}.layout-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, temporary)
        tables = old_tables + additions
        (temporary / "tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        structures = _read_jsonl(temporary / "structure-candidates.jsonl")
        old_structure_count = len(structures)
        for table in additions:
            structures.extend(table_candidates(table, page_rows[table["page"]]))
        _write_jsonl(temporary / "structure-candidates.jsonl", structures)
        report = json.loads((temporary / "report.json").read_text(encoding="utf-8"))
        report["tables"] = len(tables)
        report["structure_candidates"] = len(structures)
        report["layout_table_candidates"] = sum(t.get("extraction_basis") == "docling_tableformer_candidate" for t in tables)
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        create_package(temporary)
        verify_package(temporary, source_pdf=source_pdf)
        for name in ("pages.jsonl", "embedding-input.jsonl", "fact-candidates.jsonl"):
            if (temporary / name).read_bytes() != (package / name).read_bytes():
                raise ValueError(f"layout import changed immutable {name}")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"source_pdf_sha256": source_sha, "pages_checked": len(seen_pages),
                "tables_added": len(additions), "structure_candidates_added": len(structures)-old_structure_count,
                "status": "review_required", "package": str(package)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
