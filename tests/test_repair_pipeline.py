import json
from pathlib import Path
import pytest

from local_vector_batch.repair_pipeline import (
    _bound_independent_text, _route, _selected_ocr_engine, capabilities, prepare_jobs, reader_provenance,
    repair_status, run_repairs,
)


def test_selected_ocr_engine_requires_bound_selected_text():
    page = {"method": "vision", "text": "First\nSecond", "ocr": {
        "engine": "macos_vision", "text": "First\nSecond",
        "lines": [{"text": "First"}, {"text": "Second"}]}}
    assert _selected_ocr_engine(page, "vision") == "macos_vision"
    for changed in (
        {**page, "text": "A different reader"},
        {**page, "method": "pdf_text"},
        {**page, "ocr": {**page["ocr"], "lines": [{"text": "Different"}]}},
        {**page, "ocr": {**page["ocr"], "engine": "rapidocr_onnx"}},
    ):
        with pytest.raises(ValueError, match="not bound"):
            _selected_ocr_engine(changed, "vision")


def test_route_respects_quality_policy_and_does_not_rerun_verified_text():
    vision = {"method": "vision", "status": "review_required", "text": "The rate shall be 5%."}
    numbers = {"components": {"ordered_numbers": {"state": "fail", "reason": "ordered_numeric_tokens_disagree"}}}
    assert _route(vision, numbers, None) == "ocr_text"
    visual_gap = {"components": {name: {"state": "pass"} for name in
                    ("language", "words_and_order", "ordered_numbers", "ordered_operators")}}
    assert _route(vision, visual_gap, None) == "table_or_layout"
    assert _route({**vision, "text": ""}, numbers, None) == "source_blankness"
    assert _route({**vision, "method": "pdf_text"}, numbers, "receipt.json") == "native_consensus"
    assert _route(vision, numbers, None, "column.json") == "column_order"
    assert _route(vision, numbers, None, None, "region.json") == "ocr_region"
    assert _route({**vision, "method": "rapidocr"}, numbers, None) == "verify_selected_ocr"
    assert "external corpus/index adapter" in capabilities()["publication"]


def test_repair_queue_is_durable_and_does_not_retry_held_jobs(tmp_path: Path, monkeypatch):
    import local_vector_batch.repair_pipeline as pipeline
    jobs = [{"key": "a" * 64, "route": "table_or_layout", "source_pdf_sha256": "b" * 64,
             "page": 2, "original_text_sha256": "c" * 64}]
    monkeypatch.setattr(pipeline, "prepare_jobs", lambda manifest, limit, config: jobs)
    first = run_repairs(tmp_path / "unused.jsonl", tmp_path / "out", workers=1)
    second = run_repairs(tmp_path / "unused.jsonl", tmp_path / "out", workers=1)
    assert first["jobs_run"] == 1
    assert first["counts"] == {"awaiting_evidence": 1}
    assert second["jobs_run"] == 0
    assert second["results"] == first["results"]
    assert first["new_ready_pages"] == second["new_ready_pages"] == 0
    assert first["attempted_pages_per_second"] > 0
    assert second["attempted_pages_per_second"] == 0
    status = repair_status(tmp_path / "out")
    assert status["attempt_counts"] == {"awaiting_evidence": 1}
    assert status["current_page_counts"] == {"awaiting_evidence": 1}
    assert status["current_page_total"] == 1


