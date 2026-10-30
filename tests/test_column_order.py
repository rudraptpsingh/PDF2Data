import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from local_vector_batch.column_order import (
    column_geometry_consensus,
    derive_column_order_repair,
)
from local_vector_batch.pdf_compare import _source_raster


def _fixture(tmp_path, *, extra_ink=False):
    import pdfplumber
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from local_vector_batch.pdf_ingest import extract_pdf
    from local_vector_batch.structure_candidates import page_candidates
    from local_vector_batch.evidence_package import create_package

    source = tmp_path / "columns.pdf"
    writer = PdfWriter()
    pdfpage = writer.add_blank_page(width=600, height=500)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    pdfpage[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    values = [
        (40, 400, "Left first charge is 51 percent."),
        (340, 400, "Right first charge is 73 percent."),
        (40, 350, "Left second term is 12 years."),
        (340, 300, "Right second term is 24 years."),
    ]
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "\n".join(
                f"BT /F1 11 Tf {x} {y} Td ({text}) Tj ET" for x, y, text in values
            )
            + ("\n0 0 0 rg 250 80 50 30 re f" if extra_ink else "")
        ).encode()
    )
    pdfpage[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source)
    package = tmp_path / "package"
    extract_pdf(
        source,
        package,
        ocr="off",
        tables=False,
        title="Columns",
        source_url="https://example.org/columns.pdf",
    )
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    lines = []
    with pdfplumber.open(source) as pdf:
        chars = pdf.pages[0].chars
        for x, y, text in values:
            matching = [
                c
                for c in chars
                if abs(c["x0"] - x) < 220
                and c["x0"] >= x
                and abs(c["y0"] - (y - 2.277)) < 0.1
            ]
            # The real page's character coordinates locate each independently
            # specified source text line, including its visible descenders.
            matching = [c for c in matching if c["x0"] < (300 if x == 40 else 600)]
            box = [
                min(c["x0"] for c in matching) / 600,
                min(c["top"] for c in matching) / 500,
                max(c["x1"] for c in matching) / 600,
                max(c["bottom"] for c in matching) / 500,
            ]
            lines.append({"text": text, "bbox": box, "confidence": 1, "words": []})
    page = json.loads((package / "pages.jsonl").read_text())
    page.update(
        text="\n".join(x["text"] for x in lines),
        method="vision",
        status="review_required",
        ocr={"engine": "vision", "recognition_languages": ["en"], "lines": lines},
    )
    page["text_sha256"] = hashlib.sha256(page["text"].encode()).hexdigest()
    page["chars"] = len(page["text"])
    # Rebuild the baseline OCR package through the same source-span producers.
    (package / "pages.jsonl").write_text(json.dumps(page) + "\n")
    inputs = [
        json.loads(x)
        for x in (package / "embedding-input.jsonl").read_text().splitlines()
    ]
    for row in inputs:
        row["text"] = page["text"]
        row["metadata"]["page_text_sha256"] = page["text_sha256"]
    (package / "embedding-input.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in inputs)
    )
    (package / "fact-candidates.jsonl").write_text("")
    structures = page_candidates(page)
    (package / "structure-candidates.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in structures)
    )
    report = json.loads((package / "report.json").read_text())
    report.update(
        page_status_counts={"ready": 0, "blank": 0, "review_required": 1},
        numeric_candidates=0,
        structure_candidates=len(structures),
        text_chars=len(page["text"]),
    )
    (package / "report.json").write_text(json.dumps(report))
    create_package(package)
    raster = _source_raster(source, 1, tmp_path)
    regions = []
    for index, line in enumerate(lines):
        box = line["bbox"]
        regions.append(
            {
                "id": f"r{index}",
                "kind": "text",
                "source_pdf_sha256": sha,
                "physical_page": 1,
                "page_text_sha256": page["text_sha256"],
                "rendered_image_sha256": raster["rendered_image_sha256"],
                "bbox_normalized": [
                    box[0] - 0.003,
                    box[1] - 0.003,
                    box[2] + 0.003,
                    box[3] + 0.003,
                ],
            }
        )
    metadata = {
        "source_pdf_sha256": sha,
        "page": 1,
        "text_sha256": hashlib.sha256(
            "\n".join(x["text"] for x in lines).encode()
        ).hexdigest(),
        "engine": "source_fixture_reader",
        "version": "1",
    }
    return source, package, page, lines, regions, metadata


def test_column_repair_changes_only_order_and_passes_actual_source_ink(tmp_path):
    source, package, page, lines, regions, metadata = _fixture(tmp_path)
    result = derive_column_order_repair(
        page, lines, regions, source, independent_metadata=metadata
    )
    assert result["ready"], result
    assert result["column_geometry"]["primary_line_order"] == [0, 2, 1, 3]
    assert result["quality_gate"]["outcome"] == "auto_accepted"
    assert result["unaccounted_ink_pixels"] == 0
    assert result["page_text_sha256"] != page["text_sha256"]
    assert sorted(result["column_geometry"]["primary_line_order"]) == list(range(4))
    assert page["ocr"]["lines"] == lines


