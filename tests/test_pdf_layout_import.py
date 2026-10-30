import hashlib
import json

import pytest

from local_vector_batch.evidence_package import verify_package
from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.pdf_layout_import import import_layout_candidates


class _Page:
    objects = {"char": [1]}
    images = []

    def extract_text(self):
        return "Year 2025 10.40 9.36"

    def find_tables(self):
        return []


class _PDF:
    pages = [_Page()]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _fixture(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    import pdfplumber

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(source)
    monkeypatch.setattr(pdfplumber, "open", lambda _: _PDF())
    package = tmp_path / "package"
    extract_pdf(source, package, ocr="off")
    page = json.loads((package / "pages.jsonl").read_text())
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    raw = {"pages": {"1": {"size": {"width": 600, "height": 800}}}, "tables": [{
        "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 790, "r": 200,
                                          "b": 700, "coord_origin": "BOTTOMLEFT"}}],
        "data": {"num_rows": 2, "num_cols": 3, "table_cells": [
            {"start_row_offset_idx": 0, "end_row_offset_idx": 1,
             "start_col_offset_idx": 0, "end_col_offset_idx": 3, "text": "Year",
             "row_span": 1, "col_span": 3},
            *[{"start_row_offset_idx": 1, "end_row_offset_idx": 2,
               "start_col_offset_idx": i, "end_col_offset_idx": i+1,
               "text": value} for i, value in enumerate(("2025", "10.40", "9.36"))]]}}]}
    (comparison / "page-1.docling.json").write_text(json.dumps(raw))
    row = {"status": "review_required", "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
           "source_page": 1, "selected_text_sha256": page["text_sha256"],
           "candidate_tables": raw["tables"], "candidate_engine_version": "test",
           "candidate_language": "iso:en", "requested_device": "cpu"}
    (comparison / "page-comparisons.jsonl").write_text(json.dumps(row) + "\n")
    (comparison / "comparison-report.json").write_text(json.dumps({
        "source_pdf_sha256": row["source_pdf_sha256"], "candidate_status": "review_required",
        "pages_promoted": 0, "pages_requested": 1}))
    return source, package, comparison


def test_docling_table_import_preserves_page_text_and_blank_merged_cells(tmp_path, monkeypatch):
    source, package, comparison = _fixture(tmp_path, monkeypatch)
    original_pages = (package / "pages.jsonl").read_bytes()
    original_inputs = (package / "embedding-input.jsonl").read_bytes()
    receipt = import_layout_candidates(package, source, comparison)
    assert receipt["tables_added"] == 1
    assert receipt["structure_candidates_added"] == 4
    assert (package / "pages.jsonl").read_bytes() == original_pages
    assert (package / "embedding-input.jsonl").read_bytes() == original_inputs
    table = json.loads((package / "tables.json").read_text())[0]
    assert table["cells"] == [["Year", "", ""], ["2025", "10.40", "9.36"]]
    assert table["cell_spans"][0]["column_span"] == 3
    assert table["bbox"] == [10, 10, 200, 100]
    assert table["status"] == "needs_row_column_review"
    assert verify_package(package, source_pdf=source)["integrity"] == "passed"
    with pytest.raises(ValueError, match="already imported"):
        import_layout_candidates(package, source, comparison)


def test_docling_import_rejects_wrong_selected_page_hash_before_mutation(tmp_path, monkeypatch):
    source, package, comparison = _fixture(tmp_path, monkeypatch)
    before = (package / "evidence-package.json").read_bytes()
    path = comparison / "page-comparisons.jsonl"
    row = json.loads(path.read_text())
    row["selected_text_sha256"] = "0" * 64
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="selected page text hash"):
        import_layout_candidates(package, source, comparison)
    assert (package / "evidence-package.json").read_bytes() == before