def test_source_review_sidecar_prevents_machine_repair(tmp_path: Path, monkeypatch):
    import local_vector_batch.repair_pipeline as pipeline
    source_sha, text_sha = "a" * 64, "b" * 64
    package = tmp_path / "package"
    package.mkdir()
    (package / "pages.jsonl").write_text(json.dumps({"page": 1, "text": "Shall pay 5%",
                                                   "text_sha256": text_sha, "method": "vision",
                                                   "status": "review_required"}) + "\n")
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({"source_pdf_sha256": source_sha, "policy_sha256": "c" * 64,
                                   "results": [{"page": 1, "page_text_sha256": text_sha,
                                                "outcome": "repair_required", "components": {
                                                    "ordered_numbers": {"state": "fail"}}}]}))
    sidecar = tmp_path / "review.jsonl"
    sidecar.write_text(json.dumps({"source_pdf_sha256": source_sha, "page": 1,
                                   "decision": "keep_held"}) + "\n")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"package": str(package), "source_pdf": str(tmp_path / "source.pdf"),
                                    "quality_report": str(quality), "pages": [1],
                                    "review_sidecar": str(sidecar)}) + "\n")
    monkeypatch.setattr(pipeline, "_source_sha", lambda package, source: source_sha)
    assert prepare_jobs(manifest) == []
    sidecar.unlink()
    first = prepare_jobs(manifest)
    assert len(first) == 1
    monkeypatch.setattr(pipeline, "REPAIR_HANDLER_REVISION", pipeline.REPAIR_HANDLER_REVISION + 1)
    assert prepare_jobs(manifest)[0]["key"] != first[0]["key"]


def test_third_reader_receipt_is_bound_to_original_pdf_page(tmp_path: Path):
    from hashlib import sha256
    from io import BytesIO
    import pypdfium2 as pdfium
    from pypdf import PdfWriter
    import pytest

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    with source.open("wb") as stream:
        writer.write(stream)
    pdf = pdfium.PdfDocument(str(source))
    page = pdf[0]
    image = BytesIO()
    page.render(scale=180 / 72).to_pil().save(image, format="PNG")
    page.close()
    pdf.close()
    image_path = tmp_path / "source.png"
    image_path.write_bytes(image.getvalue())
    text = "A verified independent transcription."
    receipt = {"source_pdf_sha256": sha256(source.read_bytes()).hexdigest(), "page": 1,
               "original_text_sha256": "b" * 64, "engine": "mlx-vlm", "model": "model-name",
               "model_revision": "fixed-revision", "render_dpi": 180,
               "source_image_path": str(image_path),
               "source_image_sha256": sha256(image.getvalue()).hexdigest(),
               "independent_text": text, "independent_text_sha256": sha256(text.encode()).hexdigest()}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    assert _bound_independent_text(path, source, source_sha=receipt["source_pdf_sha256"],
                                   page=1, original_text_sha="b" * 64,
                                   candidate_engine="rapidocr") == text
    cached = {"source_pdf_sha256": receipt["source_pdf_sha256"], "page": 1,
              "selected_page_text_sha256": "b" * 64, "render_dpi": 180,
              "render_engine": "pypdfium2", "independent": {
                  "engine": "rapidocr_onnx", "version": "3.10.0",
                  "recognition_model": "en_PP-OCRv5_rec_mobile",
                  "recognition_model_sha256": "f" * 64,
                  "text": text, "text_sha256": sha256(text.encode()).hexdigest()}}
    assert _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                   page=1, original_text_sha="b" * 64,
                                   candidate_engine="vision") == text
    attested = reader_provenance(cached, source, {"page": 1, "text_sha256": "b" * 64,
        "ocr": {"engine": "vision"}}, text)
    assert attested["selected_engine_family"] == "apple_vision"
    assert attested["independent_engine_family"] == "rapidocr"
    assert attested["source_image_claimed_in_receipt"] is False
    assert len(attested["source_image_sha256"]) == 64
    with pytest.raises(ValueError, match="not independent"):
        reader_provenance(cached, source, {"page": 1, "text_sha256": "b" * 64,
            "ocr": {"engine": "rapidocr"}}, text)
    # The selected package uses the short runtime label, while the receipt
    # names its ONNX backend. They are still the same OCR reader family.
    with pytest.raises(ValueError, match="independence"):
        _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                page=1, original_text_sha="b" * 64,
                                candidate_engine="rapidocr")
    with pytest.raises(ValueError, match="independence"):
        _bound_independent_text({**cached, "independent": {**cached["independent"],
                                "engine": "pp-ocr"}}, source,
                                source_sha=receipt["source_pdf_sha256"], page=1,
                                original_text_sha="b" * 64, candidate_engine="rapidocr_onnx")
    with pytest.raises(ValueError, match="independence"):
        _bound_independent_text({**cached, "independent": {**cached["independent"],
                                "engine": "unknown-reader"}}, source,
                                source_sha=receipt["source_pdf_sha256"], page=1,
                                original_text_sha="b" * 64, candidate_engine="vision")
    with pytest.raises(ValueError, match="independence"):
        _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                page=1, original_text_sha="b" * 64,
                                candidate_engine="unknown-reader")
    with pytest.raises(ValueError, match="provenance"):
        _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                page=1, original_text_sha="c" * 64,
                                candidate_engine="vision")
    assert _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                   page=1, original_text_sha="c" * 64,
                                   candidate_engine="vision", reuse_source_ocr=True) == text
    with pytest.raises(ValueError, match="source PDF SHA"):
        _bound_independent_text(cached, source, source_sha="0" * 64,
                                page=1, original_text_sha="c" * 64,
                                candidate_engine="vision", reuse_source_ocr=True)
    original_pdf = source.read_bytes()
    source.write_bytes(original_pdf + b"\n")
    with pytest.raises(ValueError, match="source PDF SHA"):
        _bound_independent_text(cached, source, source_sha=receipt["source_pdf_sha256"],
                                page=1, original_text_sha="c" * 64,
                                candidate_engine="vision", reuse_source_ocr=True)
    source.write_bytes(original_pdf)
    receipt["source_image_sha256"] = "0" * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="source image SHA"):
        _bound_independent_text(path, source, source_sha=receipt["source_pdf_sha256"],
                                page=1, original_text_sha="b" * 64,
                                candidate_engine="rapidocr")


