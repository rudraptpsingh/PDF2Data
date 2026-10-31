"""Memory-lifetime regressions without changing evidence semantics."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pdfplumber
import pypdf

from local_vector_batch.pdf_ingest import _release_page_after_use, _write_json, extract_pdf
from local_vector_batch.evidence_package import _sha, _rows as package_rows, verify_package
from local_vector_batch.presentation import _rows as presentation_rows


class Page:
    objects = {'char': [1]}
    images = []
    def __init__(self, text, events, number):
        self.text, self.events, self.number = text, events, number
    def extract_text(self, **_):
        self.events.append(('extract', self.number))
        return self.text
    def find_tables(self):
        return []
    def close(self):
        self.events.append(('close', self.number))


class PDF:
    def __init__(self, pages):
        self.pages = pages
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


def install_source(tmp_path, monkeypatch):
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'%PDF-mocked-large-document')
    text = 'Threshold is 51% for 12 years; original text and page identity must survive. ' * 3
    events = []
    pages = [Page(text, events, number) for number in (1, 2, 3)]
    monkeypatch.setattr(pdfplumber, 'open', lambda _: PDF(pages))
    monkeypatch.setattr(pypdf, 'PdfReader', lambda _: SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: text) for _ in pages]))
    return source, events


def test_page_cache_is_released_before_next_physical_page(tmp_path, monkeypatch):
    source, events = install_source(tmp_path, monkeypatch)
    output = tmp_path / 'package'
    extract_pdf(source, output, ocr='off', tables=False)
    assert events.index(('close', 1)) < events.index(('extract', 2))
    assert events.index(('close', 2)) < events.index(('extract', 3))
    assert events.count(('close', 3)) == 1
    assert verify_package(output, source_pdf=source)['pages'] == 3


def test_failing_progress_callback_still_releases_current_page(tmp_path, monkeypatch):
    source, events = install_source(tmp_path, monkeypatch)
    def cancel(*_):
        raise RuntimeError('cancel test')
    with pytest.raises(RuntimeError, match='cancel test'):
        extract_pdf(source, tmp_path / 'partial', ocr='off', tables=False, progress=cancel)
    assert events[-1] == ('close', 1)
    assert ('extract', 2) not in events
    assert not (tmp_path / 'partial/evidence-package.json').exists()


def test_page_scope_releases_after_processing_exception():
    events = []
    page = Page('', events, 1)
    with pytest.raises(ValueError):
        with _release_page_after_use(page):
            raise ValueError('page error')
    assert events == [('close', 1)]


def test_reused_pages_are_released_without_reextracting(tmp_path, monkeypatch):
    source, events = install_source(tmp_path, monkeypatch)
    baseline = tmp_path / 'baseline'
    extract_pdf(source, baseline, ocr='off', tables=False)
    events.clear()
    repaired = tmp_path / 'repaired'
    extract_pdf(source, repaired, ocr='off', tables=False,
                repair_pages={2}, reuse_package=baseline)
    assert events == [('close', 1), ('extract', 2), ('close', 2), ('close', 3)]
    assert (baseline / 'pages.jsonl').read_bytes() == (repaired / 'pages.jsonl').read_bytes()
    assert verify_package(repaired, source_pdf=source)['pages'] == 3


def test_streamed_json_is_byte_identical_to_previous_encoder(tmp_path):
    value = [{'unicode': '₹ कानून', 'rows': [[None, '0', '-15.20687'], ['x', True, 1.25]]}, {}]
    path = tmp_path / 'artifact.json'
    _write_json(path, value)
    assert path.read_bytes() == (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def test_hash_and_jsonl_readers_do_not_read_whole_file_buffers(tmp_path, monkeypatch):
    binary = tmp_path / 'large.bin'
    data = b'1234567890' * 210000
    binary.write_bytes(data)
    rows = tmp_path / 'rows.jsonl'
    rows.write_text('{"literal":"₹"}\n\n{"amount":"1.00000"}\n')
    monkeypatch.setattr(Path, 'read_bytes', lambda *_: (_ for _ in ()).throw(AssertionError('whole byte read')))
    monkeypatch.setattr(Path, 'read_text', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('whole text read')))
    assert _sha(binary) == sha256(data).hexdigest()
    expected = [{'literal': '₹'}, {'amount': '1.00000'}]
    assert package_rows(rows) == presentation_rows(rows) == expected
