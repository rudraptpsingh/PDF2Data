"""capture_layout with page workers gives exactly the single-process result (pages are independent)."""
import json
from hashlib import sha256
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")


def _text_pdf(path: Path, n: int) -> list[dict]:
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}. The Commission directs the petitioner to file the reply within four weeks.", fontsize=11)
        page.insert_text((72, 100), f"Clause {i + 1}(a): tariff for 2024-29 shall be trued up as per Regulation {i + 3}.", fontsize=11)
    doc.save(path)
    from pypdf import PdfReader
    pages = []
    for i, p in enumerate(PdfReader(path).pages):
        text = p.extract_text() or ""
        pages.append({"page": i + 1, "text": text, "text_sha256": sha256(text.encode()).hexdigest(), "method": "pdf_text"})
    return pages


def test_page_workers_match_single_process(tmp_path, monkeypatch):
    import local_vector_batch.legal_l1 as l1
    src = tmp_path / "doc.pdf"
    pages = _text_pdf(src, 10)
    monkeypatch.setenv("PDF2DATA_PAGE_WORKERS", "1")
    one = l1.capture_layout(src, pages)
    monkeypatch.setenv("PDF2DATA_PAGE_WORKERS", "2")
    assert l1._page_workers(len(pages)) == 2
    two = l1.capture_layout(src, pages)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert [r["page"] for r in two] == list(range(1, 11))


def test_short_documents_and_bad_settings_stay_single_process(monkeypatch):
    import local_vector_batch.legal_l1 as l1
    monkeypatch.setenv("PDF2DATA_PAGE_WORKERS", "8")
    assert l1._page_workers(6) == 1          # under 8 pages: one range of at least 4 pages
    assert l1._page_workers(40) == 8
    monkeypatch.setenv("PDF2DATA_PAGE_WORKERS", "x")
    assert l1._page_workers(40) == 1
    monkeypatch.delenv("PDF2DATA_PAGE_WORKERS")
    assert l1._page_workers(400) == 1        # default: unchanged behaviour
