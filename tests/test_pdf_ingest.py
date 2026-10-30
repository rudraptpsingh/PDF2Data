import json
import subprocess
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

pdfplumber = pytest.importorskip("pdfplumber")

from local_vector_batch.model import make_encoder
from local_vector_batch.pdf_ingest import extract_pdf, independent_text_coverage, short_native_page_evidence
from local_vector_batch import ocr as ocr_module
from local_vector_batch.ocr import LocalOCR
from local_vector_batch.pipeline import Config, run, verify


def test_rapidocr_word_boxes_keep_cell_geometry(monkeypatch):
    from PIL import Image

    class Result:
        boxes = [[[10, 20], [90, 20], [90, 40], [10, 40]]]
        txts = ["AC line 90"]
        scores = [.99]
        word_results = [[("AC", .99, [[10, 20], [25, 20], [25, 40], [10, 40]]),
                         ("line", .99, [[30, 20], [60, 20], [60, 40], [30, 40]]),
                         ("90", .99, [[70, 20], [90, 20], [90, 40], [70, 40]])]]

    def reader(_image, *, return_word_box):
        assert return_word_box is True
        return Result()

    monkeypatch.setattr(ocr_module, "_rapidocr_reader", lambda _model: reader)
    lines, _elapsed = ocr_module._rapidocr_lines(Image.new("RGB", (100, 100)))
    assert [word["text"] for word in lines[0]["words"]] == ["AC", "line", "90"]
    assert lines[0]["words"][2]["bbox"] == [.7, .2, .9, .4]


