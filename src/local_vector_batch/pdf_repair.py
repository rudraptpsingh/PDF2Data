"""Source-bound, page-targeted OCR repair of an existing evidence package."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import shutil
import uuid

from .evidence_package import verify_package
from .pdf_ingest import extract_pdf


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _page_rows(package: Path, name: str) -> dict[int, list[dict]]:
    rows = _rows(package / name) if name != "tables.json" else json.loads((package / name).read_text())
    tables = {row["id"]: row["page"] for row in json.loads((package / "tables.json").read_text())}
    grouped = defaultdict(list)
    for row in rows:
        if name == "embedding-input.jsonl":
            page = row["metadata"]["page"]
        elif name == "structure-candidates.jsonl":
            page = row["evidence"][0]["page"] if row.get("evidence") else tables[row["table_id"]]
        else:
            page = row["page"]
        grouped[page].append(row)
    return grouped


def repair_pdf_pages(source: Path, package: Path, pages: set[int], *,
                     ocr: str = "auto", ocr_language: str = "eng", ocr_dpi: int = 180,
                     tables: bool = True) -> dict:
    """Replace only requested physical-page evidence, preserving all other rows.

    The original PDF SHA and baseline evidence-package hashes are verified
    before work. The replacement package is verified and compared page by page
    before an atomic directory swap. OCR candidates retain review_required.
    """
    source, package = Path(source), Path(package)
    if not pages or any(type(page) is not int or page < 1 for page in pages):
        raise ValueError("pages must contain positive physical page numbers")
    verify_package(package, source_pdf=source)
    prior = json.loads((package / "report.json").read_text(encoding="utf-8"))
    if max(pages) > prior["pages"]:
        raise ValueError("targeted repair page exceeds original PDF page count")
    if ocr == "off":
        raise ValueError("targeted OCR repair needs an active OCR engine")
    temporary = package.parent / f".{package.name}.repair-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    names = ("pages.jsonl", "embedding-input.jsonl", "fact-candidates.jsonl",
             "structure-candidates.jsonl", "tables.json")
    try:
        report = extract_pdf(source, temporary, title=prior["title"],
                             source_url=prior["source_url"], tables=tables,
                             ocr=ocr, ocr_language=ocr_language, ocr_dpi=ocr_dpi,
                             repair_pages=pages, reuse_package=package)
        verify_package(temporary, source_pdf=source)
        for name in names:
            before, after = _page_rows(package, name), _page_rows(temporary, name)
            if any(before.get(page, []) != after.get(page, [])
                   for page in range(1, prior["pages"] + 1) if page not in pages):
                raise ValueError(f"targeted repair changed untouched {name} rows")
        old_pages = {row["page"]: row for row in _rows(package / "pages.jsonl")}
        new_pages = {row["page"]: row for row in _rows(temporary / "pages.jsonl")}
        for page in pages:
            if new_pages[page]["status"] == "ready" and new_pages[page]["method"] != "pdf_text":
                raise ValueError("unreviewed OCR page was marked ready")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"source_pdf_sha256": report["source_pdf_sha256"],
                "repaired_pages": sorted(pages), "reused_pages": report["reused_page_count"],
                "changed_pages": [page for page in sorted(pages)
                                  if old_pages[page]["text_sha256"] != new_pages[page]["text_sha256"]],
                "page_status_counts": report["page_status_counts"],
                "package": str(package)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def apply_column_order_repair(package: Path, receipt: dict) -> dict:
    """Atomically apply a freshly re-proven order-only, dual-reader OCR repair.

    Source-reviewed decisions are never overridden. Every original OCR line is
    preserved once; page facts and relations are rebuilt against the new SHA.
    The package remains review-only evidence; separate machine gates may clear
    extraction status without approving legal facts or creating human receipts.
    """
    from .column_order import derive_column_order_repair
    from .evidence_package import create_package
    from .pdf_ingest import FACT
    from .structure_candidates import page_candidates
    import re

    package = Path(package)
    source = Path(receipt["source_pdf_path"])
    verify_package(package, source_pdf=source)
    report = json.loads((package / "report.json").read_text())
    pages = _rows(package / "pages.jsonl")
    number = receipt["page"]
    old = next((page for page in pages if page["page"] == number), None)
    if (old is None or old.get("review_decision") is not None or
            receipt.get("ready") is not True or receipt.get("source_pdf_sha256") != report["source_pdf_sha256"] or
            old["text_sha256"] != receipt.get("original_page_text_sha256")):
        raise ValueError("column repair source, original page, or review state changed")
    tables = json.loads((package / "tables.json").read_text())
    fresh = derive_column_order_repair(old, receipt["original_independent_lines"],
        receipt["layout_regions"], source,
        independent_metadata=receipt["original_independent_metadata"],
        table_count=sum(table["page"] == number for table in tables))
    if (not fresh.get("ready") or fresh.get("quality_gate", {}).get("outcome") != "auto_accepted" or
            fresh["page_text_sha256"] != receipt.get("page_text_sha256") or
            fresh["proposed_page"]["text"] != receipt["proposed_page"]["text"] or
            fresh["proposed_page"]["ocr"]["lines"] != receipt["proposed_page"]["ocr"]["lines"]):
        raise ValueError("column repair fresh source, block consensus, or quality gates failed")
    revised = fresh["proposed_page"]
    revised["status"] = "review_required"
    # Preserve the immutable original stream in the proof, never call this a
    # source-reviewed replacement or promote the OCR candidate to native text.
    revised.pop("review_decision", None)
    temporary = package.parent / f".{package.name}.column-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"

    def write_rows(name, rows):
        (temporary / name).write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows))

    try:
        shutil.copytree(package, temporary)
        pages = [revised if page["page"] == number else page for page in pages]
        write_rows("pages.jsonl", pages)
        inputs = _rows(package / "embedding-input.jsonl")
        for row in inputs:
            if row["metadata"]["page"] == number:
                row["text"] = revised["text"]
                row["metadata"]["page_text_sha256"] = revised["text_sha256"]
        write_rows("embedding-input.jsonl", inputs)
        facts = [row for row in _rows(package / "fact-candidates.jsonl") if row["page"] != number]
        for match in FACT.finditer(revised["text"]):
            value = match.group().strip()
            kind = ("currency" if re.match(r"₹|INR|Rs\.?", value, re.I) else
                    "percentage" if "%" in value else "capacity" if re.search(r"\b[mk]w", value, re.I) else
                    "duration" if re.search(r"\b(?:days?|months?|years?)\b", value, re.I) else "date")
            facts.append({"page": number, "page_id": revised["id"], "page_text_sha256": revised["text_sha256"],
                          "char_start": match.start(), "char_length": match.end()-match.start(),
                          "kind": kind, "value_text": value,
                          "context": re.sub(r"\s+", " ", revised["text"][max(0,match.start()-90):match.end()+90]).strip(),
                          "status": "unverified_candidate"})
        facts.sort(key=lambda row: (row["page"], row["char_start"]))
        write_rows("fact-candidates.jsonl", facts)
        structures = [row for row in _rows(package / "structure-candidates.jsonl")
                      if not any(span.get("page") == number for span in row.get("evidence", []))]
        structures.extend(page_candidates(revised))
        write_rows("structure-candidates.jsonl", structures)
        report["page_status_counts"] = {status: sum(page["status"] == status for page in pages)
                                        for status in ("ready", "blank", "review_required")}
        report.update(text_chars=sum(page["chars"] for page in pages), numeric_candidates=len(facts),
                      structure_candidates=len(structures), requires_source_review=True)
        report.setdefault("issues", []).append({"page": number, "kind": "source_bound_column_order_repair",
            "detail": "Every original OCR line preserved once; dual-reader block tokens and source raster agree",
            "original_text_sha256": old["text_sha256"], "page_text_sha256": revised["text_sha256"]})
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
        create_package(temporary)
        verify_package(temporary, source_pdf=source)
        for name in ("pages.jsonl", "embedding-input.jsonl", "fact-candidates.jsonl", "structure-candidates.jsonl", "tables.json"):
            before, after = _page_rows(package, name), _page_rows(temporary, name)
            if any(before.get(page, []) != after.get(page, []) for page in range(1, report["pages"]+1) if page != number):
                raise ValueError(f"column repair changed untouched {name} rows")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"source_pdf_sha256": report["source_pdf_sha256"], "changed_pages": [number],
                "old_text_sha256": old["text_sha256"], "page_text_sha256": revised["text_sha256"],
                "quality_gate": fresh["quality_gate"], "package": str(package), "human_review_receipts_created": 0}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def apply_ocr_region_repair(package: Path, receipt: dict | list[dict]) -> dict:
    """Fill one or more unrecognized source regions after independent agreement.

    RapidOCR is rerun on the exact PDF crop; a distinct local vision-language
    reader must agree on the inserted token. The whole revised page then must
    pass every current machine gate against the cached independent full read.
    This operates on a staging package, never a watched corpus package.
    """
    from collections import Counter
    from hashlib import sha256
    from io import BytesIO
    import re
    import pypdfium2 as pdfium
    from .evidence_package import create_package
    from .ocr import _rapidocr_lines, vision_crop_lines
    from .region_ocr import agreed_label_literal, load_region_policy, short_label_crop
    from .pdf_ingest import FACT, text_quality
    from .quality_gate import assess_package
    from .structure_candidates import page_candidates
    from .repair_pipeline import _bound_independent_text

    receipts = receipt if isinstance(receipt, list) else [receipt]
    if not receipts:
        raise ValueError("OCR region repair requires at least one receipt")
    receipt = receipts[0]
    package = Path(package)
    source = Path(receipt["source_pdf"])
    verify_package(package, source_pdf=source)
    report = json.loads((package / "report.json").read_text())
    number = receipt["page"]
    pages = _rows(package / "pages.jsonl")
    old = next((row for row in pages if row["page"] == number), None)
    if (old is None or old.get("review_decision") or old.get("method") not in {"vision", "rapidocr"} or
            old["status"] != "review_required" or receipt["source_pdf_sha256"] != report["source_pdf_sha256"] or
            old["text_sha256"] != receipt["original_page_text_sha256"]):
        raise ValueError("OCR region repair original page or source identity changed")
    if any((item["source_pdf"] != receipt["source_pdf"] or item["source_pdf_sha256"] != report["source_pdf_sha256"] or
            item["page"] != number or item["original_page_text_sha256"] != old["text_sha256"] or
            item["render_dpi"] != receipt["render_dpi"] or
            item["independent_original_text_sha256"] != receipt["independent_original_text_sha256"] or
            item["independent_full_receipt"] != receipt["independent_full_receipt"]) for item in receipts):
        raise ValueError("OCR region receipts disagree on original physical page or independent reader")
    if len({item["crop_image_sha256"] for item in receipts}) != len(receipts):
        raise ValueError("OCR region receipts contain a duplicate source crop")
    if any(table["page"] == number for table in json.loads((package / "tables.json").read_text())):
        raise ValueError("OCR region repair requires separate table cell proof")
    dpi = receipt["render_dpi"]
    if type(dpi) is not int or not 120 <= dpi <= 360:
        raise ValueError("OCR crop DPI outside 120..360")
    document = pdfium.PdfDocument(str(source))
    try:
        source_page = document[number - 1]
        try:
            rendered = source_page.render(scale=dpi / 72).to_pil()
        finally:
            source_page.close()
    finally:
        document.close()
    original_lines = old["ocr"]["lines"]
    patches = []
    from .ocr import _rapidocr_model_sha256
    region_policy, policy_sha = load_region_policy()
    for item in receipts:
        box = item["crop_bbox_pixels"]
        if (len(box) != 4 or any(type(x) is not int for x in box) or
                not (0 <= box[0] < box[2] <= rendered.width and 0 <= box[1] < box[3] <= rendered.height)):
            raise ValueError("OCR crop coordinates outside source page")
        crop = rendered.crop(tuple(box))
        image = BytesIO()
        crop.save(image, format="PNG")
        if sha256(image.getvalue()).hexdigest() != item["crop_image_sha256"]:
            raise ValueError("OCR crop does not match source PDF render")
        if (not item.get("vlm_model") or not item.get("vlm_revision") or
                item.get("vlm_engine") == "rapidocr_onnx" or
                item.get("vlm_crop_image_sha256") != item["crop_image_sha256"]):
            raise ValueError("independent VLM model/revision required")
        vlm_text = item["vlm_text"]
        if sha256(vlm_text.encode()).hexdigest() != item["vlm_text_sha256"]:
            raise ValueError("independent VLM transcription SHA changed")
        if item.get("rapidocr_model_sha256") != _rapidocr_model_sha256("en"):
            raise ValueError("RapidOCR recognition model SHA changed")
        lines_result = _rapidocr_lines(crop, "en")
        if lines_result is None:
            raise ValueError("RapidOCR EN engine unavailable")
        recognized, _ = lines_result
        wanted = item["rapidocr_line"]
        if wanted not in recognized:
            raise ValueError("RapidOCR crop result changed or low confidence")
        token = wanted["text"].strip()
        third = item.get("third_reader")
        if wanted["confidence"] < region_policy["two_reader_min_rapid_confidence"]:
            if (item.get("region_policy_sha256") != policy_sha or
                    wanted["confidence"] < region_policy["three_reader_min_rapid_confidence"] or
                    not re.fullmatch(region_policy["short_label_pattern"], token) or
                    any(char.isdigit() for char in token) or
                    not isinstance(third, dict) or third.get("engine") != "macos_vision" or
                    third.get("crop_image_sha256") != item["crop_image_sha256"]):
                raise ValueError("low-confidence label lacks policy-bound third-reader proof")
            label_image, label_box = short_label_crop(crop, wanted)
            label_png = BytesIO()
            label_image.save(label_png, format="PNG")
            if (third.get("source_subcrop_bbox_pixels") != label_box or
                    third.get("source_subcrop_sha256") != sha256(label_png.getvalue()).hexdigest()):
                raise ValueError("third-reader source subcrop proof changed")
            third_lines = vision_crop_lines(label_image)
            if (third.get("line") not in third_lines or
                    third["line"]["text"].strip().casefold() != token.casefold() or
                    third["line"]["confidence"] < region_policy["three_reader_min_vision_confidence"]):
                raise ValueError("independent Vision crop proof changed or disagrees")
            literal = agreed_label_literal(token, third["line"]["text"], vlm_text)
            if literal is None or item.get("inserted_text") != literal:
                raise ValueError("three-reader literal case consensus changed")
            token = literal
        elif item.get("inserted_text", token) != token:
            raise ValueError("two-reader inserted text changed")
        if (not token or len(token) > 80 or re.search(r"\w", token) is None or
                re.search(r"(?<!\w)" + re.escape(token.casefold()) + r"(?!\w)", vlm_text.casefold()) is None):
            raise ValueError("independent crop readers disagree on inserted text")
        x0, y0, x1, y1 = wanted["bbox"]
        full_box = [(box[0] + x0 * (box[2]-box[0])) / rendered.width,
                    (box[1] + y0 * (box[3]-box[1])) / rendered.height,
                    (box[0] + x1 * (box[2]-box[0])) / rendered.width,
                    (box[1] + y1 * (box[3]-box[1])) / rendered.height]
        for line in [*original_lines, *patches]:
            a = line["bbox"]
            intersection = max(0, min(a[2], full_box[2])-max(a[0], full_box[0])) * \
                           max(0, min(a[3], full_box[3])-max(a[1], full_box[1]))
            area = max(1e-9, (full_box[2]-full_box[0])*(full_box[3]-full_box[1]))
            if intersection / area > .1:
                raise ValueError("OCR patch overlaps an already recognized or proposed line")
        words = []
        for word in wanted.get("words", []):
            wx0, wy0, wx1, wy1 = word["bbox"]
            words.append({**word, "bbox": [
                (box[0] + wx0 * (box[2]-box[0])) / rendered.width,
                (box[1] + wy0 * (box[3]-box[1])) / rendered.height,
                (box[0] + wx1 * (box[2]-box[0])) / rendered.width,
                (box[1] + wy1 * (box[3]-box[1])) / rendered.height]})
        patches.append({"text": token, "confidence": wanted["confidence"],
                        "bbox": full_box, "words": words})
    lines = list(original_lines)
    for patch_line in sorted(patches, key=lambda line: (line["bbox"][1], line["bbox"][0])):
        insertion = len(lines)
        for index, line in enumerate(lines):
            y = line["bbox"][1]
            if patch_line["bbox"][1] < y - .01 or (abs(patch_line["bbox"][1] - y) <= .01 and patch_line["bbox"][0] < line["bbox"][0]):
                insertion = index
                break
        lines.insert(insertion, patch_line)
    text = "\n".join(line["text"] for line in lines)
    if Counter(line["text"] for line in lines) != Counter(line["text"] for line in original_lines) + Counter(line["text"] for line in patches):
        raise ValueError("OCR patch changed existing lines")
    new_sha = sha256(text.encode()).hexdigest()
    revised = {**old, "text": text, "text_sha256": new_sha, "chars": len(text),
               "selected_quality": text_quality(text),
               "ocr": {**old["ocr"], "text": text, "lines": lines},
               "status": "review_required",
               "region_repair_proof": {"source_pdf_sha256": report["source_pdf_sha256"],
                   "page": number, "original_page_text_sha256": old["text_sha256"],
                   "regions": [{"crop_image_sha256": item["crop_image_sha256"],
                       "rapidocr_model_sha256": item["rapidocr_model_sha256"],
                       "vlm_model": item["vlm_model"], "vlm_revision": item["vlm_revision"],
                       "vlm_text_sha256": item["vlm_text_sha256"], "inserted_text": patch["text"]}
                       for item, patch in zip(receipts, patches)]}}
    temporary = package.parent / f".{package.name}.region-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, temporary)
        pages = [revised if row["page"] == number else row for row in pages]
        (temporary / "pages.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in pages))
        inputs = _rows(temporary / "embedding-input.jsonl")
        for row in inputs:
            if row["metadata"]["page"] == number:
                row["text"] = text
                row["metadata"]["page_text_sha256"] = new_sha
        (temporary / "embedding-input.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in inputs))
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
        (temporary / "fact-candidates.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in facts))
        structures = [row for row in _rows(temporary / "structure-candidates.jsonl")
                      if not any(span.get("page") == number for span in row.get("evidence", []))]
        structures.extend(page_candidates(revised))
        (temporary / "structure-candidates.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in structures))
        report["page_status_counts"] = {status: sum(row["status"] == status for row in pages)
                                        for status in ("ready", "blank", "review_required")}
        report.update(text_chars=sum(row["chars"] for row in pages), numeric_candidates=len(facts),
                      structure_candidates=len(structures), requires_source_review=True)
        report.setdefault("issues", []).append({"page": number, "kind": "source_bound_ocr_region_repair",
            "original_text_sha256": old["text_sha256"], "page_text_sha256": new_sha,
            "crop_image_sha256": [item["crop_image_sha256"] for item in receipts]})
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
        create_package(temporary)
        verify_package(temporary, source_pdf=source)
        independent = _bound_independent_text(receipt["independent_full_receipt"], source,
            source_sha=report["source_pdf_sha256"], page=number,
            original_text_sha=receipt["independent_original_text_sha256"],
            candidate_engine=old.get("ocr", {}).get("engine", old["method"]))
        gate = assess_package(temporary, source, pages=[number], independent_by_page={number: independent},
                              derive_ocr_source_raster=True)
        if gate["results"][0]["outcome"] != "auto_accepted":
            row = gate["results"][0]
            raise ValueError(f"repaired source region still fails gate: {row['failed_components']}; "
                             f"unresolved_regions={row.get('unresolved_visual_regions', [])[:3]}")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {"page": number, "source_pdf_sha256": report["source_pdf_sha256"],
                "old_text_sha256": old["text_sha256"], "new_text_sha256": new_sha,
                "inserted_regions": len(receipts),
                "quality_gate": gate["results"][0]["outcome"]}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
