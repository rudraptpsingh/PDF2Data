"""Opt-in local vision suggestions for one source-bound OCR line at a time.

This is a diagnostic layer. A suggestion never changes the page package or
becomes searchable without the existing whole-page gate and guarded publisher.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from hashlib import sha256
from pathlib import Path
from io import BytesIO
import difflib
import json
import re
import time
import unicodedata

from PIL import Image
import pypdfium2 as pdfium

from .evidence_package import verify_package
from .ocr import _rapidocr_lines, _rapidocr_model_sha256
from .region_ocr import PADDLE_MODEL, PADDLE_REVISION, _vlm_transcribe
from .repair_pipeline import _bound_independent_text


_MODEL_CACHE = None


def _literal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _terminal_note_notation(value: str) -> str:
    """Compare two printed superscript notations without altering other text."""
    return re.sub(r"\s*\[([0-9]{1,2})\](?=[:;.]?$)", r"]\1", value)


def choose_crop_line(selected: str, independent: str | None,
                     vision: str, rapid: str) -> dict:
    """Require both crop readers to cover the same complete literal line."""
    selected, vision, rapid = map(_literal, (selected, vision, rapid))
    independent = _literal(independent) if independent else None
    if not selected or not vision or not rapid:
        return {"choice": None, "reason": "incomplete_crop_readers"}
    if vision != rapid and _terminal_note_notation(vision) != _terminal_note_notation(rapid):
        return {"choice": None, "reason": "crop_readers_disagree"}
    if vision == selected:
        return {"choice": "selected", "reason": "complete_source_crop_agreement"}
    if independent and (vision == independent or
            _terminal_note_notation(vision) == _terminal_note_notation(independent)) and independent != selected:
        # An OCR reader that drops most of a legal clause must never win just
        # because both crop readers happen to see its surviving short tail.
        if difflib.SequenceMatcher(None, selected, independent, autojunk=False).ratio() < .76:
            return {"choice": None, "reason": "candidate_omits_source_context"}
        return {"choice": "independent", "reason": "complete_source_crop_agreement"}
    return {"choice": None, "reason": "neither_complete_line_matched"}


def _independent_line(selected: str, lines: list[str]) -> str | None:
    """Align only a unique near-identical independent line; never synthesize."""
    ranked = sorted(((difflib.SequenceMatcher(None, _literal(selected),
                   _literal(line), autojunk=False).ratio(), line) for line in lines),
                   reverse=True)
    if not ranked or ranked[0][0] < .76 or (len(ranked) > 1 and
            ranked[0][0] - ranked[1][0] < .08):
        return None
    return ranked[0][1]


def _aligned_candidate(selected: str, selected_lines: list[str],
                       independent_lines: list[str]) -> str | None:
    if sum(_literal(line) == _literal(selected) for line in selected_lines) > 1:
        return None
    return _independent_line(selected, independent_lines)


def _table_cell_candidate(lines: list[dict], index: int) -> bool:
    """Conservatively flag dense same-row cells; line OCR cannot prove columns."""
    target = lines[index]
    y = (target["bbox"][1] + target["bbox"][3]) / 2
    peers = [row for j, row in enumerate(lines) if j != index and
             abs((row["bbox"][1] + row["bbox"][3]) / 2 - y) <= .008 and
             (row["bbox"][2] <= target["bbox"][0] or
              row["bbox"][0] >= target["bbox"][2])]
    return len(peers) >= 1 and bool(re.search(r"\d", target["text"]))


def _script_drift(selected: str, vision: str) -> bool:
    """Catch a Devanagari source transcribed into another Indic script."""
    return bool(re.search(r"[\u0900-\u097f]", selected) and
                re.search(r"[\u0980-\u0fff]", vision))


def candidate_line_indices(page: dict, independent: str, *, limit: int = 4) -> list[int]:
    """Prioritize differing lines, without relying on document-specific terms."""
    if not 1 <= limit <= 8:
        raise ValueError("line review budget must be 1..8")
    lines = page["ocr"]["lines"]
    selected = [row["text"] for row in lines]
    other = independent.splitlines()
    candidates = []
    for i, text in enumerate(selected):
        if len(text.strip()) < 4:
            continue
        aligned = _aligned_candidate(text, selected, other)
        if aligned is not None and _literal(aligned) == _literal(text):
            continue
        # Numbers, units, legal operators and amendment markers carry more
        # potential answer risk than typographic differences. The selection
        # only determines review order; it never changes the OCR output.
        risk = int(bool(re.search(r"\d", text))) * 3
        risk += int(bool(re.search(r"\b(?:shall|must|may|except|unless)\b", text, re.I))) * 2
        risk += int(bool(re.search(r"[₹%]|\bkWh\b|\bMW\b", text))) * 2
        risk += int(_table_cell_candidate(lines, i))
        candidates.append((-risk, i))
    return [i for _, i in sorted(candidates)[:limit]]


def propose_local_vision_merge(package: Path, source_pdf: Path,
                               independent_receipt: dict, page_number: int,
                               line_indices: list[int], output: Path,
                               *, dpi: int = 300) -> dict:
    """Compare selected and independent OCR against bounded original-PDF crops."""
    package, source_pdf, output = map(Path, (package, source_pdf, output))
    if not 120 <= dpi <= 360 or not 1 <= len(line_indices) <= 8 or len(set(line_indices)) != len(line_indices):
        raise ValueError("physical page, 1..8 distinct lines and 120..360 DPI required")
    verify_package(package, source_pdf=source_pdf)
    report = json.loads((package / "report.json").read_text())
    page = next((row for row in map(json.loads, jsonl_physical_lines((package / "pages.jsonl").read_text()))
                 if row["page"] == page_number), None)
    if not page or page["status"] != "review_required" or page.get("review_decision"):
        raise ValueError("source page is absent or not held")
    independent = _bound_independent_text(independent_receipt, source_pdf,
        source_sha=report["source_pdf_sha256"], page=page_number,
        original_text_sha=page["text_sha256"],
        candidate_engine=page["ocr"]["engine"])
    lines = page["ocr"]["lines"]
    if any(i < 0 or i >= len(lines) for i in line_indices):
        raise ValueError("line index outside source page")
    global _MODEL_CACHE
    load_started = time.monotonic()
    if _MODEL_CACHE is None:
        from mlx_vlm import load
        _MODEL_CACHE = load(PADDLE_MODEL, revision=PADDLE_REVISION)
    model, processor = _MODEL_CACHE
    model_load_ms = round((time.monotonic() - load_started) * 1000)
    pdf = pdfium.PdfDocument(str(source_pdf))
    try:
        physical = pdf[page_number - 1]
        try:
            rendered = physical.render(scale=dpi / 72).to_pil().copy()
        finally:
            physical.close()
    finally:
        pdf.close()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    preview = [row["text"] for row in lines]
    unresolved = []
    for i in line_indices:
        line_started = time.monotonic()
        old = lines[i]["text"]
        box = lines[i]["bbox"]
        pixels = [max(0, int((box[0] - .012) * rendered.width)),
                  max(0, int((box[1] - .012) * rendered.height)),
                  min(rendered.width, int((box[2] + .012) * rendered.width)),
                  min(rendered.height, int((box[3] + .012) * rendered.height))]
        crop = rendered.crop(tuple(pixels))
        data = BytesIO(); crop.save(data, format="PNG"); image_bytes = data.getvalue()
        image_sha = sha256(image_bytes).hexdigest()
        image_path = output / f"p{page_number}-l{i}-{image_sha[:12]}.png"
        image_path.write_bytes(image_bytes)
        vision = _vlm_transcribe(model, processor, image_path)
        large = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
        language_model = "devanagari" if re.search(r"[\u0900-\u097f]", old) else "en"
        rapid_rows = _rapidocr_lines(large, language_model)
        rapid = " ".join(row["text"] for row in (rapid_rows[0] if rapid_rows else []))
        # A repeated clause fragment cannot be assigned to a particular
        # independent OCR location from text alone (the DSM 400 MW proviso
        # appears twice with different operative consequences).
        aligned = _aligned_candidate(old, [row["text"] for row in lines],
                                     independent.splitlines())
        decision = choose_crop_line(old, aligned, vision, rapid)
        if _script_drift(old, vision):
            decision = {"choice": None, "reason": "vision_script_drift"}
        table_cell = _table_cell_candidate(lines, i)
        if table_cell:
            decision = {"choice": None, "reason": "table_structure_unverified"}
        candidate_line = (old if decision["choice"] == "selected" else
                          aligned if decision["choice"] == "independent" else None)
        if candidate_line is None:
            unresolved.append(i)
        else:
            preview[i] = candidate_line
        receipt = {"kind": "local_vision_merge_candidate", "source_pdf": str(source_pdf),
                   "source_pdf_sha256": report["source_pdf_sha256"],
                   "document_id": report["document_id"], "page": page_number,
                   "original_page_text_sha256": page["text_sha256"], "line_index": i,
                   "selected_line": old, "independent_line": aligned,
                   "table_cell_candidate": table_cell,
                   "crop_bbox_pixels": pixels, "crop_sha256": image_sha,
                   "crop_path": str(image_path), "render_dpi": dpi,
                   "model": PADDLE_MODEL, "model_revision": PADDLE_REVISION,
                   "rapidocr_recognition_model": language_model,
                   "rapidocr_model_sha256": _rapidocr_model_sha256(language_model),
                   "vision_crop_text": vision, "rapid_crop_text": rapid,
                   "candidate_line": candidate_line,
                   "confidence": "two_crop_reader_agreement" if candidate_line else "uncertain",
                   "elapsed_ms": round((time.monotonic() - line_started) * 1000),
                   "decision": decision, "publication_allowed": False}
        path = output / f"p{page_number}-l{i}-merge.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        results.append({"line_index": i, **decision, "candidate_line": candidate_line,
                        "receipt": str(path)})
    preview_text = "\n".join(preview)
    preview_path = output / f"p{page_number}-unverified-merge.txt"
    preview_path.write_text(preview_text, encoding="utf-8")
    return {"page": page_number, "lines": results, "model_load_ms": model_load_ms,
            "preview_path": str(preview_path),
            "preview_text_sha256": sha256(preview_text.encode()).hexdigest(),
            "unresolved_line_indices": unresolved,
            "publication_allowed": False}


def run_local_vision_batch(manifest: Path, independent_path: Path, output: Path,
                           *, base: Path, limit: int = 20, lines_per_page: int = 4) -> dict:
    """Resumable local candidate pass over held pages; never approves a page."""
    manifest, independent_path, output, base = map(Path, (manifest, independent_path, output, base))
    if not 1 <= limit <= 500 or not 1 <= lines_per_page <= 8:
        raise ValueError("bounded page and line budgets required")
    output.mkdir(parents=True, exist_ok=True)
    independent = {(row["source_pdf_sha256"], row["page"],
                    row["selected_page_text_sha256"]): row
                   for row in map(json.loads, jsonl_physical_lines(independent_path.read_text()))}
    ledger = output / "results.jsonl"
    previous = [json.loads(line) for line in jsonl_physical_lines(ledger.read_text())] if ledger.exists() else []
    jobs = []
    for row in map(json.loads, jsonl_physical_lines(manifest.read_text())):
        package = (base / row["package"]).resolve()
        source = (base / row["source_pdf"]).resolve()
        for number in row.get("pages", [row.get("page")]):
            if number is not None:
                jobs.append((package, source, number))
            if len(jobs) >= limit:
                break
        if len(jobs) >= limit:
            break
    current_keys = set()
    for package, _, number in jobs:
        report = json.loads((package / "report.json").read_text())
        page = next((r for r in map(json.loads, jsonl_physical_lines((package / "pages.jsonl").read_text()))
                     if r["page"] == number), None)
        if page:
            current_keys.add((report["source_pdf_sha256"], number, page["text_sha256"]))
    previous = [r for r in previous if (r["source_pdf_sha256"], r["page"],
                                        r["page_text_sha256"]) in current_keys]
    done = {(r["source_pdf_sha256"], r["page"], r["page_text_sha256"])
            for r in previous if r.get("status") == "completed"}
    started = time.monotonic()
    results = previous
    for package, source, number in jobs:
        report = json.loads((package / "report.json").read_text())
        page = next((r for r in map(json.loads, jsonl_physical_lines((package / "pages.jsonl").read_text()))
                     if r["page"] == number), None)
        if page is None:
            continue
        key = (report["source_pdf_sha256"], number, page["text_sha256"])
        if key in done:
            continue
        record = {"source_pdf_sha256": key[0], "page": number,
                  "page_text_sha256": key[2], "source_url": report.get("source_url"),
                  "document_id": report.get("document_id"), "status": "held",
                  "publication_allowed": False}
        receipt = independent.get(key)
        try:
            if receipt is None:
                record["reason"] = "bound_independent_receipt_missing"
            elif page["status"] != "review_required":
                record["reason"] = "page_not_held"
            else:
                text = _bound_independent_text(receipt, source, source_sha=key[0],
                    page=number, original_text_sha=key[2],
                    candidate_engine=page["ocr"]["engine"])
                indices = candidate_line_indices(page, text, limit=lines_per_page)
                if not indices:
                    record["reason"] = "no_differing_source_lines"
                else:
                    one = propose_local_vision_merge(package, source, receipt, number,
                        indices, output / f"{key[0][:12]}-p{number}")
                    uncertain_fields = [{"line_index": x["line_index"],
                        "selected_text": page["ocr"]["lines"][x["line_index"]]["text"],
                        "reason": x["reason"],
                        "numeric": bool(re.search(r"\d|[₹%]", page["ocr"]["lines"][x["line_index"]]["text"]))}
                        for x in one["lines"] if x["choice"] is None]
                    record.update({"status": "completed", "reviewed_lines": len(indices),
                                   "selected": sum(x["choice"] == "selected" for x in one["lines"]),
                                   "independent": sum(x["choice"] == "independent" for x in one["lines"]),
                                   "uncertain": len(one["unresolved_line_indices"]),
                                   "uncertain_fields": uncertain_fields,
                                   "numeric_uncertainty": any(x["numeric"] for x in uncertain_fields),
                                   "numeric_claim_allowed": False,
                                   "confidence": "provisional_crop_review",
                                   "preview_path": one["preview_path"],
                                   "preview_text_sha256": one["preview_text_sha256"],
                                   "unresolved_line_indices": one["unresolved_line_indices"]})
        except Exception as error:
            record.update({"status": "held", "reason": f"{type(error).__name__}: {error}"})
        results.append(record)
        ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n")
        progress = {"planned": len(jobs), "processed": len(results),
                    "completed": sum(r["status"] == "completed" for r in results),
                    "selected_lines": sum(r.get("selected", 0) for r in results),
                    "independent_lines": sum(r.get("independent", 0) for r in results),
                    "uncertain_lines": sum(r.get("uncertain", 0) for r in results),
                    "elapsed_s": round(time.monotonic() - started, 2),
                    "publication_allowed": False}
        temporary = output / "progress.tmp"
        temporary.write_text(json.dumps(progress, indent=2) + "\n")
        temporary.replace(output / "progress.json")
    return json.loads((output / "progress.json").read_text()) if (output / "progress.json").exists() else {
        "planned": len(jobs), "processed": 0, "publication_allowed": False}