def test_auto_vision_empty_uses_independent_review_only_candidate(monkeypatch):
    from PIL import Image, ImageDraw
    monkeypatch.setattr(ocr_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/bin/true" if name == "swiftc" else None)
    monkeypatch.setattr(ocr_module, "_run_vision", lambda *_: (
        subprocess.CompletedProcess([], 0, stdout='{"lines":[]}'), 0, 8))
    monkeypatch.setattr(ocr_module, "_rapidocr_lines", lambda image, model: (
        [{"text": "51%", "confidence": .83, "bbox": [.2, .2, .3, .3], "words": []}], 12))

    class RenderPage:
        width, height = 612, 792

        def to_image(self, resolution):
            image = Image.new("RGB", (200, 200), "white")
            ImageDraw.Draw(image).rectangle((20, 20, 70, 70), fill="black")
            return type("Rendered", (), {"original": image})()

    with LocalOCR("auto") as recognizer:
        recognizer.binary = Path("/tmp/unused-vision")
        candidate = recognizer.recognize(RenderPage())
    assert candidate["engine"] == "rapidocr"
    assert candidate["recognition_model"] == "en_PP-OCRv5_rec_mobile"
    assert candidate["text"] == "51%"
    assert candidate["device_used"] == "cpu"
    assert candidate["fallback"]["trigger"] == "vision_empty_visible_ink"
    assert candidate["fallback"]["primary_text_sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert candidate["fallback"]["alternate_text_sha256"] == __import__("hashlib").sha256(b"51%").hexdigest()
    assert candidate["fallback"]["numeric_agreement_verified"] is False
    assert candidate["fallback"]["requires_source_review"] is True
    assert candidate["execution_ms"] == 20


def test_visually_white_vision_empty_does_not_invoke_alternate_ocr(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(ocr_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/bin/true" if name == "swiftc" else None)
    monkeypatch.setattr(ocr_module, "_run_vision", lambda *_: (
        subprocess.CompletedProcess([], 0, stdout='{"lines":[]}'), 0, 8))
    monkeypatch.setattr(ocr_module, "_rapidocr_lines", lambda image, model: (_ for _ in ()).throw(AssertionError("unexpected OCR")))

    class BlankPage:
        width, height = 612, 792

        def to_image(self, resolution):
            return type("Rendered", (), {"original": Image.new("RGB", (200, 200), "white")})()

    with LocalOCR("auto") as recognizer:
        recognizer.binary = Path("/tmp/unused-vision")
        candidate = recognizer.recognize(BlankPage())
    assert candidate["engine"] == "vision" and candidate["text"] == ""


def test_vision_language_routing_and_unsupported_pack(monkeypatch):
    monkeypatch.setattr(ocr_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/bin/true" if name == "swiftc" else None)
    with LocalOCR("auto", language="eng") as default:
        assert default.recognition_languages == ()
    with LocalOCR("auto", language="eng+hin") as bilingual:
        assert bilingual.recognition_languages == ("hi-IN", "en-IN")
    with pytest.raises(ValueError, match="unsupported"):
        LocalOCR("auto", language="tam")


def test_explicit_rapidocr_candidate_keeps_cpu_provenance(monkeypatch):
    from PIL import Image
    seen = []
    def fake_rapidocr(image, model):
        seen.append(model)
        return ([{"text": "12 years", "confidence": .91, "bbox": [0, 0, 1, 1], "words": []}], 17)
    monkeypatch.setattr(ocr_module, "_rapidocr_lines", fake_rapidocr)
    class Page:
        width, height = 612, 792
        def to_image(self, resolution):
            return type("Rendered", (), {"original": Image.new("RGB", (20, 20), "white")})()
    with LocalOCR("rapidocr") as recognizer:
        result = recognizer.recognize(Page())
    assert result["engine"] == "rapidocr"
    assert result["device_used"] == "cpu"
    assert result["text"] == "12 years"
    assert result["recognition_languages"] == ["en"]
    assert result["recognition_model"] == "en_PP-OCRv5_rec_mobile"
    with LocalOCR("rapidocr", language="hin") as recognizer:
        hindi = recognizer.recognize(Page())
    assert hindi["recognition_languages"] == ["hi", "en"]
    assert hindi["recognition_model"] == "devanagari_PP-OCRv5_rec_mobile"
    assert seen == ["en", "devanagari"]


def test_ocr_device_receipt_does_not_claim_unobservable_vision_gpu():
    with LocalOCR("off", device="auto") as recognizer:
        assert recognizer.device_used == "none"
    with pytest.raises(ValueError, match="forced GPU OCR is unsupported"):
        LocalOCR("off", device="gpu")


def test_vision_requests_are_serialized_across_parallel_pdf_workers(monkeypatch, tmp_path):
    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_run(*_args, **_kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(.05)
        with guard:
            active -= 1
        return subprocess.CompletedProcess([], 0, stdout='{"lines": []}')

    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)
    rendezvous = threading.Barrier(2)
    def concurrent_request():
        rendezvous.wait(timeout=2)
        return ocr_module._run_vision(tmp_path / "vision", tmp_path / "page.png", "auto")
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(concurrent_request) for _ in range(2)]
        receipts = [job.result() for job in jobs]
    assert peak == 1
    assert sum(wait_ms for _result, wait_ms, _elapsed in receipts) >= 40


class Table:
    bbox = (10, 20, 200, 150)

    def extract(self):
        return [["Category", "Rate"], ["1", "100%"], [None, "75%"]]


class Page:
    objects = {"char": [1]}
    images = []

    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text

    def find_tables(self):
        return [Table()]


class PDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_independent_pdf_text_check_accepts_line_breaks_but_rejects_missing_values():
    reference = "inter state draw al 01.01.2 030 and 51%"
    selected = "inter-state drawal 01.01.2030 and 51%"
    equivalent = independent_text_coverage(selected, reference)
    assert equivalent["char_recall"] == 1 and equivalent["digit_recall"] == 1
    missing = independent_text_coverage(selected.replace("51%", "5%"), reference)
    assert missing["digit_recall"] < 1
    assert independent_text_coverage(selected.replace("drawal", "draw"), reference)["char_recall"] < 1


def test_independent_pdf_check_ignores_parser_glyph_ids_but_still_catches_missing_legal_digits():
    selected = "The rule is 51% हिं(cid:3)दी from 2025."
    reference = "The rule is 51% हिं/g7008दी from 2025."
    equivalent = independent_text_coverage(selected, reference)
    assert equivalent["char_recall"] == equivalent["digit_recall"] == 1
    missing = independent_text_coverage(selected.replace("51%", "5%"), reference)
    assert missing["digit_recall"] < 1


def test_bilingual_page_with_unresolved_glyph_is_ocr_or_review_required():
    from local_vector_batch.pdf_ingest import text_quality
    quality = text_quality("Hindi heading ककक(cid:3)े and an English legal clause")
    assert quality["needs_ocr"] is True


def test_independent_pdf_check_counts_indic_vowel_marks_as_content():
    complete = independent_text_coverage("कानून 2025", "कानून 2025")
    missing_mark = independent_text_coverage("कनून 2025", "कानून 2025")
    assert complete["char_recall"] == 1
    assert missing_mark["char_recall"] < 1


def test_short_native_page_requires_independent_text_and_full_source_ink_coverage():
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 18, 19), fill="black")

    class Page:
        chars = [{"text": "5", "x0": 10, "x1": 18, "top": 10, "bottom": 19}]
        images = curves = rects = lines = []

        def to_image(self, resolution):
            assert resolution == 72
            return type("Rendered", (), {"original": image})()

    page = Page()
    ready = short_native_page_evidence(page, "5", "5")
    assert ready["ready"] and ready["unaccounted_ink_pixels"] == 0
    assert not short_native_page_evidence(page, "5", "6")["ready"]
    ImageDraw.Draw(image).rectangle((60, 60, 68, 69), fill="black")
    hidden = short_native_page_evidence(page, "5", "5")
    assert not hidden["ready"] and hidden["reason"] == "visible_ink_outside_native_text"
    page.images = [{"src": "scan"}]
    assert short_native_page_evidence(page, "5", "5")["reason"] == "nontext_objects_require_review"


def test_targeted_repair_preserves_every_untouched_page_artifact(tmp_path, monkeypatch):
    import pypdf
    from local_vector_batch.evidence_package import verify_package
    source = tmp_path / "two-page.pdf"
    source.write_bytes(b"%PDF-test")
    first = "The statutory threshold is 51% for 12 years. " * 3
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(first), Page("")]))

    class OtherPage:
        def __init__(self, text):
            self.text = text
        def extract_text(self):
            return self.text
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("Reader", (), {
        "pages": [OtherPage(first), OtherPage("")]})())
    baseline = tmp_path / "baseline"
    extract_pdf(source, baseline, title="Two page source", source_url="https://example.gov/two.pdf", ocr="off")
    assert verify_package(baseline, source_pdf=source)["pages"] == 2
    candidate = {"engine": "rapidocr", "text": "Notice filed on 12 June 2026.",
                 "mean_confidence": .9, "lines": [], "render_dpi": 180,
                 "bbox_coordinates": "normalized top-left"}
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: candidate)
    repaired = tmp_path / "repaired"
    report = extract_pdf(source, repaired, title="Two page source",
                         source_url="https://example.gov/two.pdf", ocr="auto",
                         tables=True, repair_pages={2}, reuse_package=baseline)
    assert report["pages"] == 2
    assert verify_package(repaired, source_pdf=source)["pages"] == 2
    for name in ("pages.jsonl", "embedding-input.jsonl", "fact-candidates.jsonl",
                 "structure-candidates.jsonl"):
        before = [json.loads(s) for s in (baseline / name).read_text().splitlines() if s.strip()]
        after = [json.loads(s) for s in (repaired / name).read_text().splitlines() if s.strip()]
        def first_page(row):
            return row.get("page", row.get("metadata", {}).get("page",
                   (row.get("evidence") or [{}])[0].get("page"))) == 1
        assert [r for r in before if first_page(r)] == [r for r in after if first_page(r)]
    assert json.loads((baseline / "tables.json").read_text())[0] == \
           json.loads((repaired / "tables.json").read_text())[0]
    pages = [json.loads(s) for s in (repaired / "pages.jsonl").read_text().splitlines()]
    assert pages[1]["method"] == "rapidocr" and pages[1]["status"] == "review_required"
    with pytest.raises(ValueError, match="source identity"):
        extract_pdf(source, tmp_path / "wrong", title="Different", source_url="https://example.gov/two.pdf",
                    repair_pages={2}, reuse_package=baseline)
    from local_vector_batch.pdf_repair import repair_pdf_pages
    receipt = repair_pdf_pages(source, baseline, {2}, ocr="auto")
    assert receipt["changed_pages"] == [2] and receipt["reused_pages"] == 1
    assert verify_package(baseline, source_pdf=source)["pages"] == 2
    assert not list(tmp_path.glob(".baseline.backup-*"))


