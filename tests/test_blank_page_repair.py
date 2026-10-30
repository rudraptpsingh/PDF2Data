import hashlib
import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
import pypdfium2 as pdfium

from local_vector_batch.blank_page_repair import commit_exact_white_pages, _raster_sha
from local_vector_batch.evidence_package import create_package, verify_package
from local_vector_batch.pdf_ingest import extract_pdf


def _review_package(tmp_path):
    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(source)
    package = tmp_path / "package"
    extract_pdf(source, package, title="Empty", ocr="off")
    page = json.loads((package / "pages.jsonl").read_text().strip())
    page["status"] = "review_required"
    (package / "pages.jsonl").write_text(json.dumps(page) + "\n")
    report = json.loads((package / "report.json").read_text())
    report["page_status_counts"] = {"ready": 0, "blank": 0, "review_required": 1}
    (package / "report.json").write_text(json.dumps(report))
    create_package(package)
    raster_sha, dark, gray, native_chars = _raster_sha(source, 1, 180)
    assert (dark, gray, native_chars) == (0, 0, 0)
    receipt = {"source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "source_pdf_path": str(source), "page": 1,
               "page_text_sha256": page["text_sha256"], "status": "raster_blank_180",
               "render_dpi": 180, "render_gray_sha256": raster_sha,
               "ink_pixels_below_200": 0, "ink_pixels_below_250": 0,
               "source_pdf_sha_verified": True}
    return source, package, receipt


def test_exact_white_source_page_commits_without_vector_text(tmp_path):
    source, package, receipt = _review_package(tmp_path)
    assert commit_exact_white_pages(package, source, [receipt])["pages_blank"] == [1]
    assert json.loads((package / "pages.jsonl").read_text())["status"] == "blank"
    assert not (package / "embedding-input.jsonl").read_text().strip()
    verify_package(package, source_pdf=source)
    with pytest.raises(ValueError, match="compare-and-swap"):
        commit_exact_white_pages(package, source, [receipt])


def test_stale_raster_and_nonwhite_receipts_cannot_commit(tmp_path):
    source, package, receipt = _review_package(tmp_path)
    before = (package / "evidence-package.json").read_bytes()
    wrong = {**receipt, "render_gray_sha256": "0" * 64}
    with pytest.raises(ValueError, match="changed or has native text"):
        commit_exact_white_pages(package, source, [wrong])
    wrong = {**receipt, "ink_pixels_below_250": 1}
    with pytest.raises(ValueError, match="exact-white"):
        commit_exact_white_pages(package, source, [wrong])
    assert (package / "evidence-package.json").read_bytes() == before


def test_nonwhite_page_requires_hash_bound_visual_nontext_review(tmp_path):
    source = tmp_path / "border.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"0 0 0 RG 2 w 20 20 572 752 re S")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source)
    package = tmp_path / "package"
    extract_pdf(source, package, title="Empty border", ocr="off")
    current = json.loads((package / "pages.jsonl").read_text())
    assert current["status"] == "review_required" and not current["text"]
    raster_sha, dark, gray, native_chars = _raster_sha(source, 1, 180)
    assert dark and gray and native_chars == 0
    png = tmp_path / "border.png"
    pdfium.PdfDocument(str(source))[0].render(scale=2.5).to_pil().save(png)
    receipt = {"source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "source_pdf_path": str(source), "page": 1,
               "page_text_sha256": current["text_sha256"],
               "status": "visually_blank_nontext_180", "render_dpi": 180,
               "render_gray_sha256": raster_sha,
               "source_pdf_sha_verified": True,
               "nontext_role": "border_only", "visual_reviewed_by": "codex_source_visual",
               "visual_raster_png": str(png),
               "visual_raster_png_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
               "independent_ocr_line_count": 0,
               "independent_ocr_text_sha256": hashlib.sha256(b"").hexdigest()}
    with pytest.raises(ValueError, match="empty OCR"):
        commit_exact_white_pages(package, source, [{**receipt, "independent_ocr_line_count": 1}])
    assert commit_exact_white_pages(package, source, [receipt])["pages_blank"] == [1]
    verify_package(package, source_pdf=source)
