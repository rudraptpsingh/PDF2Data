"""Promote explicitly reviewed PDF spans into portable evidence units.

This validates provenance and literal anchors. It cannot decide whether a
reviewer's legal interpretation is correct; no parser candidate is promoted
without a separate review decision and the original PDF bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .evidence_package import verify_package


OPERATORS = {"AND", "OR", "MAX", "MIN", "NONE"}
KINDS = {"clause", "table_row"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def review_package(output: Path, decisions: Path, source_pdf: Path) -> dict:
    """Export only reviewer-selected units after strict source-anchor checks."""
    output, decisions, source_pdf = Path(output), Path(decisions), Path(source_pdf)
    verify_package(output, source_pdf=source_pdf)
    package = json.loads((output / "evidence-package.json").read_text(encoding="utf-8"))
    extraction_report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    request = json.loads(decisions.read_text(encoding="utf-8"))
    doc = package["document"]
    if request.get("schema_version") != 1 or request.get("source_pdf_sha256") != doc["pdf_sha256"]:
        raise ValueError("review decision must bind the exact source PDF SHA-256")
    rights = request.get("display_rights")
    if rights not in {"full", "cite_only"}:
        raise ValueError("display_rights must be explicitly set to full or cite_only")
    reviewer = request.get("reviewer")
    reviewed_at = request.get("reviewed_at")
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise ValueError("reviewer and reviewed_at are required")
    if request.get("basis") != "original_pdf_visual_and_text_check":
        raise ValueError("review basis must confirm original PDF visual and text check")
    pages = {p["page"]: p for p in _read_jsonl(output / "pages.jsonl")}
    tables = {t["id"]: t for t in json.loads((output / "tables.json").read_text(encoding="utf-8"))}
    units: list[dict] = []
    seen: set[str] = set()
    for selected in request.get("units", []):
        kind = selected.get("kind")
        page = pages.get(selected.get("page"))
        start, end = selected.get("char_start"), selected.get("char_end")
        quote = selected.get("exact_quote")
        if (kind not in KINDS or page is None or not isinstance(start, int) or not isinstance(end, int)
                or start < 0 or end <= start or end > len(page["text"])
                or quote != page["text"][start:end] or page["selected_quality"]["needs_ocr"]):
            raise ValueError("reviewed unit needs a literal, usable page-text span")
        if selected.get("page_text_sha256") != page["text_sha256"]:
            raise ValueError("reviewed unit page hash mismatch")
        subject = selected.get("subject_key")
        operator = selected.get("logic_operator")
        heading = selected.get("heading_path")
        if (not isinstance(subject, str) or not subject.strip() or operator not in OPERATORS
                or not isinstance(heading, list) or not heading
                or any(not isinstance(part, str) or not part.strip() for part in heading)):
            raise ValueError("reviewed unit needs subject, logic operator, and heading path")
        conditions = selected.get("conditions")
        if not isinstance(conditions, list) or any(not isinstance(x, str) or not x.strip() for x in conditions):
            raise ValueError("conditions must be explicit strings")
        printed_label = selected.get("printed_label")
        normalized_label = selected.get("normalized_label")
        if printed_label is not None and (not isinstance(printed_label, str) or not quote.startswith(printed_label)):
            raise ValueError("printed label must be literal at the start of the reviewed quote")
        if normalized_label is not None and (not isinstance(normalized_label, str) or not normalized_label.strip()
                                             or not printed_label or selected.get("label_normalization_status") != "reviewer_interpretation"):
            raise ValueError("normalized label needs its literal printed label and interpretation status")
        anchor = None
        if kind == "table_row":
            table = tables.get(selected.get("table_id"))
            row_index = selected.get("row_index")
            header_index = selected.get("header_row_index")
            cell_indices = selected.get("cell_indices")
            if (table is None or table["page"] != page["page"] or
                    not isinstance(row_index, int) or not isinstance(header_index, int) or
                    row_index <= header_index or header_index < 0 or row_index >= len(table["cells"]) or
                    not isinstance(cell_indices, list) or not cell_indices):
                raise ValueError("table row and header must refer to the same extracted page table")
            row, header = table["cells"][row_index], table["cells"][header_index]
            if (any(not isinstance(i, int) or i < 0 or i >= len(row) or i >= len(header) for i in cell_indices)
                    or len(set(cell_indices)) != len(cell_indices) or
                    selected.get("row_cells") != [row[i] for i in cell_indices] or
                    selected.get("header_cells") != [header[i] for i in cell_indices] or
                    any(row[i] is None or not str(row[i]).strip() or header[i] is None or
                        not str(header[i]).strip() for i in cell_indices)):
                raise ValueError("table selection must preserve nonblank source cells and headers without inheritance")
            anchor = {"table_id": table["id"], "row_index": row_index,
                      "header_row_index": header_index, "cell_indices": cell_indices,
                      "row_cells": selected["row_cells"], "header_cells": selected["header_cells"],
                      "table_bbox": table["bbox"], "bbox_coordinates": table["bbox_coordinates"]}
            caption = selected.get("caption")
            if not isinstance(caption, str) or caption not in page["text"]:
                raise ValueError("reviewed table caption must occur on the source page")
            parent_text = caption + "\n" + " | ".join(str(x).replace("\n", " ") for x in anchor["header_cells"]) + "\n" + " | ".join(str(x).replace("\n", " ") for x in anchor["row_cells"])
        elif any(key in selected for key in ("table_id", "row_index", "row_cells")):
            raise ValueError("clause unit cannot carry a table row")
        else:
            parent_text = quote
        facts = selected.get("facts", [])
        if not isinstance(facts, list):
            raise ValueError("facts must be a list")
        checked_facts = []
        for fact in facts:
            value = fact.get("source_value_text") if isinstance(fact, dict) else None
            if not isinstance(value, str) or not value.strip():
                raise ValueError("reviewed fact requires a literal source value")
            if kind == "clause":
                if value not in quote:
                    raise ValueError("clause fact value is absent from reviewed quote")
            else:
                cell_index = fact.get("cell_index")
                if cell_index not in anchor["cell_indices"] or value not in str(table["cells"][row_index][cell_index]):
                    raise ValueError("table fact value is absent from selected source cell")
            checked_facts.append({"source_value_text": value,
                                  **({"cell_index": fact["cell_index"]} if kind == "table_row" else {})})
        stable = f"{doc['id']}:p{page['page']}:{kind}:{start}-{end}"
        if stable in seen:
            raise ValueError("duplicate reviewed unit")
        seen.add(stable)
        units.append({"unit_id": stable, "kind": kind, "page": page["page"],
                      "page_text_sha256": page["text_sha256"],
                      "span": {"start": start, "end": end, "encoding": "codepoint"},
                      "exact_quote": quote, "table_anchor": anchor, "heading_path": heading,
                      "parent_text": parent_text,
                      "subject_key": subject, "logic_operator": operator,
                      "printed_label": printed_label, "normalized_label": normalized_label,
                      "label_normalization_status": selected.get("label_normalization_status") if normalized_label else None,
                      "conditions": conditions, "facts": checked_facts,
                      "search_text": " > ".join(heading) + "\n" + parent_text,
                      "review": {"state": "reviewed", "reviewer": reviewer,
                                 "reviewed_at": reviewed_at, "basis": request["basis"]}})
    if not units:
        raise ValueError("review decisions contain no units")
    exported = {"schema_version": 1,
                "document": {"id": doc["id"], "source_document_id": request.get("source_document_id"),
                             "title": doc["title"], "source_url": doc["source_url"],
                             "pdf_sha256": doc["pdf_sha256"], "display_rights": rights,
                             "version_status": "unresolved", "version_id": None, "issued_on": None,
                             "source_extraction_complete": extraction_report["complete_for_embedding"]},
                "units": units,
                "pages": [{"page": p["page"], "text": p["text"], "text_sha256": p["text_sha256"],
                           "method": p["method"], "review_state": "source_verified"}
                          for p in pages.values() if p["page"] in {u["page"] for u in units}],
                "source_package_sha256": _sha((output / "evidence-package.json").read_bytes()),
                "review_decisions_sha256": _sha(decisions.read_bytes())}
    target = output / "reviewed-evidence.json"
    target.write_text(json.dumps(exported, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"document_id": doc["id"], "reviewed_units": len(units),
            "reviewed_facts": sum(len(u["facts"]) for u in units), "file": str(target)}