def test_short_text_only_pdf_page_is_ready_after_source_ink_audit(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw
    import pypdf
    source = tmp_path / "short.pdf"
    source.write_bytes(b"%PDF-test")
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 18, 19), fill="black")

    class ShortPage(Page):
        chars = [{"text": "5", "x0": 10, "x1": 18, "top": 10, "bottom": 19}]
        images = curves = rects = lines = []
        def to_image(self, resolution):
            return type("Rendered", (), {"original": image})()

    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([ShortPage("5")]))
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("Reader", (), {
        "pages": [type("OtherPage", (), {"extract_text": lambda self: "5"})()]})())
    output = tmp_path / "out"
    report = extract_pdf(source, output, ocr="off", tables=False)
    page = json.loads((output / "pages.jsonl").read_text())
    assert page["status"] == "ready"
    assert page["short_source_evidence"]["unaccounted_ink_pixels"] == 0
    assert report["page_status_counts"]["ready"] == 1


def test_short_native_footer_without_exposed_images_still_attempts_ocr(tmp_path, monkeypatch):
    import pypdf
    source = tmp_path / "footer.pdf"
    source.write_bytes(b"%PDF-test")
    footer = "Page 13 of 88"
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(footer)]))
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("Reader", (), {
        "pages": [type("OtherPage", (), {"extract_text": lambda self: footer})()]})())
    seen = []
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: seen.append(page) or None)
    extract_pdf(source, tmp_path / "out", ocr="off", tables=False)
    assert len(seen) == 1