def test_column_repair_preserves_hard_numeric_and_table_gates(tmp_path):
    source, package, page, lines, regions, metadata = _fixture(tmp_path)
    independent = deepcopy(lines)
    independent[0]["text"] = independent[0]["text"].replace("51", "52")
    with pytest.raises(ValueError, match="block_local"):
        column_geometry_consensus(page, independent, regions)
    held = derive_column_order_repair(
        page, lines, regions, source, independent_metadata=metadata, table_count=1
    )
    assert not held["ready"]
    bad = deepcopy(regions)
    bad[0]["kind"] = "table"
    with pytest.raises(ValueError, match="table_or_visual"):
        column_geometry_consensus(page, lines, bad)


def test_column_coverage_never_ignores_extra_visible_source_ink(tmp_path):
    source, package, page, lines, regions, metadata = _fixture(tmp_path, extra_ink=True)
    held = derive_column_order_repair(
        page, lines, regions, source, independent_metadata=metadata
    )
    assert (
        not held["ready"] and held["source_coverage"]["unaccounted_ink_pixels"] > 1000
    )


def test_atomic_column_repair_rebuilds_hashes_and_normal_batch_reproves_it(tmp_path):
    from local_vector_batch.pdf_repair import apply_column_order_repair
    from local_vector_batch.quality_gate import assess_package
    from local_vector_batch.evidence_package import verify_package

    source, package, page, lines, regions, metadata = _fixture(tmp_path)
    receipt = derive_column_order_repair(
        page, lines, regions, source, independent_metadata=metadata
    )
    result = apply_column_order_repair(package, receipt)
    assert (
        result["changed_pages"] == [1] and result["human_review_receipts_created"] == 0
    )
    assert verify_package(package, source_pdf=source)["integrity"] == "passed"
    assessed = assess_package(package, source, derive_ocr_source_raster=True)
    assert assessed["counts"]["auto_accepted"] == 1
    new = json.loads((package / "pages.jsonl").read_text())
    assert new["method"] == "vision" and new["status"] == "review_required"
    before = (package / "evidence-package.json").read_bytes()
    with pytest.raises(ValueError, match="changed"):
        apply_column_order_repair(package, receipt)
    assert (package / "evidence-package.json").read_bytes() == before


def test_geometric_section_bands_keep_copy_recipients_before_next_section():
    lines = []
    regions = []

    def add_region(ref, kind, box, values):
        regions.append(
            {
                "id": ref,
                "kind": kind,
                "bbox_normalized": box,
                "reading_order_index": 99 - len(regions),
            }
        )
        for text, y in values:
            lines.append(
                {"text": text, "bbox": [box[0] + 0.005, y, box[2] - 0.005, y + 0.015]}
            )

    add_region("to", "section_header", [0.1, 0.05, 0.2, 0.075], [("To:", 0.055)])
    add_region(
        "recipient",
        "text",
        [0.1, 0.1, 0.4, 0.17],
        [("Original recipient", 0.11), ("Original address", 0.14)],
    )
    add_region("copy", "section_header", [0.1, 0.3, 0.25, 0.325], [("Copy to:", 0.305)])
    add_region(
        "copy-left",
        "text",
        [0.1, 0.4, 0.4, 0.47],
        [("Left copy first", 0.41), ("Left copy second", 0.44)],
    )
    add_region(
        "copy-right",
        "text",
        [0.6, 0.4, 0.9, 0.47],
        [("Right copy first", 0.41), ("Right copy second", 0.44)],
    )
    add_region("cc", "section_header", [0.1, 0.6, 0.2, 0.625], [("CC:", 0.605)])
    add_region(
        "cc-left",
        "text",
        [0.1, 0.7, 0.4, 0.77],
        [("Later recipient", 0.71), ("Later address", 0.74)],
    )
    add_region(
        "cc-right",
        "text",
        [0.6, 0.7, 0.9, 0.77],
        [("Later note first", 0.71), ("Later note second", 0.74)],
    )
    lines.sort(key=lambda row: (row["bbox"][1], row["bbox"][0]))
    page = {"text": "\n".join(row["text"] for row in lines), "ocr": {"lines": lines}}
    result = column_geometry_consensus(page, deepcopy(lines), regions)
    assert result["proposed_text"].index("Right copy second") < result[
        "proposed_text"
    ].index("CC:")
    assert len(result["bands"]) == 3
    assert sorted(result["primary_line_order"]) == list(range(len(lines)))


def test_column_gate_rejects_tampered_region_provenance_or_line_identity(tmp_path):
    from local_vector_batch.quality_gate import assess_page

    source, package, page, lines, regions, metadata = _fixture(tmp_path)
    receipt = derive_column_order_repair(
        page, lines, regions, source, independent_metadata=metadata
    )
    proposed = receipt["proposed_page"]
    proof = deepcopy(proposed["column_order_proof"])
    proof["layout_regions"][0]["source_pdf_sha256"] = "0" * 64
    held = assess_page(
        proposed,
        source_pdf_sha256=receipt["source_pdf_sha256"],
        package_pdf_sha256=receipt["source_pdf_sha256"],
        independent_text=receipt["independent_text"],
        source_geometry=proof,
    )
    assert held["outcome"] == "repair_required"
    assert (
        held["components"]["source_render"]["reason"]
        == "column_line_identity_geometry_or_block_consensus_unproven"
    )
    dropped = deepcopy(page)
    dropped["text"] = "\n".join(x["text"] for x in lines[:-1])
    with pytest.raises(ValueError, match="preserve_original"):
        column_geometry_consensus(dropped, lines, regions)
