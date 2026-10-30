from copy import deepcopy
from hashlib import sha256

import pytest

from local_vector_batch import table_grid


def _case(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-bound-pdf")
    monkeypatch.setattr(table_grid, "_render", lambda *_: ("render-sha", (612, 792)))
    lines = [
        ("Discipline", [0.30, 0.71, 0.4, 0.74]),
        ("Qualifications and Experience", [0.60, 0.71, 0.85, 0.74]),
        ("(a) Power operations", [0.24, 0.75, 0.55, 0.78]),
        ("Degree with 10 years", [0.60, 0.75, 0.86, 0.79]),
        ("(b) Finance", [0.24, 0.84, 0.55, 0.86]),
        ("MBA with 5 years", [0.60, 0.84, 0.86, 0.90]),
    ]
    page = {"page": 1, "text": "\n".join(text for text, _ in lines),
            "ocr": {"lines": [{"text": text, "bbox": box} for text, box in lines]}}
    page["text_sha256"] = sha256(page["text"].encode()).hexdigest()
    cells = [["", "Discipline", "Qualifications and Experience"],
             ["(a)", "Power operations", "Degree with 10 years"],
             ["(b)", "Finance", "MBA with 5 years"]]
    boxes = [[None, [183, 562, 250, 585], [365, 562, 530, 585]],
             [[145, 590, 177, 615], [180, 590, 350, 635], [365, 590, 530, 635]],
             [[145, 660, 177, 681], [180, 660, 350, 690], [365, 660, 545, 715]]]
    spans = [{"row": row, "column": col, "row_span": 1, "column_span": 1,
              "text": text, "bbox": boxes[row][col]}
             for row, values in enumerate(cells) for col, text in enumerate(values) if text]
    table = {"id": "table-1", "page": 1, "cells": cells, "cell_spans": spans,
             "bbox": [140, 560, 550, 716], "source_pdf_sha256": sha256(source.read_bytes()).hexdigest(),
             "page_text_sha256": page["text_sha256"],
             "rendered_image_sha256": "render-sha",
             "extraction_basis": "docling_tableformer_candidate"}
    return source, page, table


def test_source_bound_grid_rechecks_each_row_and_numeric_value(tmp_path, monkeypatch):
    source, page, table = _case(tmp_path, monkeypatch)
    proof = table_grid.derive_table_grid_proof(page, table, source)
    assert proof["populated_cells"] == 8
    assert table_grid.validate_table_grid_proof(page, proof, source)
    wrong = deepcopy(proof)
    wrong["table"]["cells"][1][2] = "Degree with 11 years"
    assert not table_grid.validate_table_grid_proof(page, wrong, source)


def test_source_bound_grid_rejects_stale_or_crossed_cells(tmp_path, monkeypatch):
    source, page, table = _case(tmp_path, monkeypatch)
    changed = deepcopy(table)
    changed["page_text_sha256"] = "old"
    with pytest.raises(ValueError, match="identity"):
        table_grid.derive_table_grid_proof(page, changed, source)
    changed = deepcopy(table)
    changed["cell_spans"][4]["bbox"] = [180, 680, 350, 700]
    with pytest.raises(ValueError, match="rows cross"):
        table_grid.derive_table_grid_proof(page, changed, source)