def test_independent_parser_missing_number_blocks_embedding(tmp_path, monkeypatch):
    import pypdf
    source = tmp_path / "number.pdf"
    source.write_bytes(b"%PDF-test")
    selected = "The requirement is renewable charging and applies for 12 years. " * 3
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(selected)]))

    class OtherPage:
        def extract_text(self):
            return selected + " The threshold is 51%."

    class OtherPDF:
        pages = [OtherPage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda _: OtherPDF())
    out = tmp_path / "out"
    report = extract_pdf(source, out, ocr="off")
    assert not report["complete_for_embedding"]
    assert any(item["kind"] == "native_parser_disagreement" and item["digit_recall"] < 1
               for item in report["issues"])
    page = json.loads((out / "pages.jsonl").read_text())
    assert "51%" in page["secondary_native_text"]


def test_independent_parser_gap_triggers_ocr_without_claiming_native_text_is_complete(tmp_path, monkeypatch):
    import pypdf
    source = tmp_path / "parser-gap.pdf"
    source.write_bytes(b"%PDF-test")
    native = "The renewable charging condition applies every year. " * 3
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(native)]))

    class OtherPage:
        def extract_text(self):
            return native + " It requires 51%."

    class OtherPDF:
        pages = [OtherPage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda _: OtherPDF())
    recovered = {"engine": "vision", "text": native + " It requires 51%.",
                 "mean_confidence": .96, "lines": [], "bbox_coordinates": "normalized top-left"}
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: recovered)
    output = tmp_path / "out"
    report = extract_pdf(source, output)
    page = json.loads((output / "pages.jsonl").read_text())
    assert report["complete_for_embedding"]
    assert page["method"] == "vision" and page["status"] == "review_required"
    assert page["native_text"] == native.strip() and "51%" in page["secondary_native_text"]
    assert any(i["kind"] == "native_parser_disagreement_recovered_by_ocr" for i in report["issues"])


def test_ready_page_requires_independent_coverage_even_with_secondary_nul(tmp_path, monkeypatch):
    import pypdf
    source = tmp_path / "secondary-nul.pdf"
    source.write_bytes(b"%PDF-test")
    native = "A legal condition applies for 120 days and 90 days. " * 3
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(native)]))

    class OtherPage:
        def extract_text(self):
            return native + "\x00"

    class OtherPDF:
        pages = [OtherPage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda _: OtherPDF())
    output = tmp_path / "audited"
    extract_pdf(source, output, ocr="off")
    page = json.loads((output / "pages.jsonl").read_text())
    assert page["status"] == "ready"
    assert page["independent_coverage"]["char_recall"] == 1
    assert page["independent_coverage"]["digit_recall"] == 1

    monkeypatch.setattr(OtherPage, "extract_text", lambda self: "")
    output = tmp_path / "inconclusive"
    extract_pdf(source, output, ocr="off")
    page = json.loads((output / "pages.jsonl").read_text())
    assert page["status"] == "review_required"
    assert page["independent_coverage"] is None


