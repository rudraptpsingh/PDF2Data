"""Local PDF extraction with explicit page, table, and review provenance.

Numbers are *candidates*, never verified legal facts. Tables preserve blank
cells instead of guessing inherited labels or values.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

import hashlib
import json
from pathlib import Path
import re
from collections import Counter
from contextlib import ExitStack, contextmanager
from tempfile import TemporaryDirectory
from typing import Callable
import unicodedata
from .structure_candidates import document_profiles

EXTRACTOR_CONTRACT='local-vector-batch.pdf-evidence.v2.visual-regions'
_EXTRACTOR_IMPORT_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def extractor_identity(policy=None):
    from .quality_gate import load_policy
    from .native_visuals import NATIVE_VISUALS_IMPLEMENTATION_SHA256
    policy=load_policy() if policy is None else policy
    return dict(extractor_contract=EXTRACTOR_CONTRACT,extractor_implementation_sha256=_EXTRACTOR_IMPORT_SHA256,
        native_visuals_implementation_sha256=NATIVE_VISUALS_IMPLEMENTATION_SHA256,
        quality_policy_sha256=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest())


_QUANTITIES = document_profiles()["quantity_lexicon"]
_MONEY_SCALE = "|".join(re.escape(value) for value in sorted(_QUANTITIES["money_scales"], key=len, reverse=True))
_MONEY_DENOMINATOR = "|".join(re.escape(value) for value in sorted(_QUANTITIES["money_denominators"], key=len, reverse=True))
_MONEY = (r"(?:₹|INR|Rs\.?)\s*[\d,]+(?:\.\d+)?"
          rf"(?:\s*(?:{_MONEY_SCALE})(?=\s*(?:per|/)|\b))?"
          rf"(?:\s*(?:per|/)\s*(?:{_MONEY_DENOMINATOR})(?=\s*(?:per|/)|\b))*")
FACT = re.compile(
    rf"(?<!\w)(?:{_MONEY}|\b[\d,]+(?:\.\d+)?\s*(?:%|(?:MW(?:h)?|kW(?:h)?|days?|months?|years?)\b)|"
    r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)
CID = re.compile(r"\(cid:\d+\)")
PARSER_GLYPH = re.compile(r"\(cid:\d+\)|/g\d{4,6}", re.IGNORECASE)
BLOCKING_ISSUES = frozenset({"no_searchable_text", "missing_text", "garbled_text",
                             "low_confidence_ocr", "ocr_failed", "text_extraction_failed",
                             "native_parser_disagreement", "secondary_parser_failed",
                             "native_raster_content_unverified"})


def independent_text_coverage(selected: str, reference: str) -> dict:
    """Compare independent PDF parsers without treating line wraps as lost text.

    PDF engines disagree about hyphens and token boundaries. Character counts
    catch missing letters/digits while retaining each engine's original text
    for page and table review. This does not validate reading order or rows.
    """
    # Engines use different numeric glyph IDs for undecodable font characters.
    # Those IDs are parser artifacts, not legal digits or missing source text.
    selected = PARSER_GLYPH.sub("", selected)
    reference = PARSER_GLYPH.sub("", reference)
    def meaningful_chars(value: str) -> Counter:
        normalized = unicodedata.normalize("NFC", value.casefold())
        return Counter(char for char in normalized if unicodedata.category(char)[0] in "LNM")
    selected_chars = meaningful_chars(selected)
    reference_chars = meaningful_chars(reference)
    total = sum(reference_chars.values())
    found = sum((reference_chars & selected_chars).values())
    reference_digits = Counter(char for char in reference if char.isdigit())
    selected_digits = Counter(char for char in selected if char.isdigit())
    digits = sum(reference_digits.values())
    return {"reference_chars": total, "found_chars": found,
            "char_recall": round(found / total, 4) if total else None,
            "reference_digits": digits,
            "digit_recall": round(sum((reference_digits & selected_digits).values()) / digits, 4) if digits else None}


def text_quality(text: str) -> dict:
    """Detect undecodable native glyph markers; this is a review signal."""
    cid = len(CID.findall(text))
    remainder = "".join(CID.sub("", text).split())
    share = cid / max(1, cid + len(remainder))
    replacements = text.count("\ufffd")
    nul = text.count("\x00")
    has_devanagari = any("\u0900" <= char <= "\u097f" for char in text)
    garbled = ((cid >= 5 and share > .01) or (cid > 0 and has_devanagari) or
               (replacements >= 5 and replacements / max(1, len(text)) > .02) or nul > 0)
    from .native_tables import glyph_audit
    audit = glyph_audit(text)
    garbled = garbled or bool(audit["issues"])
    return {"glyph_audit": audit, "cid_glyphs": cid, "cid_share": round(share, 4), "replacement_chars": replacements,
            "nul_chars": nul, "needs_ocr": garbled}


def short_native_page_evidence(page, native_text: str, independent_text: str) -> dict:
    """Check whether a short native page's visible ink is accounted for.

    A short footer/cover is not an extraction failure solely because it has
    fewer than 80 characters. This deliberately releases only text-only pages
    where the independent parser has every letter/digit and the original
    72-DPI raster has no dark pixels outside native character boxes. Images,
    vector outlines and uncertain renders remain review-required.
    """
    result = {"method": "independent_native_text_and_source_raster", "ready": False}
    if not native_text or not independent_text:
        return {**result, "reason": "missing_native_or_independent_text"}
    coverage = independent_text_coverage(native_text, independent_text)
    reverse = independent_text_coverage(independent_text, native_text)
    result["independent_coverage"] = coverage
    result["native_coverage_by_independent"] = reverse
    if (coverage["char_recall"] != 1 or coverage["digit_recall"] not in {None, 1} or
            reverse["char_recall"] != 1 or reverse["digit_recall"] not in {None, 1}):
        return {**result, "reason": "independent_text_disagrees"}
    object_counts = {kind: len(getattr(page, kind, []) or []) for kind in
                     ("images", "curves", "rects", "lines")}
    result["nontext_objects"] = object_counts
    if any(object_counts.values()):
        return {**result, "reason": "nontext_objects_require_review"}
    chars = getattr(page, "chars", []) or []
    if not chars:
        return {**result, "reason": "missing_character_geometry"}
    glyph_text = "".join(str(char.get("text", "")) for char in chars)
    glyph_coverage = independent_text_coverage(native_text, glyph_text)
    glyph_reverse = independent_text_coverage(glyph_text, native_text)
    result["native_glyph_coverage"] = glyph_coverage
    if (glyph_coverage["char_recall"] != 1 or glyph_coverage["digit_recall"] not in {None, 1} or
            glyph_reverse["char_recall"] != 1 or glyph_reverse["digit_recall"] not in {None, 1}):
        return {**result, "reason": "native_text_omits_pdf_character"}
    try:
        import numpy as np
        image = page.to_image(resolution=72).original.convert("L")
        pixels = np.asarray(image)
        ink = pixels < 200
        covered = np.zeros(ink.shape, dtype=bool)
        for char in chars:
            left = max(0, int(char["x0"]) - 2)
            right = min(image.width, int(char["x1"]) + 3)
            top = max(0, int(char["top"]) - 2)
            bottom = min(image.height, int(char["bottom"]) + 3)
            covered[top:bottom, left:right] = True
        unaccounted = int((ink & ~covered).sum())
        result.update(render_dpi=72, ink_pixels=int(ink.sum()),
                      unaccounted_ink_pixels=unaccounted)
        if unaccounted:
            return {**result, "reason": "visible_ink_outside_native_text"}
    except Exception as error:
        return {**result, "reason": "source_render_failed", "error": str(error)[:180]}
    return {**result, "ready": True, "reason": "native_text_accounts_for_source_ink"}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _open_pdf_with_page_recovery(source: Path, secondary_pages, issues: list[dict]):
    """Recover a malformed page tree without changing the source identity.

    Some scanned PDFs have real pages that pdfminer/pdfplumber reports as zero
    while pypdf and PDF viewers can traverse them. Rebuilding the page tree
    into a temporary file exposes those pages to native layout/OCR. The
    original PDF bytes and SHA remain the evidence source.
    """
    import pdfplumber
    with ExitStack() as stack:
        pdf = stack.enter_context(pdfplumber.open(source))
        if not pdf.pages and secondary_pages is not None and len(secondary_pages):
            from pypdf import PdfWriter
            temporary = Path(stack.enter_context(TemporaryDirectory())) / "page-tree-rebuilt.pdf"
            writer = PdfWriter()
            for page in secondary_pages:
                writer.add_page(page)
            writer.write(temporary)
            recovered = stack.enter_context(pdfplumber.open(temporary))
            if recovered.pages:
                pdf = recovered
                issues.append({"page": 0, "kind": "source_page_tree_recovered",
                               "detail": "Temporary page-tree rebuild exposed pages hidden from pdfplumber; original PDF SHA retained",
                               "recovered_pages": len(recovered.pages)})
        yield pdf


def extract_pdf(
    source: Path, output: Path, *, title: str | None = None, source_url: str | None = None,
    tables: bool = True, ocr: str = "auto", ocr_language: str = "eng", ocr_device: str = "auto",
    ocr_dpi: int = 180, native_consensus: bool = False,
    ocr_use_angle_classifier: bool | None = None,
    ocr_cpu_threads: int = 2,
    repair_pages: set[int] | None = None, reuse_package: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Extract all pages locally and write reviewable JSONL/JSON artifacts.

    `complete_for_embedding` is false when a nonblank page remains unreadable,
    OCR confidence is low, or extraction fails. Short nonempty pages survive.
    """
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("PDF support is missing; install local-vector-batch[pdf]") from error
    source = Path(source)
    output = Path(output)
    if not source.is_file():
        raise ValueError("input must be a readable PDF file")
    with source.open("rb") as stream:
        signature = stream.read(5)
    if signature != b"%PDF-":
        raise ValueError("input must be a readable PDF file")
    output.mkdir(parents=True, exist_ok=True)
    sha = _file_sha(source)
    document_id = f"pdf-{sha[:24]}"
    label = (title or source.stem).strip()[:240]
    if not label:
        raise ValueError("document title must not be empty")
    if source_url and not re.match(r"^https?://[^\s]+$", source_url):
        raise ValueError("source URL must be HTTP or HTTPS")
    if (repair_pages is None) != (reuse_package is None):
        raise ValueError("targeted repair requires both repair_pages and reuse_package")
    issues: list[dict] = []
    old_pages: dict[int, dict] = {}
    old_inputs: dict[int, list[dict]] = {}
    old_tables: dict[int, list[dict]] = {}
    old_facts: dict[int, list[dict]] = {}
    old_structures: dict[int, list[dict]] = {}
    old_issues: dict[int, list[dict]] = {}
    if reuse_package is not None:
        from .evidence_package import verify_package
        reuse_package = Path(reuse_package)
        if reuse_package.resolve() == output.resolve():
            raise ValueError("targeted repair output must differ from the verified baseline")
        verify_package(reuse_package, source_pdf=source)
        prior = json.loads((reuse_package / "report.json").read_text(encoding="utf-8"))
        if (prior["document_id"], prior["source_pdf_sha256"], prior["title"], prior["source_url"]) != \
                (document_id, sha, label, source_url):
            raise ValueError("targeted repair source identity differs from verified baseline")
        def existing_rows(name: str) -> list[dict]:
            return [json.loads(line) for line in jsonl_physical_lines((reuse_package / name).read_text(encoding="utf-8"))
                    if line.strip(' \t\r')]
        old_pages = {row["page"]: row for row in existing_rows("pages.jsonl")}
        table_page = {}
        for row in json.loads((reuse_package / "tables.json").read_text(encoding="utf-8")):
            old_tables.setdefault(row["page"], []).append(row)
            table_page[row["id"]] = row["page"]
        for name, target, page_key in (
            ("embedding-input.jsonl", old_inputs, "metadata"),
            ("fact-candidates.jsonl", old_facts, "page"),
            ("structure-candidates.jsonl", old_structures, "page"),
        ):
            for row in existing_rows(name):
                number = row["metadata"]["page"] if page_key == "metadata" else row.get("page")
                if page_key == "page" and number is None:
                    number = row["evidence"][0]["page"] if row.get("evidence") else \
                        table_page[row["table_id"]]
                target.setdefault(number, []).append(row)
        for row in prior["issues"]:
            old_issues.setdefault(row["page"], []).append(row)
        issues.extend(old_issues.get(0, []))
        if not repair_pages or any(type(page) is not int or page not in old_pages for page in repair_pages):
            raise ValueError("repair_pages must name existing physical pages")
    pages: list[dict] = []
    input_rows: list[dict] = []
    table_rows: list[dict] = []
    fact_rows: list[dict] = []
    structure_rows: list[dict] = []
    ocr_eligible_pages = 0
    ocr_attempted_pages = 0
    secondary_pages = None
    try:
        from pypdf import PdfReader
        secondary_pages = PdfReader(source).pages
    except Exception as error:
        issues.append({"page": 0, "kind": "secondary_parser_unavailable", "detail": str(error)[:180]})
    from .ocr import LocalOCR
    from .quality_gate import load_policy
    from .native_visuals import native_raster_regions, associate_table_geometry
    quality_policy=load_policy()
    angle_options = ({'use_angle_classifier':ocr_use_angle_classifier}
                     if ocr_use_angle_classifier is not None else {})
    if ocr_cpu_threads!=2:angle_options['cpu_threads']=ocr_cpu_threads
    with LocalOCR(ocr, ocr_language, ocr_device, ocr_dpi, **angle_options) as recognizer, _open_pdf_with_page_recovery(source, secondary_pages, issues) as pdf:
        total = len(pdf.pages)
        if total == 0:
            raise ValueError("PDF has no pages")
        if old_pages and total != len(old_pages):
            raise ValueError("source PDF physical page count changed from verified baseline")
        for index, page in enumerate(pdf.pages, 1):
            if old_pages and index not in repair_pages:
                pages.append(old_pages[index])
                input_rows.extend(old_inputs.get(index, []))
                table_rows.extend(old_tables.get(index, []))
                fact_rows.extend(old_facts.get(index, []))
                structure_rows.extend(old_structures.get(index, []))
                issues.extend(old_issues.get(index, []))
                if progress:
                    progress(index, total)
                continue
            native_error = None
            try:
                text = (page.extract_text() or "").replace("\r\n", "\n").strip()
            except Exception as error:
                text = ""
                native_error = str(error)[:180]
            native = text
            native_quality = text_quality(native)
            independent_coverage = None
            secondary_text = ""
            parser_gap = False
            if secondary_pages is not None and index <= len(secondary_pages):
                try:
                    secondary_text = (secondary_pages[index - 1].extract_text() or "").strip()
                    if native and (len(secondary_text) >= 80 or
                                   (len(native) < 80 and secondary_text)):
                        independent_coverage = independent_text_coverage(native, secondary_text)
                        parser_gap = ((independent_coverage["char_recall"] is not None and
                                       independent_coverage["char_recall"] < .995) or
                                      (independent_coverage["digit_recall"] is not None and
                                       independent_coverage["digit_recall"] < 1))
                except Exception as error:
                    issues.append({"page": index, "kind": "secondary_parser_failed", "detail": str(error)[:180]})
            try:
                blank = not text and not any(getattr(page, "objects", {"unknown": [1]}).values())
            except Exception:
                blank = False
            ocr_result = None
            selected_method = "pdf_text"
            # Short native text can be only a digitally printed footer while the
            # substantive page is a scanned/form XObject that `page.images` does
            # not expose. Render and OCR every visibly nonblank short page.
            if native_quality["needs_ocr"] or parser_gap or (not blank and len(text) < 80):
                ocr_eligible_pages += 1
                try:
                    if recognizer.engine != "off":
                        ocr_attempted_pages += 1
                    ocr_result = recognizer.recognize(page)
                    if ocr_result and ocr_result["text"].strip() and (
                        not native or native_quality["needs_ocr"] or
                        (parser_gap and ocr_result["mean_confidence"] >= .8 and
                         not text_quality(ocr_result["text"])["needs_ocr"]) or
                        (len(ocr_result["text"]) > len(native) and ocr_result["mean_confidence"] >= .75)
                    ):
                        text = ocr_result["text"]
                        selected_method = ocr_result["engine"]
                        issues.append({"page": index, "kind": "ocr_used", "engine": ocr_result["engine"],
                                       "confidence": round(ocr_result["mean_confidence"], 3)})
                        if ocr_result["mean_confidence"] < .75:
                            issues.append({"page": index, "kind": "low_confidence_ocr"})
                except Exception as error:
                    issues.append({"page": index, "kind": "ocr_failed", "detail": str(error)[:180]})
            native_line_repairs = []
            if native_consensus and selected_method == "pdf_text" and secondary_pages is not None:
                try:
                    from .native_consensus import repair_source_lines
                    layout_reference = secondary_pages[index-1].extract_text(extraction_mode="layout") or ""
                    text, native_line_repairs = repair_source_lines(source, page, index, text, layout_reference)
                except Exception as error:
                    issues.append({"page": index, "kind": "native_consensus_unavailable", "detail": str(error)[:180]})
            selected_quality = text_quality(text)
            short_evidence = (short_native_page_evidence(page, text, secondary_text)
                              if selected_method == "pdf_text" and 0 < len(text) < 80 else None)
            if parser_gap:
                issues.append({"page": index,
                               "kind": "native_parser_disagreement" if selected_method == "pdf_text" else
                                       "native_parser_disagreement_recovered_by_ocr",
                               "detail": "Independent extraction found letters or digits absent from native text",
                               **independent_coverage})
            if independent_coverage is None and len(native) >= 80:
                issues.append({"page": index, "kind": "secondary_parser_inconclusive",
                               "detail": "Independent page text was unavailable or too short to audit native coverage"})
            if selected_quality["needs_ocr"]:
                issues.append({"page": index, "kind": "garbled_text", "detail": "Encoded or replacement glyphs remain unresolved"})
            if native_error:
                issues.append({"page": index, "kind": "native_extraction_recovered" if text else "text_extraction_failed",
                               "detail": native_error})
            page_id = f"{document_id}:p{index}"
            text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            from .text_script import classify_text_script, native_script_verification
            script_verification = native_script_verification(text, quality_policy)
            raster_regions=native_raster_regions(page,minimum_page_fraction=quality_policy.get('native_raster_region_min_page_fraction',0.0))
            if raster_regions:
                issues.append({'page':index,'kind':'native_raster_content_unverified',
                    'region_count':len(raster_regions),
                    'detail':'Native text does not establish recovery of raster image content; retain original regions and require source/reading-order/table review'})
            if selected_method == 'pdf_text' and script_verification['required_scripts']:
                issues.append({'page': index, 'kind': 'native_complex_script_glyph_mapping_unverified',
                               'required_scripts': script_verification['required_scripts'],
                               'detail': 'Native Unicode and parser agreement do not verify font glyph mapping; independent raster/language proof required'})
            pages.append({"id": page_id, "page": index, "text": text, "chars": len(text),
                          "method": selected_method, "text_sha256": text_sha,
                          **classify_text_script(text),
                          "script_verification": script_verification,
                          "native_raster_regions":raster_regions,
                          "pdf_width": round(float(page.width), 2) if getattr(page, "width", None) else None,
                          "pdf_height": round(float(page.height), 2) if getattr(page, "height", None) else None,
                          "native_quality": native_quality, "selected_quality": selected_quality,
                          "independent_coverage": independent_coverage,
                          **({"short_source_evidence": short_evidence} if short_evidence else {}),
                          **({"secondary_native_text": secondary_text} if parser_gap else {}),
                          **({"native_text": native} if ocr_result is not None or native_line_repairs else {}),
                          **({"native_line_repairs": native_line_repairs} if native_line_repairs else {}),
                          **({"ocr": ocr_result} if ocr_result else {})})
            from .structure_candidates import page_candidates, table_candidates
            structure_rows.extend(page_candidates(pages[-1]))
            if len(text) < 80:
                kind = "blank_page" if blank else "missing_text" if not text else \
                    "short_text_verified" if short_evidence and short_evidence["ready"] else "short_text"
                issues.append({"page": index, "kind": kind, "chars": len(text),
                               "detail": "Short native text matches independent parser and accounts for all visible ink"
                               if kind == "short_text_verified" else
                               "Review scanned, image-only, or intentionally blank page before embedding"})
            if text:
                metadata = {"parent_document_id": document_id, "page": index,
                            "source_pdf_sha256": sha, "page_text_sha256": text_sha, "source_title": label}
                if source_url:
                    metadata["url"] = source_url
                input_rows.append({"id": page_id, "text": text, "metadata": metadata})
                for match in FACT.finditer(text):
                    value = match.group().strip()
                    kind = ("currency" if re.match(r"₹|INR|Rs\.?", value, re.I) else "percentage" if "%" in value else
                            "capacity" if re.search(r"\b[mk]w", value, re.I) else
                            "duration" if re.search(r"\b(?:days?|months?|years?)\b", value, re.I) else "date")
                    fact_rows.append({"page": index, "page_id": page_id, "page_text_sha256": text_sha,
                                      "char_start": match.start(), "char_length": match.end() - match.start(),
                                      "kind": kind, "value_text": value,
                                      "context": re.sub(r"\s+", " ", text[max(0, match.start()-90):match.end()+90]).strip(),
                                      "status": "unverified_candidate"})
            if tables:
                native_tables_on_page = 0
                try:
                    from .native_tables import find_native_tables, table_shape
                    for number, found in enumerate(find_native_tables(page), 1):
                        cells = found.extract()
                        if not cells:
                            continue
                        table_rows.append({"id": f"{page_id}:table{number}", "page": index,
                                           "bbox": [round(float(x), 2) for x in found.bbox],
                                           "bbox_coordinates": "PDF points, top-left",
                                           "extraction_basis": "native_pdf_geometry",
                                           "cells": cells, "status": "needs_row_column_review",
                                           **table_shape(found, cells, f"{page_id}:table{number}")})
                        native_tables_on_page += 1
                        structure_rows.extend(table_candidates(table_rows[-1], pages[-1]))
                except Exception as error:
                    issues.append({"page": index, "kind": "table_extraction_failed", "detail": str(error)[:180]})
                if selected_method in {"vision", "tesseract", "rapidocr"} and ocr_result and not native_tables_on_page:
                    try:
                        from .ocr_tables import ruled_table_candidates
                        rendered = (getattr(recognizer, "last_rendered_image", None) or
                                    page.to_image(resolution=ocr_result["render_dpi"]).original)
                        for candidate in ruled_table_candidates(
                            rendered, ocr_result["lines"], page=index, page_id=page_id,
                            pdf_width=float(page.width), pdf_height=float(page.height),
                        ):
                            candidate["source_pdf_sha256"] = sha
                            candidate["page_text_sha256"] = text_sha
                            candidate["ocr_engine"] = ocr_result["engine"]
                            candidate["render_dpi"] = ocr_result["render_dpi"]
                            table_rows.append(candidate)
                            structure_rows.extend(table_candidates(candidate, pages[-1]))
                            issues.append({"page": index, "kind": "ocr_ruled_table_candidate",
                                           "table_id": candidate["id"],
                                           "detail": "OCR word boxes assigned to printed grid; row and column values require source review"})
                    except Exception as error:
                        issues.append({"page": index, "kind": "ocr_table_candidate_failed", "detail": str(error)[:180]})
                if selected_method != "pdf_text":
                    issues.append({"page": index, "kind": "ocr_layout_review",
                                   "detail": "OCR text does not reconstruct scanned table rows or merged cells"})
                page_tables=[t for t in table_rows if t['page']==index]
                table_geometry=associate_table_geometry(page_tables,tolerance=quality_policy.get('table_geometry_containment_tolerance_points',0.25))
                pages[-1]['table_geometry_summary']=table_geometry
                if table_geometry['contained_candidates'] or table_geometry['equal_geometry_candidates']:
                    issues.append({'page':index,'kind':'nested_table_geometry_candidates',**table_geometry,
                        'detail':'Raw table candidates retained; contained/equal geometry is not proof of independent tables or permission to discard them'})
            recognizer.last_rendered_image = None
            page_issue_kinds = {item["kind"] for item in issues if item["page"] == index}
            if blank and not text:
                pages[-1]["status"] = "blank"
            elif (selected_method != "pdf_text" or
                  (len(text) < 80 and not (short_evidence and short_evidence["ready"])) or
                  independent_coverage is None or
                  page_issue_kinds & {"garbled_text", "native_parser_disagreement", "secondary_parser_failed",
                                      "text_extraction_failed", "native_extraction_recovered", "ocr_failed",
                                      "low_confidence_ocr", "table_extraction_failed", "missing_text",
                                      "native_complex_script_glyph_mapping_unverified", "native_raster_content_unverified",
                                      "nested_table_geometry_candidates"}):
                pages[-1]["status"] = "review_required"
            else:
                pages[-1]["status"] = "ready"
            if progress:
                progress(index, total)
    if not input_rows:
        issues.append({"page": 1, "kind": "no_searchable_text", "detail": "No page produced searchable text"})
    page_status_counts = {status: sum(row["status"] == status for row in pages)
                          for status in ("ready", "blank", "review_required")}
    report = {"schema_version": 1, "document_id": document_id, "title": label,
              "source_url": source_url, "source_pdf_sha256": sha, "pages": len(pages),
              "targeted_repair_pages": sorted(repair_pages) if repair_pages is not None else [],
              "reused_page_count": len(pages) - len(repair_pages) if repair_pages is not None else 0,
              "ocr_compute": {"engine": recognizer.engine, "device_requested": recognizer.device_requested,
                              "language_requested": recognizer.language,
                              "recognition_languages": list(recognizer.recognition_languages)
                              if recognizer.engine in {"vision", "rapidocr"} else [recognizer.language],
                              "recognition_model": (f"{recognizer.rapidocr_model}_PP-OCRv5_rec_mobile"
                                                    if recognizer.engine == "rapidocr" else None),
                              "device_used": "mixed_vision_system_managed_and_rapidocr_cpu"
                              if recognizer.fallback_pages else recognizer.device_used,
                              "rapidocr_fallback_pages": recognizer.fallback_pages,
                              "pages_eligible": ocr_eligible_pages,
                              "pages_attempted": ocr_attempted_pages,
                              "queue_wait_ms": sum((row.get("ocr") or {}).get("queue_wait_ms", 0) for row in pages),
                              "execution_ms": sum((row.get("ocr") or {}).get("execution_ms", 0) for row in pages)},
              "page_status_counts": page_status_counts,
              "text_chars": sum(row["chars"] for row in pages), "tables": len(table_rows),
              "native_raster_regions":sum(len(p.get('native_raster_regions',[])) for p in pages),
              "maximal_table_geometry_candidates":sum(p.get('table_geometry_summary',{}).get('maximal_geometry_candidates',0) for p in pages),
              "table_count_semantics":"tables counts raw candidates; maximal geometry is not a certified semantic-table count",
              **extractor_identity(quality_policy),
              "visual_completeness":"not_certified; separate original-raster quality gate required even when no image object is exposed",
              "ocr_table_candidates": sum(row["extraction_basis"] == "ocr_ruling_geometry_word_boxes"
                                          for row in table_rows),
              "numeric_candidates": len(fact_rows), "issues": issues,
              "structure_candidates": len(structure_rows),
              "structure_candidates_status": "review_required_no_auto_verified_claims",
              "structured_facts_status": "unverified_candidates_only",
              "requires_source_review": page_status_counts["review_required"] > 0 or bool(table_rows),
              "recommended_min_chars": 1,
              "complete_for_embedding": not any(item["kind"] in BLOCKING_ISSUES for item in issues),
              "review_note": "Numeric, role, clause and table candidates require source review; no legal fact or citation is inferred."}
    _write_json(output / "report.json", report)
    _jsonl(output / "pages.jsonl", pages)
    _jsonl(output / "embedding-input.jsonl", input_rows)
    _write_json(output / "tables.json", table_rows)
    _jsonl(output / "fact-candidates.jsonl", fact_rows)
    _jsonl(output / "structure-candidates.jsonl", structure_rows)
    from .evidence_package import create_package
    create_package(output)
    return report
