import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from local_vector_batch.pdf_batch import _repair_improved, extract_batch, extract_one, read_manifest


def test_repair_prefers_fewer_parser_gaps_even_when_ocr_text_is_shorter():
    before = {"text_chars": 1000, "page_status_counts": {"review_required": 2},
              "issues": [{"kind": "native_parser_disagreement"},
                         {"kind": "native_parser_disagreement"}]}
    after = {"text_chars": 800, "page_status_counts": {"review_required": 2},
             "issues": [{"kind": "native_parser_disagreement_recovered_by_ocr"},
                        {"kind": "ocr_layout_review"}]}
    assert _repair_improved(after, before)
    worse = {"text_chars": 1200, "page_status_counts": {"review_required": 2},
             "issues": [{"kind": "native_parser_disagreement"},
                        {"kind": "low_confidence_ocr"},
                        {"kind": "missing_text"}]}
    assert not _repair_improved(worse, before)


def test_batch_manifest_rejects_duplicate_and_unsafe_source_ids(tmp_path):
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text('\n'.join([
        json.dumps({"document_id": "reg-1", "path": "one.pdf", "url": "https://example.gov/one.pdf"}),
        json.dumps({"document_id": "reg-1", "path": "two.pdf", "url": "https://example.gov/two.pdf"}),
    ]) + '\n')
    with pytest.raises(ValueError, match="duplicate document_id"):
        read_manifest(manifest, limit=10)
    manifest.write_text(json.dumps({"document_id": "../escape", "path": "one.pdf"}) + '\n')
    with pytest.raises(ValueError, match="unsafe"):
        read_manifest(manifest, limit=10)
    manifest.write_text(json.dumps({"document_id": "reg-1", "path": "one.pdf",
                                    "title": "CERC 2025 amendment"}) + '\n')
    assert read_manifest(manifest, limit=10)[0]["title"] == "CERC 2025 amendment"
    manifest.write_text(json.dumps({"document_id": "reg-1", "path": "one.pdf", "title": "  "}) + '\n')
    with pytest.raises(ValueError, match="invalid title"):
        read_manifest(manifest, limit=10)


def test_source_receipt_hash_mismatch_fails_before_extraction(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    row = {"document_id": "reg-1", "path": str(source), "url": "https://example.gov/source.pdf",
           "sha256": hashlib.sha256(b"other").hexdigest()}
    result = extract_one(row, tmp_path / "out", resume=False, ocr="off", tables=False)
    assert result["status"] == "failed"
    assert "receipt SHA-256" in result["error"]
    assert not (tmp_path / "out" / "reg-1").exists()


def test_batch_reports_unavailable_sources_without_claiming_page_coverage(tmp_path):
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(json.dumps({"document_id": "reg-1", "status": "failed",
                                    "url": "https://example.gov/source.pdf"}) + '\n')
    report = extract_batch(manifest, tmp_path / "out", workers=1, limit=10)
    assert report["counts"]["source_unavailable"] == 1
    assert report["pages_extracted"] == report["pages_ready"] == 0
    assert json.loads(Path(report["report_path"]).read_text()) == report


def test_batch_rejects_unverifiable_forced_gpu_before_starting_jobs(tmp_path):
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(json.dumps({"document_id": "reg-1", "url": "https://example.gov/source.pdf"}) + '\n')
    with pytest.raises(ValueError, match="forced GPU OCR is unsupported"):
        extract_batch(manifest, tmp_path / "out", workers=1, limit=1, ocr_device="gpu")


def test_repaired_ocr_text_keeps_visual_coverage_held_until_source_proof(monkeypatch, tmp_path):
    image = pytest.importorskip("PIL.Image")
    from local_vector_batch.ocr import LocalOCR

    source = tmp_path / "scan.pdf"
    image.new("RGB", (300, 300), "white").save(source, "PDF")
    output = tmp_path / "out"
    output.mkdir()
    row = {"document_id": "scan-1", "path": str(source), "url": "https://example.gov/scan.pdf"}
    first = extract_one(row, output, resume=False, ocr="off", tables=False)
    assert first["status"] == "review_required"
    native_report = json.loads((output / "scan-1" / "report.json").read_text())
    assert native_report["complete_for_embedding"] is False
    assert native_report["ocr_compute"]["pages_eligible"] == 1
    assert native_report["ocr_compute"]["pages_attempted"] == 0

    text = "The regulator recorded a 123 MW capacity and a documented condition on this scanned page. " * 2
    monkeypatch.setattr(LocalOCR, "recognize", lambda _self, _page: {
        "engine": "vision", "text": text, "lines": [], "mean_confidence": .99,
        "queue_wait_ms": 0, "execution_ms": 1})
    second = extract_one(row, output, resume=True, ocr="auto", tables=False, repair_ocr=True)
    assert second["status"] == "review_required"
    assert second["repaired_ocr"] is True
    report = json.loads((output / "scan-1" / "report.json").read_text())
    assert report["complete_for_embedding"] is False
    assert any(i['kind']=='native_raster_content_unverified' for i in report['issues'])
    assert json.loads((output/'scan-1'/'pages.jsonl').read_text())['text']==text
    assert report["source_pdf_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(LocalOCR, "recognize", lambda _self, _page: None)
    refreshed = extract_one(row, output, resume=True, ocr="off", tables=False, refresh=True)
    assert refreshed["status"] == "review_required"
    assert refreshed["refreshed"] is True
    assert json.loads((output / "scan-1" / "report.json").read_text())["complete_for_embedding"] is False


def test_old_extractor_cache_is_not_silently_reused_or_overwritten(tmp_path):
    from pypdf import PdfWriter
    from local_vector_batch.evidence_package import create_package
    source=tmp_path/'source.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    with source.open('wb') as stream:writer.write(stream)
    output=tmp_path/'out';output.mkdir();row=dict(document_id='source',path=str(source),url='https://example.gov/source.pdf')
    extract_one(row,output,resume=False,ocr='off',tables=False)
    target=output/'source';report_path=target/'report.json';report=json.loads(report_path.read_text())
    report.pop('extractor_contract');report_path.write_text(json.dumps(report));create_package(target)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
    result=extract_one(row,output,resume=True,ocr='off',tables=False)
    assert result['status']=='retry_required' and result['reason']=='cached_extractor_outdated'
    after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
    assert before==after


@pytest.mark.skipif(os.name != "posix", reason="per-document deadline requires POSIX signals")
def test_document_timeout_returns_explicit_retry_without_partial_package(monkeypatch, tmp_path):
    from local_vector_batch import pdf_ingest

    source = tmp_path / "slow.pdf"
    source.write_bytes(b"%PDF-test")
    output = tmp_path / "out"
    output.mkdir()
    def slow_extract(*_args, **_kwargs):
        time.sleep(.2)
    monkeypatch.setattr(pdf_ingest, "extract_pdf", slow_extract)
    row = {"document_id": "slow-1", "path": str(source), "url": "https://example.gov/slow.pdf"}
    result = extract_one(row, output, resume=False, ocr="off", tables=False, document_timeout=.02)
    assert result["status"] == "retry_required"
    assert "retry with a higher timeout" in result["error"]
    assert not (output / "slow-1").exists()