def test_pdf_artifacts_preserve_page_table_and_numeric_evidence(tmp_path, monkeypatch):
    import pypdf
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    first = "At least 51% renewable charging applies for 12 years. Capacity 500 MW. Fee ₹10,000. Date 1 July 2028."
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([
        Page(first),
        Page("Short appendix label"),
    ]))
    class OtherPDF:
        pages = [Page(first), Page("Short appendix label")]
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: OtherPDF())
    output = tmp_path / "extracted"
    report = extract_pdf(source, output, source_url="https://example.gov/source.pdf", ocr="off")
    assert report["pages"] == 2 and report["complete_for_embedding"]
    assert report["requires_source_review"]  # extracted table cells are candidates, not released facts
    structure = [json.loads(line) for line in (output / "structure-candidates.jsonl").read_text().splitlines()]
    assert report["structure_candidates"] == len(structure)
    assert report["structure_candidates_status"] == "review_required_no_auto_verified_claims"
    assert all(row["status"] == "review_required" for row in structure)
    tables = json.loads((output / "tables.json").read_text())
    assert tables[0]["cells"][2][0] is None  # never invent inherited category labels
    facts = [json.loads(line) for line in (output / "fact-candidates.jsonl").read_text().splitlines()]
    assert {"51%", "12 years", "500 MW", "₹10,000", "1 July 2028"} <= {row["value_text"] for row in facts}
    assert all(row["status"] == "unverified_candidate" and row["page"] == 1 for row in facts)
    pages = [json.loads(line) for line in (output / "pages.jsonl").read_text().splitlines()]
    assert [page["status"] for page in pages] == ["ready", "review_required"]
    assert report["page_status_counts"] == {"ready": 1, "blank": 0, "review_required": 1}
    for candidate in facts:
        page = pages[candidate["page"] - 1]
        assert page["text"][candidate["char_start"]:candidate["char_start"]+candidate["char_length"]] == candidate["value_text"]
        assert page["text_sha256"] == candidate["page_text_sha256"]

    class Encoder:
        device = "test:cpu"

        def encode(self, texts):
            return [[1., 0., 0.] for _ in texts]

    vectors = tmp_path / "vectors"
    manifest = run(Config(output / "embedding-input.jsonl", vectors, min_chars=1), encoder_factory=Encoder)
    assert verify(vectors)["vectors"] == 2  # short nonempty page must survive
    rows = [json.loads(line) for item in manifest["batches"] for line in (vectors / item["file"]).read_text().splitlines()]
    assert {row["metadata"]["page"] for row in rows} == {1, 2}
    assert all(row["metadata"]["source_pdf_sha256"] == report["source_pdf_sha256"] for row in rows)


def test_scanned_pdf_grid_is_review_only_with_source_and_page_hashes(tmp_path, monkeypatch):
    import pypdf
    from test_ocr_tables import scanned_grid

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-scanned-test")
    image, lines = scanned_grid([
        ["Condition", "Value"], ["Annual charging", "51%"], ["Waiver", "12 years"],
    ])

    class ScannedPage(Page):
        objects = {"image": [1]}
        images = [1]
        width = 612
        height = 340

        def __init__(self):
            super().__init__("")

        def find_tables(self):
            return []

        def to_image(self, resolution):
            assert resolution == 180
            return type("Rendered", (), {"original": image})()

    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([ScannedPage()]))
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("OtherPDF", (), {"pages": []})())
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: {
        "engine": "vision", "text": "Condition Value\nAnnual charging 51%\nWaiver 12 years",
        "mean_confidence": .98, "render_dpi": 180, "lines": lines,
    })
    out = tmp_path / "extracted"
    report = extract_pdf(source, out, source_url="https://example.gov/scan.pdf")
    table = json.loads((out / "tables.json").read_text())[0]
    page = json.loads((out / "pages.jsonl").read_text())
    assert report["ocr_table_candidates"] == 1
    assert table["cells"][1] == ["Annual charging", "51%"]
    assert table["source_pdf_sha256"] == report["source_pdf_sha256"]
    assert table["page_text_sha256"] == page["text_sha256"]
    assert table["status"] == "needs_row_column_review"
    assert page["status"] == "review_required"
    assert report["requires_source_review"] is True