def test_ocr_resolution_is_explicitly_bounded():
    import pytest
    from local_vector_batch.ocr import LocalOCR

    with LocalOCR("off", render_dpi=300) as recognizer:
        assert recognizer.render_dpi_requested == 300
    with pytest.raises(ValueError, match="DPI"):
        LocalOCR("off", render_dpi=600)


def test_region_receipt_list_forms_one_atomic_page_job(tmp_path: Path, monkeypatch):
    import local_vector_batch.repair_pipeline as pipeline
    source_sha, text_sha = "a" * 64, "b" * 64
    package = tmp_path / "package"
    package.mkdir()
    (package / "pages.jsonl").write_text(json.dumps({"page": 4, "text": "a b",
        "text_sha256": text_sha, "method": "vision", "status": "review_required"}) + "\n")
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({"source_pdf_sha256": source_sha, "policy_sha256": "c" * 64,
        "results": [{"page": 4, "page_text_sha256": text_sha,
                     "outcome": "repair_required", "components": {"source_render": {"state": "fail"}}}]}))
    receipts = []
    for index in range(2):
        path = tmp_path / f"region-{index}.json"
        path.write_text(json.dumps({"source_pdf_sha256": source_sha, "page": 4,
                                    "region": index}))
        receipts.append(str(path))
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"package": str(package), "source_pdf": str(tmp_path / "source.pdf"),
        "quality_report": str(quality), "pages": [4], "region_receipts": {"4": receipts}}) + "\n")
    monkeypatch.setattr(pipeline, "_source_sha", lambda package, source: source_sha)
    jobs = prepare_jobs(manifest)
    assert len(jobs) == 1
    assert jobs[0]["route"] == "ocr_region"
    assert jobs[0]["region_receipts"] == receipts
    (tmp_path / "region-1.json").write_text('{"changed":true}')
    assert prepare_jobs(manifest)[0]["key"] != jobs[0]["key"]
