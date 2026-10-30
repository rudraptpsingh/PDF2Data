from local_vector_batch.local_vision_merge import (choose_crop_line, _independent_line,
                                                   _aligned_candidate, _table_cell_candidate,
                                                   _script_drift, candidate_line_indices)


def test_complete_line_agreement_preserves_selected_operative_clause():
    line = "in a time block is less than or equal to 400 MW, the charges for the"
    assert choose_crop_line(line, None, line, line) == {
        "choice": "selected", "reason": "complete_source_crop_agreement"}
    assert choose_crop_line(line, None, "400 MW", "400 MW")["choice"] is None
    assert choose_crop_line(line, None, line, "400 MW")["choice"] is None


def test_complete_line_agreement_can_nominate_missing_superscript():
    selected = "methodology specified in clause (2) of this regulation]:"
    independent = "methodology specified in clause (2) of this regulation]5:"
    assert choose_crop_line(selected, independent, independent, independent)["choice"] == "independent"
    assert choose_crop_line(selected, independent, independent, selected)["choice"] is None
    assert choose_crop_line("deciding the installed capacity for the next month.",
                            "deciding the installed capacity for the next month.]3",
                            "deciding the installed capacity for the next month. [3]",
                            "deciding the installed capacity for the next month.]3")["choice"] == "independent"
    assert choose_crop_line("deciding the installed capacity for the next month.",
                            "deciding the installed capacity for the next month.]3",
                            "deciding the installed capacity for the next month. [4]",
                            "deciding the installed capacity for the next month.]3")["choice"] is None


def test_ambiguous_alignment_and_partial_spans_abstain():
    line = "in a time block is less than or equal to 400 MW, the charges for the"
    assert _independent_line(line, [line, line]) is None
    assert _aligned_candidate(line, [line, line], [line]) is None
    assert choose_crop_line("section 62(1)(a), section 64 and 86",
                            "and 86", "and 86", "and 86")["choice"] is None


def test_dense_numeric_row_requires_table_structure_proof():
    lines = [{"text": "49.93", "bbox": [.1, .8, .2, .82]},
             {"text": "49.92", "bbox": [.35, .8, .45, .82]},
             {"text": "344.72", "bbox": [.7, .8, .8, .82]}]
    assert _table_cell_candidate(lines, 0)
    assert _table_cell_candidate(lines, 2)
    assert _table_cell_candidate(lines[:2], 0)
    assert not _table_cell_candidate(lines[:1], 0)


def test_hindi_crop_does_not_accept_mixed_indic_script():
    assert _script_drift("संवितरण अधिकारी", "संவிதरण अधिकारी")
    assert not _script_drift("संवितरण अधिकारी", "संवितरण अधिकारी")


def test_batch_line_selection_prioritizes_differing_numeric_clauses():
    page = {"ocr": {"lines": [
        {"text": "The authority is CERC", "bbox": [.1, .1, .9, .12]},
        {"text": "The limit shall be 400 MW in a time block", "bbox": [.1, .2, .9, .22]},
        {"text": "The schedule is final", "bbox": [.1, .3, .9, .32]},
    ]}}
    independent = "The authority is CERC\nThe limit shall be 40 MW in a time block\nThe schedule is final"
    assert candidate_line_indices(page, independent, limit=1) == [1]
    try:
        candidate_line_indices(page, independent, limit=9)
    except ValueError:
        pass
    else:
        assert False, "unbounded line review should fail"
