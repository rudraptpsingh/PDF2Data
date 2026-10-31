"""Portable PDF evidence receipt. It links artifacts; it does not certify claims.

The format deliberately keeps page text, candidate observations, and vector
records separate. A local chat and a hosted importer can verify the same
hashes/anchors without trusting a model's interpretation of a PDF.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

import hashlib
import json
from collections import Counter
from pathlib import Path

from .evidence import check_quote
from .presentation import export_presentation, verify_presentation


FILES = ("report.json", "pages.jsonl", "tables.json", "fact-candidates.jsonl",
         "structure-candidates.jsonl", "embedding-input.jsonl")


def _sha(path: Path) -> str:
    from .hashing import file_sha256
    return file_sha256(path)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="\n") as stream:
        return [json.loads(line) for line in stream if line.strip(" \t\r\n")]


def create_package(output: Path) -> dict:
    """Write a small, immutable-content manifest after PDF extraction."""
    output = Path(output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    files = {}
    for name in FILES:
        path = output / name
        if not path.is_file():
            raise ValueError(f"missing evidence artifact: {name}")
        files[name] = {"sha256": _sha(path)}
    package = {
        "schema_version": 1,
        "document": {
            "id": report["document_id"],
            "title": report["title"],
            "source_url": report.get("source_url"),
            "pdf_sha256": report["source_pdf_sha256"],
            "version": {"status": "unresolved", "instrument_id": None,
                        "issued_on": None, "effective_on": None},
        },
        "units": {"granularity": "page", "file": "pages.jsonl",
                  "clause_hierarchy": "review_required"},
        "facts": {"file": "fact-candidates.jsonl", "status": "unverified_candidates_only"},
        "relations": {"file": "structure-candidates.jsonl", "status": "review_required",
                      "table_file": "tables.json", "amendment_edges": "unresolved"},
        "vectors": {"input_file": "embedding-input.jsonl", "status": "input_only",
                    "record_id": "page.id", "document_id": "metadata.parent_document_id",
                    "page_sha256": "metadata.page_text_sha256",
                    "offset_encoding": "codepoint"},
        "files": files,
    }
    paddle = output / "paddle-layout-candidates.jsonl"
    if paddle.is_file():
        package["candidate_artifacts"] = {paddle.name: _sha(paddle)}
    from .provisions import export_candidates
    provision_receipt = export_candidates(output, _rows(output / "pages.jsonl"), report["source_pdf_sha256"])
    package["provision_candidates"] = provision_receipt
    package["presentation"] = export_presentation(output, report)
    (output / "evidence-package.json").write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    verify_package(output)
    return package


def verify_package(output: Path, vectors: Path | None = None, source_pdf: Path | None = None) -> dict:
    """Check all local joins and literal spans; never promote candidate facts."""
    output = Path(output)
    package = json.loads((output / "evidence-package.json").read_text(encoding="utf-8"))
    if package.get("schema_version") != 1 or set(package.get("files", {})) != set(FILES):
        raise ValueError("unsupported or incomplete evidence package")
    for name in FILES:
        path = output / name
        if not path.is_file() or _sha(path) != package["files"][name].get("sha256"):
            raise ValueError(f"evidence artifact hash mismatch: {name}")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    doc = package["document"]
    if (doc.get("id"), doc.get("pdf_sha256"), doc.get("title"), doc.get("source_url")) != (
            report.get("document_id"), report.get("source_pdf_sha256"),
            report.get("title"), report.get("source_url")):
        raise ValueError("document identity differs from extraction report")
    if source_pdf is not None and _sha(Path(source_pdf)) != doc["pdf_sha256"]:
        raise ValueError("original PDF hash mismatch")
    if (doc.get("version", {}).get("status") != "unresolved" or
            package.get("facts", {}).get("status") != "unverified_candidates_only" or
            package.get("relations", {}).get("status") != "review_required" or
            package.get("vectors", {}).get("status") != "input_only"):
        raise ValueError("candidate evidence was improperly promoted")
    pages = _rows(output / "pages.jsonl")
    by_id = {}
    for page in pages:
        page_id = page.get("id")
        if (not isinstance(page_id, str) or page_id in by_id or
                page_id != f"{doc['id']}:p{page.get('page')}" or
                page.get("text_sha256") != hashlib.sha256(page["text"].encode()).hexdigest()):
            raise ValueError("page identity or text hash mismatch")
        if (page.get("status") not in {"ready", "blank", "review_required"} or
                (page["status"] == "blank" and page["text"]) or
                (page["status"] == "ready" and (not page["text"] or page.get("method") != "pdf_text"))):
            raise ValueError("page review status contradicts extracted text")
        by_id[page_id] = page
    if len(pages) != report.get("pages"):
        raise ValueError("page count differs from extraction report")
    if [page.get("page") for page in pages] != list(range(1, len(pages)+1)):
        raise ValueError("physical PDF page sequence is missing or out of order")
    if "provision_candidates" in package:
        from .provisions import SCHEMA, validate_wrapper
        receipt = package["provision_candidates"]
        if receipt.get("file") != "provision-candidates.json" or _sha(output / receipt["file"]) != receipt.get("sha256"):
            raise ValueError("provision candidate artifact hash mismatch")
        candidates = json.loads((output / receipt["file"]).read_text(encoding="utf-8"))
        if candidates.get("schema_version") != SCHEMA or candidates.get("status") != "candidate_only":
            raise ValueError("provision candidates improperly promoted")
        for wrapper in candidates["provisions"]:
            if wrapper.get("boundary_state") != "candidate" or wrapper.get("semantic_state") != "unresolved":
                raise ValueError("provision candidates improperly promoted")
            validate_wrapper(wrapper, pages, doc["pdf_sha256"])
    if package.get("candidate_artifacts"):
        candidate_path = output / "paddle-layout-candidates.jsonl"
        if package["candidate_artifacts"].get(candidate_path.name) != _sha(candidate_path):
            raise ValueError("Paddle layout candidate hash mismatch")
        for row in _rows(candidate_path):
            page = pages[row["page"] - 1] if type(row.get("page")) is int and 1 <= row["page"] <= len(pages) else None
            if (not page or row.get("page_id") != page["id"] or
                    row.get("selected_page_text_sha256") != page["text_sha256"] or
                    row.get("source_pdf_sha256") != doc["pdf_sha256"] or
                    row.get("status") != "review_required"):
                raise ValueError("Paddle layout candidate source identity mismatch")
    verify_presentation(output, package, pages)
    original_pages = None
    if source_pdf is not None:
        from pypdf import PdfReader
        original_pages = len(PdfReader(source_pdf).pages)
        if original_pages != len(pages):
            raise ValueError("extracted pages do not cover the original PDF page count")
    if report.get("page_status_counts") != {
        status: sum(page["status"] == status for page in pages)
        for status in ("ready", "blank", "review_required")
    }:
        raise ValueError("page status counts differ from extraction report")
    inputs = _rows(output / "embedding-input.jsonl")
    for row in inputs:
        page = by_id.get(row.get("id"))
        meta = row.get("metadata", {})
        if (page is None or row.get("text") != page["text"] or
                meta.get("parent_document_id") != doc["id"] or
                meta.get("source_pdf_sha256") != doc["pdf_sha256"] or
                meta.get("page_text_sha256") != page["text_sha256"] or
                meta.get("page") != page["page"] or
                (doc.get("source_url") and meta.get("url") != doc["source_url"])):
            raise ValueError("embedding input does not link to its source page")
    if {row["id"] for row in inputs} != {page["id"] for page in pages if page["text"]}:
        raise ValueError("searchable page input coverage mismatch")
    facts = _rows(output / "fact-candidates.jsonl")
    for fact in facts:
        page = by_id.get(fact.get("page_id"))
        start, length = fact.get("char_start"), fact.get("char_length")
        if (page is None or fact.get("status") != "unverified_candidate" or
                fact.get("page") != page["page"] or
                fact.get("page_text_sha256") != page["text_sha256"] or
                not isinstance(start, int) or not isinstance(length, int) or
                start < 0 or length < 1 or page["text"][start:start+length] != fact.get("value_text")):
            raise ValueError("fact candidate lost literal page evidence")
    structures = _rows(output / "structure-candidates.jsonl")
    tables = {table["id"]: table for table in json.loads((output / "tables.json").read_text(encoding="utf-8"))}
    if len(tables) != report.get("tables"):
        raise ValueError("table count or identity differs from extraction report")
    checked_spans = 0
    for row in structures:
        if row.get("status") != "review_required":
            raise ValueError("unreviewed relation was promoted")
        spans = list(row.get("evidence", []))
        for key in ("operator_cues", "conjunction_cues", "subject_cues", "temporal_scope_cues"):
            spans.extend(row.get(key, []))
        if not spans and row.get("kind") != "table_cell":
            raise ValueError("structure candidate has no source evidence")
        for span in spans:
            page = by_id.get(span.get("page_id"))
            if (page is None or span.get("selected_method") != page["method"] or
                    span.get("page") != page["page"] or
                    check_quote(page, span)["source_quote"] != span.get("quote")):
                raise ValueError("structure candidate source span mismatch")
            checked_spans += 1
        if row.get("kind") == "table_cell" and not spans and row.get("span_status") != "ambiguous_or_not_in_selected_text":
            raise ValueError("table cell without quote is not marked ambiguous")
        if row.get("kind") == "table_cell":
            table = tables.get(row.get("table_id"))
            r, c = row.get("row"), row.get("column")
            parent_page = by_id.get(table["id"].split(":table")[0]) if table else None
            if (table is None or parent_page is None or not isinstance(r, int) or not isinstance(c, int) or
                    r < 0 or c < 0 or r >= len(table["cells"]) or
                    c >= len(table["cells"][r]) or
                    str(table["cells"][r][c]).strip() != row.get("value_text") or
                    table.get("page") != parent_page["page"]):
                raise ValueError("table-cell candidate does not match the extracted row and column")
    if len(facts) != report.get("numeric_candidates") or len(structures) != report.get("structure_candidates"):
        raise ValueError("candidate count differs from extraction report")
    result = {"document_id": doc["id"], "pages": len(pages), "embedding_inputs": len(inputs),
              "fact_candidates": len(facts), "structure_candidates": len(structures),
              "fact_candidates_by_kind": dict(sorted(Counter(row.get("kind", "unknown") for row in facts).items())),
              "fact_candidates_by_status": dict(sorted(Counter(row.get("status", "unknown") for row in facts).items())),
              "structure_candidates_by_kind": dict(sorted(Counter(row.get("kind", "unknown") for row in structures).items())),
              "structure_candidates_by_status": dict(sorted(Counter(row.get("status", "unknown") for row in structures).items())),
              "checked_structure_spans": checked_spans, "verified_facts": 0,
              "version_resolved": False, "vectors_checked": 0,
              "original_pdf_checked": source_pdf is not None,
              "original_pdf_pages_checked": original_pages,
              "integrity": "passed",
              "page_text_readiness": "review_required" if any(page["status"] == "review_required" for page in pages) else "complete",
              "review_required_pages": [page["page"] for page in pages if page["status"] == "review_required"],
              "structured_candidate_status": "unverified_candidates_only"}
    if vectors is not None:
        from .pipeline import verify
        vectors = Path(vectors)
        receipt = verify(vectors)
        manifest = json.loads((vectors / "manifest.json").read_text(encoding="utf-8"))
        if not receipt["complete"] or manifest["input_sha256"] != package["files"]["embedding-input.jsonl"]["sha256"]:
            raise ValueError("vector run is incomplete or used another evidence input")
        linked = set()
        for item in manifest["batches"]:
            for row in _rows(vectors / item["file"]):
                meta = row.get("metadata", {})
                page = by_id.get(meta.get("document_id"))
                start, length = meta.get("char_start"), meta.get("char_length")
                if (page is None or meta.get("parent_document_id") != doc["id"] or
                        meta.get("source_pdf_sha256") != doc["pdf_sha256"] or
                        meta.get("page_text_sha256") != page["text_sha256"] or
                        meta.get("offset_encoding") != "codepoint" or
                        not isinstance(start, int) or not isinstance(length, int) or
                        start < 0 or length < 1 or start + length > len(page["text"])):
                    raise ValueError("vector anchor does not link to the source page")
                linked.add(page["id"])
        if linked != {row["id"] for row in inputs}:
            raise ValueError("vector run did not cover every searchable page")
        result["vectors_checked"] = receipt["vectors"]
        result["embedding_model"] = receipt["model"]
    return result
