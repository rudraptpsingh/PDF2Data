"""Source-bound correction of one OCR word on an otherwise verified page."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import re
import shutil
import uuid

from .evidence_package import create_package, verify_package
from .footnote_repair import _rows, _write_rows
from .ocr import _rapidocr_lines, vision_crop_lines
from .pdf_ingest import FACT, text_quality
from .quality_gate import assess_package, WORD, _normalized
from .region_ocr import corrected_footnote_text, require_composite_word_agreement
from .repair_pipeline import _bound_independent_text
from .structure_candidates import page_candidates, table_candidates
from .table_grid import derive_table_grid_proof


def independent_for_page(page: dict, source: Path, full: dict, footnote: dict | None) -> str:
    source_sha = sha256(source.read_bytes()).hexdigest()
    if footnote is None:
        return _bound_independent_text(full, source, source_sha=source_sha, page=page["page"],
            original_text_sha=page["text_sha256"],
            candidate_engine=page.get("ocr", {}).get("engine", page["method"]))
    prior = page.get("footnote_marker_proof") or {}
    if (prior.get("source_pdf_sha256") != source_sha or
            prior.get("original_page_text_sha256") != footnote.get("original_page_text_sha256") or
            footnote.get("marker") not in prior.get("markers", []) or
            footnote.get("independent_full_receipt") != full):
        raise ValueError("prior footnote OCR lineage disagrees")
    old = {**page, "ocr": {**page["ocr"], "lines": [dict(x) for x in page["ocr"]["lines"]]}}
    for index_key, text_key in (("body_line_index", "body_selected_text"),
                                 ("note_line_index", "note_selected_text")):
        old["ocr"]["lines"][footnote[index_key]]["text"] = footnote[text_key]
    old["text"] = "\n".join(x["text"] for x in old["ocr"]["lines"])
    old["text_sha256"] = footnote["original_page_text_sha256"]
    if sha256(old["text"].encode()).hexdigest() != old["text_sha256"]:
        raise ValueError("prior source page cannot be reconstructed")
    baseline = _bound_independent_text(full, source, source_sha=source_sha,
        page=page["page"], original_text_sha=old["text_sha256"],
        candidate_engine=page.get("ocr", {}).get("engine", page["method"]))
    composed, independent = corrected_footnote_text(old, baseline, [footnote])
    if "\n".join(x["text"] for x in composed) != page["text"]:
        raise ValueError("current page differs from proven footnote composition")
    return independent


def propose_line_correction(package: Path, source: Path, number: int, line_index: int,
                            full: dict, footnote: dict | None = None) -> dict:
    """Require full independent OCR plus both RapidOCR and Vision source crops."""
    import pypdfium2 as pdfium
    package, source = Path(package), Path(source)
    verify_package(package, source_pdf=source)
    page = next(x for x in _rows(package / "pages.jsonl") if x["page"] == number)
    if (page.get("status") != "review_required" or page.get("review_decision") or
            not 0 <= line_index < len(page["ocr"]["lines"])):
        raise ValueError("held OCR page or line unavailable")
    independent = independent_for_page(page, source, full, footnote)
    old = page["ocr"]["lines"][line_index]["text"]
    old_words = WORD.findall(_normalized(old))
    matches = [line for line in independent.splitlines()
               if len(WORD.findall(_normalized(line))) == len(old_words)
               and sum(a != b for a, b in zip(WORD.findall(_normalized(line)), old_words)) == 1]
    if len(matches) != 1:
        raise ValueError("independent OCR has no unique one-word correction")
    proposed = matches[0]
    if re.findall(r"\d+(?:[.,]\d+)*", old) != re.findall(r"\d+(?:[.,]\d+)*", proposed):
        raise ValueError("numeric correction requires a separate source proof")
    b = page["ocr"]["lines"][line_index]["bbox"]
    with pdfium.PdfDocument(str(source)) as pdf:
        physical = pdf[number - 1]
        try:
            image = physical.render(scale=300 / 72).to_pil().copy()
        finally:
            physical.close()
    box = [max(0, int((b[0] - .01) * image.width)),
           max(0, int((b[1] - .01) * image.height)),
           min(image.width, int((b[2] + .01) * image.width)),
           min(image.height, int((b[3] + .01) * image.height))]
    crop = image.crop(tuple(box))
    buffer = BytesIO()
    crop.save(buffer, format="PNG")
    rapid = _rapidocr_lines(crop, "en")
    rapid_text = " ".join(x["text"] for x in rapid[0]) if rapid else ""
    vision_text = " ".join(x["text"] for x in vision_crop_lines(crop))
    if rapid_text != proposed or vision_text != proposed:
        raise ValueError("source crop readers disagree on proposed line")
    return {"schema_version": 1, "kind": "source_bound_line_correction",
            "source_pdf_sha256": sha256(source.read_bytes()).hexdigest(),
            "physical_page": number, "original_page_text_sha256": page["text_sha256"],
            "line_index": line_index, "old_line": old, "proposed_line": proposed,
            "crop_bbox_pixels": box, "crop_sha256": sha256(buffer.getvalue()).hexdigest(),
            "rapid_text": rapid_text, "vision_text": vision_text,
            "full_independent_receipt": full, "footnote_receipt": footnote,
            "independent_text_sha256": sha256(independent.encode()).hexdigest(),
            "publication_allowed": False}


def recheck_line_correction(package: Path, source: Path, receipt: dict) -> str:
    fresh = propose_line_correction(package, source, receipt["physical_page"],
        receipt["line_index"], receipt["full_independent_receipt"],
        receipt.get("footnote_receipt"))
    if fresh != receipt:
        raise ValueError("source crop or OCR lineage changed")
    page = next(x for x in _rows(Path(package) / "pages.jsonl")
                if x["page"] == receipt["physical_page"])
    return independent_for_page(page, Path(source), receipt["full_independent_receipt"],
                                receipt.get("footnote_receipt"))


def stage_line_correction(package: Path, source: Path, receipt: dict, quality_output: Path) -> dict:
    """Stage one changed page and require every whole-page gate to pass."""
    package, source, quality_output = Path(package), Path(source), Path(quality_output)
    independent = recheck_line_correction(package, source, receipt)
    number = receipt["physical_page"]
    pages = _rows(package / "pages.jsonl")
    old = next(x for x in pages if x["page"] == number)
    lines = [dict(x) for x in old["ocr"]["lines"]]
    lines[receipt["line_index"]]["text"] = receipt["proposed_line"]
    text = "\n".join(x["text"] for x in lines)
    require_composite_word_agreement(text, independent)
    digest = sha256(text.encode()).hexdigest()
    revised = {**old, "text": text, "text_sha256": digest, "chars": len(text),
               "selected_quality": text_quality(text),
               "ocr": {**old["ocr"], "text": text, "lines": lines},
               "source_line_proof": {"source_pdf_sha256": receipt["source_pdf_sha256"],
                   "original_page_text_sha256": old["text_sha256"],
                   "crop_sha256": receipt["crop_sha256"], "line_index": receipt["line_index"]}}
    temporary = package.parent / f".{package.name}.line-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, temporary)
        tables = json.loads((temporary / "tables.json").read_text())
        for table in tables:
            if table["page"] == number:
                table["page_text_sha256"] = digest
                revised["table_grid_proof"] = derive_table_grid_proof(revised, table, source)
        (temporary / "tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2) + "\n")
        _write_rows(temporary / "pages.jsonl", [revised if x["page"] == number else x for x in pages])
        embeddings = _rows(temporary / "embedding-input.jsonl")
        for row in embeddings:
            if row["metadata"]["page"] == number:
                row["text"] = text
                row["metadata"]["page_text_sha256"] = digest
        _write_rows(temporary / "embedding-input.jsonl", embeddings)
        facts = [x for x in _rows(temporary / "fact-candidates.jsonl") if x["page"] != number]
        for match in FACT.finditer(text):
            value = match.group().strip()
            kind = ("currency" if re.match(r"₹|INR|Rs\.?", value, re.I) else
                    "percentage" if "%" in value else "capacity" if re.search(r"\b[mk]w", value, re.I) else
                    "duration" if re.search(r"\b(?:days?|months?|years?)\b", value, re.I) else "date")
            facts.append({"page": number, "page_id": revised["id"], "page_text_sha256": digest,
                          "char_start": match.start(), "char_length": match.end()-match.start(),
                          "kind": kind, "value_text": value,
                          "context": re.sub(r"\s+", " ", text[max(0,match.start()-90):match.end()+90]).strip(),
                          "status": "unverified_candidate"})
        facts.sort(key=lambda x: (x["page"], x["char_start"]))
        _write_rows(temporary / "fact-candidates.jsonl", facts)
        structures = [x for x in _rows(temporary / "structure-candidates.jsonl")
                      if not any(e.get("page") == number for e in x.get("evidence", []))]
        structures.extend(page_candidates(revised))
        for table in tables:
            if table["page"] == number:
                structures.extend(table_candidates(table, revised))
        _write_rows(temporary / "structure-candidates.jsonl", structures)
        report = json.loads((temporary / "report.json").read_text())
        report.update(text_chars=sum(x["chars"] for x in pages)-old["chars"]+len(text),
                      numeric_candidates=len(facts), structure_candidates=len(structures))
        report.setdefault("issues", []).append({"page": number, "kind": "source_bound_line_correction",
            "old_text_sha256": old["text_sha256"], "page_text_sha256": digest})
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        create_package(temporary)
        verify_package(temporary, source_pdf=source)
        quality = assess_package(temporary, source, pages=[number],
            independent_by_page={number: independent}, derive_ocr_source_raster=True)
        quality_output.parent.mkdir(parents=True, exist_ok=True)
        quality_output.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n")
        if quality["results"][0]["outcome"] != "auto_accepted":
            raise ValueError("source line correction failed full-page gate")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"page": number, "old_text_sha256": old["text_sha256"],
                "new_text_sha256": digest, "quality_gate": "auto_accepted"}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
