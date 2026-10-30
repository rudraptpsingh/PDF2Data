"""Source-bound native page repairs for verified portable PDF packages.

Only PDFium/pypdf consensus text with an independently recorded source-raster
coverage proof may replace a page. Candidate facts/relations for that page are
rebuilt; every other page and its candidates are copied byte-for-byte logically.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .evidence_package import create_package, verify_package
from .pdf_ingest import FACT, independent_text_coverage, text_quality
from .quality_gate import assess_page
from .structure_candidates import page_candidates, table_candidates


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _numbers(text: str) -> list[str]:
    return re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text)


def repair_native_page(package: Path, receipt: dict) -> dict:
    """Replace one page atomically after checking original bytes and source proof.

    The caller supplies a source-bound machine receipt, never arbitrary text.
    Failure leaves the original verified package in place.
    """
    import pdfplumber
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from io import BytesIO

    package = Path(package)
    source = Path(receipt["source_pdf_path"])
    before = verify_package(package, source_pdf=source)
    doc = json.loads((package / "evidence-package.json").read_text())["document"]
    page_no = receipt["page"]
    if (receipt["source_pdf_sha256"] != doc["pdf_sha256"] or
            receipt["source_url"] != doc["source_url"] or
            _sha(source.read_bytes()) != doc["pdf_sha256"]):
        raise ValueError("source PDF identity mismatch")
    pages = _rows(package / "pages.jsonl")
    old = pages[page_no - 1]
    if old["page"] != page_no or old["text_sha256"] != receipt["original_text_sha256"] or old["method"] != "pdf_text":
        raise ValueError("original page changed")
    pdfium_doc = pdfium.PdfDocument(str(source))
    proposed = pdfium_doc[page_no - 1].get_textpage().get_text_range().replace("\r\n", "\n").strip()
    independent = (PdfReader(str(source), strict=False).pages[page_no - 1].extract_text() or "")
    if (proposed != receipt["proposed_text"] or
            _sha(proposed.encode()) != receipt["proposed_text_sha256"] or
            _words(proposed) != _words(independent) or _numbers(proposed) != _numbers(independent) or
            text_quality(proposed)["needs_ocr"]):
        raise ValueError("native parser consensus changed")
    proof = receipt["source_render_evidence"]
    if (proof.get("method") != "native_glyph_raster_coverage" or not proof.get("page_complete") or
            not proof.get("reading_order_proven") or proof.get("unaccounted_ink_pixels") != 0 or
            proof.get("unresolved_visual_regions") or proof.get("source_pdf_sha256") != doc["pdf_sha256"] or
            proof.get("page_text_sha256") != receipt["proposed_text_sha256"] or proof.get("page") != page_no):
        raise ValueError("incomplete source raster proof")
    with pdfplumber.open(source) as pdf:
        original_page = pdf.pages[page_no - 1]
        raster = original_page.to_image(resolution=proof["render_dpi"]).original
        stream = BytesIO()
        raster.save(stream, format="PNG")
        if _sha(stream.getvalue()) != proof.get("rendered_image_sha256"):
            raise ValueError("source rendering changed")
        glyphs = "".join(str(c.get("text", "")) for c in original_page.chars)
        tables = json.loads((package / "tables.json").read_text())
        page_tables = [row for row in tables if row["page"] == page_no]
        from .native_form_layout import prove_empty_form_grid
        layout_proof = prove_empty_form_grid(original_page, page_tables,
            source_pdf_sha256=doc["pdf_sha256"], page_text_sha256=receipt["proposed_text_sha256"],
            physical_page=page_no, source_geometry=proof)
    for left, right in ((proposed, independent), (independent, proposed), (proposed, glyphs), (glyphs, proposed)):
        coverage = independent_text_coverage(left, right)
        if coverage["char_recall"] != 1 or coverage["digit_recall"] not in (None, 1):
            raise ValueError("native glyph coverage changed")
    source_omission = "[…]" in proposed and "[…]" in independent and "[…]" in glyphs
    if "[…]" in proposed and not source_omission:
        raise ValueError("unverified source omission marker")
    revised = {**old, "text": proposed, "chars": len(proposed), "text_sha256": receipt["proposed_text_sha256"],
               "selected_quality": text_quality(proposed),
               "independent_coverage": independent_text_coverage(proposed, independent),
               "status": "ready", "native_consensus": {"engines": ["pdfium", "pypdf"],
                   "source_render_sha256": proof["rendered_image_sha256"]}}
    if source_omission:
        revised["source_omission"] = "literal_marker_printed_in_original_pdf"
    revised.pop("short_source_evidence", None)
    source_geometry = {**proof, "source_pdf_sha256": doc["pdf_sha256"],
                       "page_text_sha256": revised["text_sha256"]}
    gate = assess_page(revised, source_pdf_sha256=doc["pdf_sha256"], package_pdf_sha256=doc["pdf_sha256"],
                       independent_text=independent, source_geometry=source_geometry,
                       table_count=len(page_tables), layout_proof=layout_proof)
    if gate["outcome"] != "auto_accepted":
        raise ValueError(f"quality gate rejected repair: {gate['failed_components']}")
    if page_tables and layout_proof is None:
        raise ValueError("table is not a source-proven empty form grid")

    parent = package.parent
    staging = Path(tempfile.mkdtemp(prefix=f".{package.name}.repair-", dir=parent))
    backup = parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, staging, dirs_exist_ok=True)
        pages[page_no - 1] = revised
        _write_rows(staging / "pages.jsonl", pages)
        inputs = _rows(staging / "embedding-input.jsonl")
        meta = {"parent_document_id": doc["id"], "page": page_no,
                "source_pdf_sha256": doc["pdf_sha256"], "page_text_sha256": revised["text_sha256"],
                "source_title": doc["title"]}
        if doc["source_url"]:
            meta["url"] = doc["source_url"]
        replacement = {"id": revised["id"], "text": proposed, "metadata": meta}
        inputs = [row for row in inputs if row["id"] != revised["id"]] + [replacement]
        inputs.sort(key=lambda row: row["metadata"]["page"])
        _write_rows(staging / "embedding-input.jsonl", inputs)
        facts = [row for row in _rows(staging / "fact-candidates.jsonl") if row["page"] != page_no]
        for match in FACT.finditer(proposed):
            value = match.group().strip()
            kind = ("currency" if re.match(r"₹|INR|Rs\.?", value, re.I) else
                    "percentage" if "%" in value else "capacity" if re.search(r"\b[mk]w", value, re.I) else
                    "duration" if re.search(r"\b(?:days?|months?|years?)\b", value, re.I) else "date")
            facts.append({"page": page_no, "page_id": revised["id"], "page_text_sha256": revised["text_sha256"],
                          "char_start": match.start(), "char_length": match.end()-match.start(),
                          "kind": kind, "value_text": value,
                          "context": re.sub(r"\s+", " ", proposed[max(0,match.start()-90):match.end()+90]).strip(),
                          "status": "unverified_candidate"})
        facts.sort(key=lambda row: (row["page"], row["char_start"]))
        _write_rows(staging / "fact-candidates.jsonl", facts)
        structures = _rows(staging / "structure-candidates.jsonl")
        table_ids = {row["id"] for row in page_tables}
        def on_page(row: dict) -> bool:
            if row.get("table_id") in table_ids:
                return True
            return any(span.get("page") == page_no for span in row.get("evidence", []))
        structures = [row for row in structures if not on_page(row)] + page_candidates(revised)
        for table in page_tables:
            structures.extend(table_candidates(table, revised))
        _write_rows(staging / "structure-candidates.jsonl", structures)
        report = json.loads((staging / "report.json").read_text())
        report["issues"] = [row for row in report["issues"] if row.get("page") != page_no]
        report["issues"].append({"page": page_no, "kind": "native_consensus_repair",
                                 "detail": "PDFium and pypdf ordered text agree; source raster fully accounted",
                                 "source_render_sha256": proof["rendered_image_sha256"]})
        if layout_proof:
            report["issues"].append({"page": page_no, "kind": "native_empty_form_grid",
                                     "detail": "Only one prose cell and ordinal placeholders; all blank cells checked against source glyphs",
                                     "table_id": layout_proof["table_id"],
                                     "table_cells_sha256": layout_proof["table_cells_sha256"]})
        if source_omission:
            report["issues"].append({"page": page_no, "kind": "source_authored_omission",
                                     "detail": "Literal […] is printed in the original PDF; omitted words are unknown"})
        report["page_status_counts"] = {status: sum(p["status"] == status for p in pages)
                                        for status in ("ready", "blank", "review_required")}
        report["text_chars"] = sum(p["chars"] for p in pages)
        report["numeric_candidates"] = len(facts)
        report["structure_candidates"] = len(structures)
        report["requires_source_review"] = report["page_status_counts"]["review_required"] > 0 or bool(tables)
        from .pdf_ingest import BLOCKING_ISSUES
        report["complete_for_embedding"] = not any(row["kind"] in BLOCKING_ISSUES for row in report["issues"])
        (staging / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        create_package(staging)
        verify_package(staging, source_pdf=source)
        os.replace(package, backup)
        try:
            os.replace(staging, package)
        except Exception:
            os.replace(backup, package)
            raise
        shutil.rmtree(backup)
        return {"document_id": doc["id"], "page": page_no, "old_sha256": old["text_sha256"],
                "new_sha256": revised["text_sha256"], "quality_gate": gate["outcome"],
                "ready_pages_before": before["pages"] - len(before["review_required_pages"]),
                "ready_pages_after": len(pages) - report["page_status_counts"]["review_required"]}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
