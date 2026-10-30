"""Real original PDF ink, cached OCR provenance, and no page promotion."""

from copy import deepcopy
from hashlib import sha256

import pytest

pdfplumber = pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
pytest.importorskip("cv2")
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from local_vector_batch.verified_regions import (
    derive_text_region,
    region_candidates,
    validate_text_region,
    write_region_images,
)


def _fixture(tmp_path, extra="0 0 0 rg 20 370 10 10 re f"):
    path = tmp_path / "source.pdf"
    writer = PdfWriter()
    p = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    p[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 10 Tf 60 320 Td (4.9. The charge shall not exceed 51%.) Tj "
            "0 -25 Td (It applies for 12 years.) Tj ET\n" + extra
        ).encode()
    )
    p[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    with pdfplumber.open(path) as pdf:
        source = pdf.pages[0]
        text = source.extract_text()
        lines = []
        for top in sorted({c["top"] for c in source.chars}):
            chars = [c for c in source.chars if c["top"] == top]
            lines.append(
                {
                    "text": "".join(c["text"] for c in chars),
                    "bbox": [
                        min(c["x0"] for c in chars) / 400,
                        min(c["top"] for c in chars) / 400,
                        max(c["x1"] for c in chars) / 400,
                        max(c["bottom"] for c in chars) / 400,
                    ],
                }
            )
    page = {
        "page": 1,
        "text": text,
        "text_sha256": sha256(text.encode()).hexdigest(),
        "method": "vision",
        "status": "review_required",
        "ocr": {"engine": "vision", "lines": lines},
    }
    independent = {
        "source_pdf_sha256": sha256(path.read_bytes()).hexdigest(),
        "page": 1,
        "selected_page_text_sha256": page["text_sha256"],
        "render_dpi": 180,
        "render_engine": "pypdfium2",
        "table_on_page": False,
        "independent": {
            "engine": "rapidocr_onnx",
            "version": "3.10.0",
            "recognition_model": "en_PP-OCRv5_rec_mobile",
            "recognition_model_sha256": "b" * 64,
            "recognition_languages": ["en"],
            "text": text,
            "text_sha256": page["text_sha256"],
        },
    }
    return path, page, independent


def test_ready_region_retains_real_outside_ink_and_full_page_hold(tmp_path):
    source, page, independent = _fixture(tmp_path)
    original = deepcopy(page)
    proof = derive_text_region(page, source, independent, region_candidates(page)[0])
    assert proof["ready"], proof
    assert proof["acceptance_scope"] == "verified_transcription_only"
    assert proof["page_complete"] is False
    assert proof["context_unaccounted_ink_pixels"] == 0
    assert proof["outside_unresolved_visual_regions"]
    assert all(
        r["role"] == "unknown" and r["status"] == "held"
        for r in proof["outside_unresolved_visual_regions"]
    )
    assert page == original
    assert validate_text_region(page, source, independent, proof)
    files = write_region_images(proof, source, tmp_path / "images")
    assert (
        "source-page" in files and "context" in files and "outside-unknown-0" in files
    )
    assert (
        sha256((tmp_path / "images/context.png").read_bytes()).hexdigest()
        == proof["crop_sha256"]
    )
    assert (
        page["text"][proof["char_start"] : proof["char_start"] + proof["char_length"]]
        == proof["exact_quote"]
    )
    assert proof["bbox_points"][0] == pytest.approx(
        proof["bbox_pixels"][0] / proof["render_size"][0] * 400
    )


def test_unexplained_ink_inside_clause_context_holds_even_between_lines(tmp_path):
    source, page, independent = _fixture(tmp_path, "0 0 0 rg 70 310 10 4 re f")
    proof = derive_text_region(page, source, independent, region_candidates(page)[0])
    assert not proof["ready"]
    assert proof["reason"] == "unexplained_source_ink_inside_context"
    assert proof["context_unaccounted_ink_pixels"] > 0


@pytest.mark.parametrize(
    "before,after", [("51%", "52%"), ("shall not", "shall"), ("charge", "tariff")]
)
def test_exact_two_reader_tokens_required_not_whole_page_scores(
    tmp_path, before, after
):
    source, page, independent = _fixture(tmp_path)
    reader = independent["independent"]
    reader["text"] = reader["text"].replace(before, after)
    reader["text_sha256"] = sha256(reader["text"].encode()).hexdigest()
    proof = derive_text_region(page, source, independent, region_candidates(page)[0])
    assert not proof["ready"]
    assert "independent_region" in proof["reason"]


def test_tables_truncated_context_and_stale_or_forged_proof_cannot_pass(tmp_path):
    source, page, independent = _fixture(tmp_path)
    selection = region_candidates(page)[0]
    assert not derive_text_region(page, source, independent, selection, table_count=1)[
        "ready"
    ]
    assert not derive_text_region(
        page, source, independent, {**selection, "line_indexes": [0]}
    )["ready"]
    proof = derive_text_region(page, source, independent, selection)
    altered = deepcopy(proof)
    altered["bbox_pixels"][0] += 1
    assert not validate_text_region(page, source, independent, altered)
    altered = deepcopy(page)
    altered["text"] += "unrelated subsequent OCR correction"
    assert validate_text_region(altered, source, independent, proof)
    altered = deepcopy(proof)
    altered["primary_page_snapshot"]["text"] += "!"
    assert not validate_text_region(page, source, independent, altered)
    source.write_bytes(source.read_bytes() + b"\n%changed")
    assert not validate_text_region(page, source, independent, proof)


def test_adjacent_exception_cannot_be_excluded():
    page = {
        "text": "The limit is 51%.\nHowever, it is 40% for plants.",
        "ocr": {
            "lines": [
                {"text": "The limit is 51%."},
                {"text": "However, it is 40% for plants."},
            ]
        },
    }
    from local_vector_batch.verified_regions import _selection

    with pytest.raises(ValueError, match="excluded_adjacent_exception"):
        _selection(page, [0], "text_region")
