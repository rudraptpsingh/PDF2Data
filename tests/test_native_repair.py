"""Native repair refuses stale or misbound source receipts without mutation."""
import hashlib
import json

import pytest


def test_native_repair_rejects_wrong_source_and_stale_page(tmp_path):
    pytest.importorskip("pdfplumber")
    from pypdf import PdfWriter
    from local_vector_batch.evidence_package import verify_package
    from local_vector_batch.native_repair import repair_native_page
    from local_vector_batch.pdf_ingest import extract_pdf

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    with source.open("wb") as stream:
        writer.write(stream)
    package = tmp_path / "package"
    extract_pdf(source, package, title="Test PDF", source_url="https://example.org/test.pdf",
                ocr="off", tables=False)
    manifest_before = (package / "evidence-package.json").read_bytes()
    page = json.loads((package / "pages.jsonl").read_text().splitlines()[0])
    base = {"source_pdf_path": str(source), "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_url": "https://example.org/test.pdf", "page": 1,
            "original_text_sha256": page["text_sha256"], "proposed_text": "new text",
            "proposed_text_sha256": hashlib.sha256(b"new text").hexdigest(),
            "source_render_evidence": {}}
    with pytest.raises(ValueError, match="source PDF identity"):
        repair_native_page(package, {**base, "source_url": "https://example.org/other.pdf"})
    with pytest.raises(ValueError, match="original page changed"):
        repair_native_page(package, {**base, "original_text_sha256": "0" * 64})
    assert (package / "evidence-package.json").read_bytes() == manifest_before
    assert verify_package(package, source_pdf=source)["integrity"] == "passed"
