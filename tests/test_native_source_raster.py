"""Real PDF glyphs and original rasters exercise native evidence generation."""
import hashlib
from io import BytesIO
import json

import pytest

pdfplumber = pytest.importorskip("pdfplumber")
pytest.importorskip("cv2")
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from local_vector_batch.quality_gate import assess_page, assess_package, native_source_raster_evidence


def _source(path, *, extra="", crop=False):
    writer = PdfWriter()
    pdf_page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    pdf_page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 14 Tf 60 320 Td (The charge is 51% for 12 years.) Tj ET\n"+extra).encode())
    pdf_page[NameObject("/Contents")] = writer._add_object(stream)
    if crop:
        pdf_page.cropbox.lower_left = (40, 40)
        pdf_page.cropbox.upper_right = (360, 360)
    writer.write(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(text):
    return {"page": 1, "method": "pdf_text", "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def test_native_real_glyph_coverage_preserves_source_render_hash_and_object_counts(tmp_path):
    source = tmp_path / "native.pdf"
    sha = _source(source, extra="1 1 1 rg 30 30 10 10 re f")
    independent = PdfReader(source).pages[0].extract_text()
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[0]
        selected = _record(page.extract_text())
        proof = native_source_raster_evidence(selected, page, sha, independent)
        original = page.to_image(resolution=120).original
        data = BytesIO()
        original.save(data, format="PNG")
        assert proof["rendered_image_sha256"] == hashlib.sha256(data.getvalue()).hexdigest()
        assert proof["nontext_objects"]["rects"] == 1
        assert proof["glyph_count"] == len(page.chars) > 20
        assert proof["source_bbox"] == [0, 0, 400, 400]
        assert proof["unaccounted_ink_pixels"] == 0
        assert proof["ready"] and proof["page_complete"] and proof["reading_order_proven"]
        assert proof["source_pdf_sha256"] == sha
        assert proof["page"] == 1 and proof["page_text_sha256"] == selected["text_sha256"]
        assert len(proof["glyph_boxes_sha256"]) == 64
        table = assess_page(selected, source_pdf_sha256=sha, package_pdf_sha256=sha,
                            independent_text=independent, source_geometry=proof, table_count=1)
        assert table["outcome"] == "repair_required"
        assert table["components"]["source_render"]["state"] == "pass"
        assert table["components"]["layout"]["reason"] == "table_row_column_proof_unavailable"


def test_native_unaccounted_vector_ink_cannot_be_released(tmp_path):
    source = tmp_path / "unaccounted.pdf"
    sha = _source(source, extra="0 0 0 rg 60 150 80 30 re f")
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[0]
        proof = native_source_raster_evidence(_record(page.extract_text()), page, sha,
                                               PdfReader(source).pages[0].extract_text())
    assert not proof["ready"] and not proof["page_complete"]
    assert proof["reason"] == "source_ink_outside_native_glyphs"
    assert proof["unaccounted_ink_pixels"] > 1000
    assert proof["unresolved_visual_regions"][0]["bbox_normalized"]


def test_native_word_order_numeric_order_and_invalid_source_box_hold(tmp_path):
    source = tmp_path / "native.pdf"
    sha = _source(source)
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[0]
        selected = _record(page.extract_text())
        reordered = "The charge is 12% for 51 years."
        proof = native_source_raster_evidence(selected, page, sha, reordered)
        assert not proof["ready"] and not proof["independent_number_order_agrees"]
        assert not proof["independent_word_order_agrees"]
        words = native_source_raster_evidence(selected, page, sha,
                                               "The years is 51% for 12 charge.")
        assert not words["ready"] and words["independent_number_order_agrees"]
        assert not words["reading_order_proven"]
        page.chars[0]["x0"] = page.chars[0]["x1"]+1
        bad = native_source_raster_evidence(selected, page, sha, selected["text"])
        assert not bad["ready"] and bad["reason"] == "invalid_native_glyph_box"
        stale = native_source_raster_evidence({**selected, "text_sha256": "a"*64}, page, sha, selected["text"])
        assert stale["reason"] == "page_text_hash_mismatch"


def test_native_cropbox_coordinates_match_rendered_canvas(tmp_path):
    source = tmp_path / "cropped.pdf"
    sha = _source(source, crop=True)
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[0]
        proof = native_source_raster_evidence(_record(page.extract_text()), page, sha,
                                               PdfReader(source).pages[0].extract_text())
    assert proof["ready"] and proof["source_bbox"] == [40, 40, 360, 360]
    assert proof["unaccounted_ink_pixels"] == 0


def test_source_raster_batch_flag_generates_native_proof_without_override(tmp_path):
    from local_vector_batch.pdf_ingest import extract_pdf
    source = tmp_path / "native.pdf"
    _source(source, extra="1 1 1 rg 30 30 10 10 re f")
    package = tmp_path / "package"
    extract_pdf(source, package, ocr="off", tables=False,
                title="Native source", source_url="https://example.org/native.pdf")
    held = assess_package(package, source)
    assert held["counts"]["auto_accepted"] == 0
    audited = assess_package(package, source, derive_ocr_source_raster=True)
    assert audited["counts"] == {"auto_accepted": 1, "repair_required": 0, "source_blocked": 0}
    assert audited["results"][0]["components"]["source_render"]["rendered_image_sha256"]
    assert audited["human_review_receipts_created"] == 0


def test_source_raster_batch_flag_closes_real_pdfium_ocr_page(tmp_path):
    from local_vector_batch.evidence_package import create_package
    from local_vector_batch.pdf_ingest import extract_pdf
    source = tmp_path / "ocr-source.pdf"
    _source(source)
    package = tmp_path / "package"
    extract_pdf(source, package, ocr="off", tables=False,
                title="OCR source", source_url="https://example.org/ocr.pdf")
    page = json.loads((package / "pages.jsonl").read_text().splitlines()[0])
    page.update(method="vision", status="review_required", ocr={"engine": "vision", "recognition_languages": ["en"],
                "lines": [{"text": page["text"], "bbox": [.14, .15, .85, .23]}]})
    (package / "pages.jsonl").write_text(json.dumps(page)+"\n")
    from local_vector_batch.structure_candidates import page_candidates
    structures = page_candidates(page)
    (package / "structure-candidates.jsonl").write_text("".join(json.dumps(row)+"\n" for row in structures))
    report = json.loads((package / "report.json").read_text())
    report.update(page_status_counts={"ready": 0, "blank": 0, "review_required": 1},
                  structure_candidates=len(structures))
    (package / "report.json").write_text(json.dumps(report))
    create_package(package)
    report = assess_package(package, source, derive_ocr_source_raster=True,
                            independent_by_page={1: page["text"]})
    assert report["counts"]["auto_accepted"] == 1
    assert report["results"][0]["components"]["source_render"]["rendered_image_sha256"]
