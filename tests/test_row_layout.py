from copy import deepcopy
import hashlib

import pytest

from local_vector_batch.row_layout import (
    label_value_geometry,
    row_consensus_metrics,
    row_layout_eligible,
)


def _page():
    lines = [{"text": "Definitions", "bbox": [0.1, 0.04, 0.4, 0.06]}]
    regions = [{"kind": "page_header", "bbox_normalized": [0.09, 0.03, 0.41, 0.07]}]
    for n, (term, value) in enumerate(
        (
            ("Alpha", "The share shall not be 51%."),
            ("Beta", "The period is 12 years."),
            ("Gamma", "The capacity is 20 MW."),
        )
    ):
        y = 0.2 + n * 0.2
        lines.extend(
            [
                {"text": f"{n+1})", "bbox": [0.1, y, 0.13, y + 0.02]},
                {"text": term, "bbox": [0.17, y, 0.4, y + 0.02]},
                {"text": value, "bbox": [0.55, y, 0.9, y + 0.02]},
            ]
        )
        regions.extend(
            [
                {"kind": "text", "bbox_normalized": [0.09, y - 0.005, 0.41, y + 0.025]},
                {"kind": "text", "bbox_normalized": [0.54, y - 0.005, 0.91, y + 0.025]},
            ]
        )
    lines.append({"text": "Page 2", "bbox": [0.7, 0.94, 0.85, 0.96]})
    regions.append({"kind": "page_footer", "bbox_normalized": [0.69, 0.93, 0.86, 0.97]})
    page = {
        "page": 2,
        "method": "vision",
        "ocr": {"lines": lines},
        "text": "\n".join(x["text"] for x in lines),
    }
    page["text_sha256"] = hashlib.sha256(page["text"].encode()).hexdigest()
    return page, regions


def _cells(page, geometry):
    cells = {
        f"row-{r['row']}-{kind}": {"text": r[kind]["text"]}
        for r in geometry["rows"]
        for kind in ("label", "value")
    }
    for kind in ("header", "footer"):
        cells[kind] = {
            "text": "\n".join(
                page["ocr"]["lines"][i]["text"]
                for i in geometry[kind + "_line_indexes"]
            )
        }
    return cells


def test_rows_pair_one_label_with_one_value_and_preserve_every_literal_line():
    page, regions = _page()
    geometry = label_value_geometry(page, regions)
    assert len(geometry["rows"]) == 3
    assert geometry["all_source_line_indexes"] == list(range(len(page["ocr"]["lines"])))
    for row in geometry["rows"]:
        for kind in ("label", "value"):
            for span in row[kind]["source_spans"]:
                assert (
                    page["text"][
                        span["char_start"] : span["char_start"] + span["char_length"]
                    ]
                    == span["quote"]
                )
    metrics = row_consensus_metrics(geometry, _cells(page, geometry), page)
    assert metrics["word_policy_pass"] and metrics["ordered_numbers_exact"]
    assert metrics["ordered_operators_exact"] and metrics["semantic_labels_exact"]


def test_numeric_operator_and_semantic_label_changes_are_separate_hard_failures():
    page, regions = _page()
    geometry = label_value_geometry(page, regions)
    cells = _cells(page, geometry)
    numeric = deepcopy(cells)
    numeric["row-0-value"]["text"] = numeric["row-0-value"]["text"].replace("51", "52")
    assert not row_consensus_metrics(geometry, numeric, page)["ordered_numbers_exact"]
    operator = deepcopy(cells)
    operator["row-0-value"]["text"] = operator["row-0-value"]["text"].replace(
        "shall not", "shall"
    )
    assert not row_consensus_metrics(geometry, operator, page)[
        "ordered_operators_exact"
    ]
    label = deepcopy(cells)
    label["row-1-label"]["text"] = "2) Another term"
    assert not row_consensus_metrics(geometry, label, page)["semantic_labels_exact"]


def test_crossing_gutter_or_misaligned_row_start_cannot_create_a_relation():
    page, regions = _page()
    bad = deepcopy(page)
    bad["ocr"]["lines"][3]["bbox"][0] = 0.3
    with pytest.raises(ValueError, match="crosses_label_value_gutter"):
        label_value_geometry(bad, regions)
    bad = deepcopy(page)
    bad["ocr"]["lines"][3]["bbox"][1] += 0.05
    bad["ocr"]["lines"][3]["bbox"][3] += 0.05
    with pytest.raises(ValueError, match="starts_not_aligned"):
        label_value_geometry(bad, regions)


def test_automatic_selector_requires_current_complete_independent_text_gates():
    page, regions = _page()
    quality = {
        "page": 2,
        "page_text_sha256": page["text_sha256"],
        "outcome": "repair_required",
        "page_complete": True,
        "components": {
            name: {"state": "pass"}
            for name in (
                "source_identity",
                "text_content",
                "language",
                "source_render",
                "words_and_order",
                "ordered_numbers",
                "ordered_operators",
            )
        },
    }
    quality["components"]["layout"] = {"state": "unknown"}
    assert row_layout_eligible(page, quality)
    assert not row_layout_eligible(
        {**page, "label_value_proof": {"ready": True}}, quality
    )
    assert not row_layout_eligible(page, {**quality, "page_text_sha256": "0" * 64})
    failed = deepcopy(quality)
    failed["components"]["ordered_numbers"]["state"] = "fail"
    assert not row_layout_eligible(page, failed)