def test_image_only_page_is_not_silently_embedded(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-test")
    page = Page("")
    page.images = [1]
    page.objects = {"image": [1]}
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([page]))
    report = extract_pdf(source, tmp_path / "out", ocr="off")
    assert not report["complete_for_embedding"]
    assert any(issue["kind"] == "missing_text" for issue in report["issues"])


def test_zero_page_primary_parser_recovers_secondary_page_tree_without_changing_source(tmp_path, monkeypatch):
    from hashlib import sha256
    from pypdf import PdfWriter

    source = tmp_path / "malformed-page-tree.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_blank_page(width=200, height=300)
    writer.write(source)
    source_sha = sha256(source.read_bytes()).hexdigest()
    real_open = pdfplumber.open

    def primary_misses_pages(path):
        if str(path) == str(source):
            return PDF([])
        return real_open(path)

    monkeypatch.setattr(pdfplumber, "open", primary_misses_pages)
    output = tmp_path / "recovered"
    report = extract_pdf(source, output, ocr="off")
    pages = [json.loads(line) for line in (output / "pages.jsonl").read_text().splitlines()]
    assert report["pages"] == 2
    assert [row["page"] for row in pages] == [1, 2]
    assert all(row["status"] == "blank" for row in pages)
    assert report["source_pdf_sha256"] == source_sha
    assert sha256(source.read_bytes()).hexdigest() == source_sha
    assert any(item["kind"] == "source_page_tree_recovered" for item in report["issues"])


def test_cached_only_mode_does_not_silently_download_through_mlx():
    with pytest.raises(ValueError, match="cached-only mode requires"):
        make_encoder(backend="mlx", model="BAAI/bge-base-en-v1.5", device="gpu", pooling="cls",
                     max_tokens=512, normalize=True, revision=None, threads=1, offline=True)


def test_long_garbled_native_text_is_recovered_without_losing_native_evidence(tmp_path, monkeypatch):
    source = tmp_path / "garbled.pdf"
    source.write_bytes(b"%PDF-test")
    native = "(cid:38)(cid:82)(cid:80)" * 20
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([Page(native)]))
    recovered = {"engine": "vision", "text": "At least 51% of annual charging energy must be renewable.",
                 "mean_confidence": .98, "lines": [], "bbox_coordinates": "normalized top-left"}
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: recovered)
    output = tmp_path / "out"
    report = extract_pdf(source, output)
    page = json.loads((output / "pages.jsonl").read_text())
    assert report["complete_for_embedding"]
    assert page["text"] == recovered["text"] and page["native_text"] == native
    assert page["method"] == "vision" and page["native_quality"]["needs_ocr"]
    assert page["status"] == "review_required" and report["requires_source_review"]
    assert not page["selected_quality"]["needs_ocr"]
    assert (output / "structure-candidates.jsonl").is_file()


def test_low_confidence_ocr_blocks_embedding(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-test")
    page = Page("")
    page.images = [1]
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([page]))
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, page: {
        "engine": "vision", "text": "Uncertain text and 51%", "mean_confidence": .4, "lines": []})
    report = extract_pdf(source, tmp_path / "out")
    assert not report["complete_for_embedding"]
    assert any(item["kind"] == "low_confidence_ocr" for item in report["issues"])


def test_all_blank_pdf_does_not_report_embedding_ready(tmp_path, monkeypatch):
    source = tmp_path / "blank.pdf"
    source.write_bytes(b"%PDF-test")
    page = Page("")
    page.objects = {}
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF([page]))
    report = extract_pdf(source, tmp_path / "out", ocr="off")
    assert not report["complete_for_embedding"]
    assert any(item["kind"] == "no_searchable_text" for item in report["issues"])
