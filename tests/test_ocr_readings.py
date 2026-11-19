"""Page readings made ahead of time (pdf-ocr-pages) give the same extraction as reading the pages in the run."""
import io
import json

import pytest

pytest.importorskip("rapidocr")
pytest.importorskip("pypdf")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

from local_vector_batch.ocr import LocalOCR  # noqa: E402
from local_vector_batch.ocr_readings import load, read_pages  # noqa: E402
from local_vector_batch.pdf_ingest import extract_pdf  # noqa: E402


def _native_page(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return PdfReader(io.BytesIO(out.getvalue())).pages[0]


def _scanned_page(lines):
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default(size=40)
    for row, line in enumerate(lines):
        draw.text((120, 160 + row * 80), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=150)
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]


@pytest.fixture(scope="module")
def mixed_pdf(tmp_path_factory):
    writer = PdfWriter()
    writer.add_page(_native_page("This native page has more than eighty characters of text so OCR never reads it at all."))
    writer.add_page(_scanned_page(["ORDER OF THE COMMISSION", "Petition No. 12 of 2024", "Tariff for 2024-25"]))
    writer.add_page(_scanned_page(["The petition is allowed.", "Sd/- Chairperson"]))
    writer.add_page(_scanned_page(["Annexure A", "Capital cost Rs 120 crore"]))
    path = tmp_path_factory.mktemp("src") / "mixed.pdf"
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def _pages(package):
    return [json.loads(line) for line in (package / "pages.jsonl").read_text().splitlines() if line.strip()]


def test_readings_reproduce_the_run_and_skip_native_pages(mixed_pdf, tmp_path):
    first = read_pages(mixed_pdf, 1, 2, tmp_path / "r/1-2.jsonl")
    second = read_pages(mixed_pdf, 3, 3, tmp_path / "r/3-3.jsonl")
    assert (first["pages_read"], first["pages_skipped_native"], second["pages_read"]) == (1, 1, 1)

    live = extract_pdf(mixed_pdf, tmp_path / "live", ocr="rapidocr")
    split = extract_pdf(mixed_pdf, tmp_path / "split", ocr="rapidocr", ocr_readings=[tmp_path / "r"])
    assert [(p["page"], p["text"], p["method"]) for p in _pages(tmp_path / "split")] == \
           [(p["page"], p["text"], p["method"]) for p in _pages(tmp_path / "live")]
    assert "ORDER OF THE COMMISSION" in _pages(tmp_path / "split")[1]["text"]
    # Page 4 had no reading and was read in the run.
    assert split["ocr_compute"]["precomputed_pages"] == 2
    assert split["ocr_compute"]["readings"]["pages"] == 2
    assert split["ocr_compute"]["pages_attempted"] == live["ocr_compute"]["pages_attempted"] == 3
    assert "precomputed_pages" not in live["ocr_compute"]


def test_readings_bound_to_source_and_recognizer(mixed_pdf, tmp_path):
    read_pages(mixed_pdf, 2, 3, tmp_path / "r.jsonl")
    with LocalOCR("rapidocr", "eng") as reader:
        pages, receipt = load([tmp_path / "r.jsonl"], read_pages.__globals__["_sha"](mixed_pdf), reader)
        assert sorted(pages) == [2, 3] and receipt["files"][0]["pages"] == 2
        with pytest.raises(ValueError, match="another source PDF"):
            load([tmp_path / "r.jsonl"], "0" * 64, reader)
    with LocalOCR("rapidocr", "eng+hin") as reader:
        with pytest.raises(ValueError, match="another recognizer"):
            load([tmp_path / "r.jsonl"], read_pages.__globals__["_sha"](mixed_pdf), reader)


def test_overlapping_readings_and_bad_ranges_refused(mixed_pdf, tmp_path):
    read_pages(mixed_pdf, 2, 3, tmp_path / "a.jsonl")
    read_pages(mixed_pdf, 3, 4, tmp_path / "b.jsonl")
    with LocalOCR("rapidocr", "eng") as reader, pytest.raises(ValueError, match="Two OCR readings for page 3"):
        load([tmp_path / "a.jsonl", tmp_path / "b.jsonl"], read_pages.__globals__["_sha"](mixed_pdf), reader)
    with pytest.raises(ValueError, match="last physical page"):
        read_pages(mixed_pdf, 3, 9, tmp_path / "c.jsonl")
    with pytest.raises(ValueError, match="first <= last"):
        read_pages(mixed_pdf, 3, 2, tmp_path / "d.jsonl")
