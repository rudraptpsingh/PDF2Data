"""Source-bound paired footnote correction; never publishes a page by itself."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import re
import shutil
import tempfile
import uuid

from .evidence_package import create_package, verify_package
from .pdf_ingest import FACT, text_quality
from .quality_gate import assess_package
from .region_ocr import (corrected_footnote_text, propose_footnote_marker_link,
                         require_composite_word_agreement, source_bound_line_words,
                         source_bound_complete_line, compose_complete_line_proof,
                         compose_source_bound_word_line)
from .repair_pipeline import _bound_independent_text
from .structure_candidates import page_candidates


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def same_source_crop_marker(fresh: dict, recorded: dict) -> bool:
    """Compare stable crop evidence; cropped VLM context may vary by a letter."""
    return (fresh.get("status") == recorded.get("status") == "candidate" and
            fresh.get("marker") == recorded.get("marker") and
            all(fresh["regions"][role]["source_image_sha256"] ==
                recorded["regions"][role]["source_image_sha256"] and
                fresh["regions"][role]["rapidocr_lines"] ==
                recorded["regions"][role]["rapidocr_lines"]
                for role in ("body", "note")))


def recheck_footnote_receipts(package: Path, source: Path, number: int,
                              receipts: list[dict]) -> None:
    """Rerun every bounded crop proof against the same PDF and OCR lineage."""
    if not receipts:
        raise ValueError("footnote source receipts are missing")
    package, source = Path(package), Path(source)
    verify_package(package, source_pdf=source)
    report = json.loads((package / "report.json").read_text())
    page = next((row for row in _rows(package / "pages.jsonl") if row["page"] == number), None)
    if page is None:
        raise ValueError("footnote page is absent")
    independent_receipt = receipts[0]["independent_full_receipt"]
    with tempfile.TemporaryDirectory(prefix="footnote-crop-recheck-") as folder:
        for index, receipt in enumerate(receipts):
            if (receipt.get("kind") != "source_bound_footnote_marker_link" or
                    receipt.get("status") != "candidate" or
                    receipt.get("publication_allowed") is not False or
                    receipt.get("source_pdf") != str(source) or
                    receipt.get("page") != number or
                    receipt.get("source_pdf_sha256") != report["source_pdf_sha256"] or
                    receipt.get("original_page_text_sha256") != page["text_sha256"] or
                    receipt.get("independent_full_receipt") != independent_receipt):
                raise ValueError("footnote receipt provenance changed")
            check = propose_footnote_marker_link(package, source, number,
                independent_receipt, Path(folder) / str(index),
                receipt["body_line_index"], receipt["note_line_index"])
            fresh = json.loads(Path(check["receipt"]).read_text())
            # The proposal has independently re-proved the literal marker.
            if not same_source_crop_marker(fresh, receipt):
                raise ValueError("source crop or marker proof changed")


def apply_footnote_marker_repair(package: Path, receipts: list[dict],
                                 proof_root: Path,
                                 docling_output: Path | None = None,
                                 line_proofs: list[dict] | None = None,
                                 independent_receipt: dict | None = None,
                                 source_pdf: Path | None = None,
                                 visual_region: dict | None = None,
                                 visual_layout_python: Path | None = None) -> dict:
    """Reprove every source crop, compose markers, then run the full page gate.

    The caller supplies a disposable staging package. A failed gate leaves it
    unchanged, and a successful page still needs the ordinary commit/publisher.
    """
    package, proof_root = Path(package), Path(proof_root)
    # The Cloudflare corpus adapter stores its watched source packages here.
    # This helper mutates its input only after the local gate passes, so it
    # must never be pointed at that authoritative tree. The caller stages a
    # copy and merges it through commit_page_repairs with a source lock/CAS.
    if package.parent.name == "extracted" and package.parent.parent.name == "pdf-backfill":
        raise ValueError("authoritative extraction package cannot be used as repair staging")
    if not receipts and (not line_proofs or independent_receipt is None or source_pdf is None):
        raise ValueError("source-bound footnote or line proof and independent receipt required")
    first = receipts[0] if receipts else line_proofs[0]
    source = Path(first["source_pdf"] if receipts else source_pdf)
    verify_package(package, source_pdf=source)
    report = json.loads((package / "report.json").read_text())
    number = first["page"]
    pages = _rows(package / "pages.jsonl")
    old = next((row for row in pages if row["page"] == number), None)
    if (old is None or old.get("review_decision") or old["status"] != "review_required" or
            old["text_sha256"] != first["original_page_text_sha256"] or
            report["source_pdf_sha256"] != first["source_pdf_sha256"]):
        raise ValueError("stale footnote page or source identity")
    if visual_region is not None and (visual_layout_python is None or
            visual_region.get("source_pdf_sha256") != report["source_pdf_sha256"] or
            visual_region.get("physical_page") != number or
            visual_region.get("page_text_sha256") != old["text_sha256"] or
            visual_region.get("kind") != "picture"):
        raise ValueError("stale or incomplete visual region proof")
    independent_receipt = first["independent_full_receipt"] if receipts else independent_receipt
    independent = _bound_independent_text(
        independent_receipt, source, source_sha=report["source_pdf_sha256"],
        page=number, original_text_sha=old["text_sha256"],
        candidate_engine=old.get("ocr", {}).get("engine", old["method"]))
    if receipts:
        recheck_footnote_receipts(package, source, number, receipts)
        lines, independent_corrected = corrected_footnote_text(old, independent, receipts)
    else:
        lines, independent_corrected = [dict(line) for line in old["ocr"]["lines"]], independent
    line_proofs = line_proofs or []
    touched = {index for receipt in receipts for index in
               (receipt["body_line_index"], receipt["note_line_index"])}
    if len({proof.get("line_index") for proof in line_proofs}) != len(line_proofs):
        raise ValueError("duplicate source line correction")
    for proof in line_proofs:
        index = proof.get("line_index")
        if (proof.get("kind") not in {"source_bound_line_words", "source_bound_complete_line"} or
                proof.get("publication_allowed") is not False or
                proof.get("source_pdf_sha256") != report["source_pdf_sha256"] or
                proof.get("original_page_text_sha256") != old["text_sha256"] or
                index in touched or not isinstance(index, int)):
            raise ValueError("stale or overlapping source line correction")
        fresh = (source_bound_line_words(old, source, index, independent,
                                        dpi=proof["crop_dpi"],
                                        horizontal_padding=proof.get("crop_horizontal_padding", .005))
                 if proof["kind"] == "source_bound_line_words" else
                 source_bound_complete_line(old, source, index, independent,
                     dpi=proof["crop_dpi"], vertical_padding=proof.get("crop_vertical_padding", .01),
                     horizontal_padding=proof.get("crop_horizontal_padding", .01)))
        if fresh != proof:
            raise ValueError("source line proof changed")
        if proof["kind"] == "source_bound_complete_line":
            lines, independent_corrected = compose_complete_line_proof(
                lines, independent_corrected, proof)
            touched.add(index)
            continue
        lines[index]["text"] = proof["proposed_line"]
        # The full-page reader can omit punctuation even when the two
        # independently rerun crop readers both see it. Replace only this
        # uniquely located source line, never a whole-page OCR result.
        independent_corrected = compose_source_bound_word_line(independent_corrected, proof)
        touched.add(index)
    text = "\n".join(line["text"] for line in lines)
    require_composite_word_agreement(text, independent_corrected)
    if text == old["text"]:
        raise ValueError("footnote repair did not change source text")
    new_sha = sha256(text.encode()).hexdigest()
    revised = {**old, "text": text, "text_sha256": new_sha, "chars": len(text),
               "selected_quality": text_quality(text),
               "ocr": {**old["ocr"], "text": text, "lines": lines},
               "status": "review_required"}
    if receipts:
        revised["footnote_marker_proof"] = {"source_pdf_sha256": report["source_pdf_sha256"],
                   "original_page_text_sha256": old["text_sha256"],
                   "markers": [row["marker"] for row in receipts],
                   "source_crop_sha256": [row["regions"][role]["source_image_sha256"]
                                          for row in receipts for role in ("body", "note")]}
    if line_proofs:
        revised["source_line_proofs"] = [{"line_index": proof["line_index"],
            "crop_sha256": proof["crop_sha256"], "original_page_text_sha256": old["text_sha256"]}
            for proof in line_proofs]
    table = None
    if docling_output is not None:
        from .pdf_layout_import import _table_from_docling
        from .table_grid import derive_table_grid_proof
        docling_output = Path(docling_output)
        comparison = json.loads((docling_output / "comparison-report.json").read_text())
        comparison_rows = _rows(docling_output / "page-comparisons.jsonl")
        raw_path = docling_output / f"page-{number}.docling.json"
        raw = json.loads(raw_path.read_text())
        if (comparison.get("source_pdf_sha256") != report["source_pdf_sha256"] or
                len(comparison_rows) != 1 or
                comparison_rows[0].get("source_page") != number or
                comparison_rows[0].get("selected_text_sha256") != old["text_sha256"] or
                len(raw.get("tables") or []) != 1 or
                raw.get("tables") != comparison_rows[0].get("candidate_tables")):
            raise ValueError("Docling table candidate is stale or ambiguous")
        size = raw["pages"][str(number)]["size"]
        table = _table_from_docling(raw["tables"][0], page=revised,
            source_sha=report["source_pdf_sha256"],
            comparison_sha=sha256(raw_path.read_bytes()).hexdigest(), index=1,
            page_width=size["width"], page_height=size["height"],
            engine_version=comparison_rows[0]["candidate_engine_version"],
            language=comparison_rows[0]["candidate_language"],
            device=comparison_rows[0]["requested_device"])
        table["rendered_image_sha256"] = sha256(
            (docling_output / f"page-{number}.source.png").read_bytes()).hexdigest()
        revised["table_grid_proof"] = derive_table_grid_proof(revised, table, source)
    temporary = package.parent / f".{package.name}.footnote-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, temporary)
        _write_rows(temporary / "pages.jsonl", [revised if row["page"] == number else row for row in pages])
        if table is not None:
            tables = json.loads((temporary / "tables.json").read_text())
            if any(row["page"] == number for row in tables):
                raise ValueError("existing page table needs an explicit merge")
            tables.append(table)
            (temporary / "tables.json").write_text(
                json.dumps(tables, ensure_ascii=False, indent=2) + "\n")
            report["tables"] = len(tables)
        inputs = _rows(temporary / "embedding-input.jsonl")
        for row in inputs:
            if row["metadata"]["page"] == number:
                row["text"] = text
                row["metadata"]["page_text_sha256"] = new_sha
        _write_rows(temporary / "embedding-input.jsonl", inputs)
        facts = [row for row in _rows(temporary / "fact-candidates.jsonl") if row["page"] != number]
        for match in FACT.finditer(text):
            value = match.group().strip()
            kind = ("currency" if re.match(r"₹|INR|Rs\.?", value, re.I) else
                    "percentage" if "%" in value else "capacity" if re.search(r"\b[mk]w", value, re.I) else
                    "duration" if re.search(r"\b(?:days?|months?|years?)\b", value, re.I) else "date")
            facts.append({"page": number, "page_id": revised["id"], "page_text_sha256": new_sha,
                          "char_start": match.start(), "char_length": match.end()-match.start(),
                          "kind": kind, "value_text": value,
                          "context": re.sub(r"\s+", " ", text[max(0,match.start()-90):match.end()+90]).strip(),
                          "status": "unverified_candidate"})
        facts.sort(key=lambda row: (row["page"], row["char_start"]))
        _write_rows(temporary / "fact-candidates.jsonl", facts)
        structures = [row for row in _rows(temporary / "structure-candidates.jsonl")
                      if not any(span.get("page") == number for span in row.get("evidence", []))]
        structures.extend(page_candidates(revised))
        if table is not None:
            from .structure_candidates import table_candidates
            structures.extend(table_candidates(table, revised))
        _write_rows(temporary / "structure-candidates.jsonl", structures)
        report["page_status_counts"] = {status: sum(row["status"] == status for row in pages)
                                        for status in ("ready", "blank", "review_required")}
        report.update(text_chars=sum(row["chars"] for row in pages) - old["chars"] + len(text),
                      numeric_candidates=len(facts), structure_candidates=len(structures),
                      requires_source_review=True)
        report.setdefault("issues", []).append({"page": number,
            "kind": "source_bound_footnote_marker_repair" if receipts else "source_bound_line_repair",
            "original_text_sha256": old["text_sha256"],
            "page_text_sha256": new_sha})
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        create_package(temporary)
        verify_package(temporary, source_pdf=source)
        gate = assess_package(temporary, source, pages=[number],
                              independent_by_page={number: independent_corrected},
                              visual_region_by_page=({number: visual_region} if visual_region else None),
                              visual_layout_python=visual_layout_python,
                              derive_ocr_source_raster=True)
        proof_root.mkdir(parents=True, exist_ok=True)
        (proof_root / f"p{number}-composite-quality.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2) + "\n")
        outcome = gate["results"][0]["outcome"]
        if outcome != "auto_accepted":
            row = gate["results"][0]
            raise ValueError(f"footnote page remains held: failed={row['failed_components']} "
                             f"missing={row['missing_evidence']}")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"page": number, "old_text_sha256": old["text_sha256"],
                "new_text_sha256": new_sha, "quality_gate": outcome,
                "linked_markers": len(receipts)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
