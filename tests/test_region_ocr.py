import pytest
from copy import deepcopy

from local_vector_batch.region_ocr import (_clusters, _region_window,
    agreed_label_literal, linked_footnote_marker, candidate_footnote_pairs,
    corrected_footnote_text, source_bound_line_words)
from local_vector_batch.region_ocr import _missing_line_anchors, compose_complete_line_proof
from local_vector_batch.region_ocr import _canonical_quotes
from local_vector_batch.region_ocr import compose_source_bound_word_line
from local_vector_batch.footnote_repair import same_source_crop_marker


def test_crop_reader_quote_typography_equivalence_is_narrow():
    assert _canonical_quotes("Commission’s office") == "Commission's office"
    assert _canonical_quotes('“Rule 3”') == '"Rule 3"'
    assert _canonical_quotes('and') != _canonical_quotes('aad')
    assert _canonical_quotes('31') != _canonical_quotes('3l')


def test_line_word_proof_rejects_unbounded_crop_expansion():
    page = {"ocr": {"lines": [{"text": "one line", "bbox": [0, 0, .5, .1]}]}}
    with pytest.raises(ValueError, match="invalid source line"):
        source_bound_line_words(page, "unused.pdf", 0, "one line", horizontal_padding=.03)


def test_source_bound_word_line_rejoins_split_enumerator_only_at_unique_span():
    proof = {"independent_line": "(2) CEA shall prepare",
             "independent_span": ["(2)", "CEA shall prepare"],
             "proposed_line": "(2) CEA shall prepare"}
    assert compose_source_bound_word_line("before\n(2)\nCEA shall prepare\nafter", proof) == (
        "before\n(2) CEA shall prepare\nafter")
    with pytest.raises(ValueError, match="changed or is ambiguous"):
        compose_source_bound_word_line("(2)\nCEA shall prepare\n(2)\nCEA shall prepare", proof)
    with pytest.raises(ValueError, match="changed or is ambiguous"):
        compose_source_bound_word_line("(3)\nCEA shall prepare", proof)


def test_footnote_recheck_ignores_clipped_context_but_rejects_changed_marker_or_pixels():
    recorded = {"status": "candidate", "marker": "19", "regions": {
        role: {"source_image_sha256": role + "sha", "rapidocr_lines": [{"text": "19"}],
               "vlm_text": "e petitions.]¹⁹"} for role in ("body", "note")}}
    fresh = deepcopy(recorded)
    fresh["regions"]["body"]["vlm_text"] = "he petitions.]¹⁹"
    assert same_source_crop_marker(fresh, recorded)
    fresh["marker"] = "18"
    assert not same_source_crop_marker(fresh, recorded)
    fresh["marker"] = "19"
    fresh["regions"]["note"]["source_image_sha256"] = "changed"
    assert not same_source_crop_marker(fresh, recorded)


def test_adjacent_glyphs_group_without_swallowing_other_lines_or_footer():
    regions = [
        {"bbox_pixels": [218, 279, 225, 288], "location": "body"},
        {"bbox_pixels": [227, 279, 234, 288], "location": "body"},
        {"bbox_pixels": [240, 279, 242, 288], "location": "body"},
        {"bbox_pixels": [500, 279, 510, 288], "location": "body"},
        {"bbox_pixels": [218, 320, 225, 329], "location": "body"},
        {"bbox_pixels": [218, 1190, 225, 1199], "location": "footer_visual"},
    ]
    assert _clusters(regions) == [
        [218, 279, 242, 288],
        [500, 279, 510, 288],
        [218, 320, 225, 329],
    ]


def test_region_work_budget_defers_and_resumes_without_dropping_regions():
    groups = [[i, 0, i + 1, 10] for i in range(11)]
    first, cursor = _region_window(groups, 4, 0)
    second, cursor = _region_window(groups, 4, cursor)
    third, cursor = _region_window(groups, 4, cursor)
    assert [*first, *second, *third] == list(range(11))
    assert cursor == 11
    with pytest.raises(ValueError):
        _region_window(groups, 0, 0)


def test_short_structural_label_preserves_exact_case_majority():
    assert agreed_label_literal("C.", "C.", "c. The average peak demand") == "C."
    assert agreed_label_literal("C.", "c.", "c. The average peak demand") == "c."
    assert agreed_label_literal("C.", "C.", "b. Other") == "C."
    assert agreed_label_literal("C.", "c_", "c. The average") is None


def test_linked_superscript_marker_requires_anchor_note_and_independent_source():
    independent = "Liability Partnership Act, 2008.]5\n(2) Technical Qualifications"
    assert linked_footnote_marker("Act, 2008.]⁵", "5 Added vide First Amendment",
                                  "5", independent) == "5"
    assert linked_footnote_marker("Act, 2008.]⁵", "6 Added vide First Amendment",
                                  "5", independent) is None
    assert linked_footnote_marker("Act, 2008.]⁵", "5 Added vide First Amendment",
                                  "5", "Act, 2008.]J") is None
    # A handwritten signature date or a stray digit is not a footnote anchor.
    assert linked_footnote_marker("Shubha Sarma 9/12", "5 Added", "5", independent) is None


def test_footnote_pair_discovery_and_composite_text_preserve_other_lines():
    page = {"ocr": {"lines": [
        {"text": "Section 2", "bbox": [0.1, 0.1, 0.5, 0.12]},
        {"text": "Liability Partnership Act, 2008.J", "bbox": [0.2, 0.5, 0.6, 0.52]},
        {"text": "(2) Technical Qualifications", "bbox": [0.2, 0.6, 0.6, 0.62]},
        {"text": "$ Added vide First Amendment", "bbox": [0.1, 0.92, 0.7, 0.94]},
    ]}}
    assert candidate_footnote_pairs(page) == [(1, 3)]
    link = {"body_line_index": 1, "note_line_index": 3, "marker": "5",
            "body_selected_text": page["ocr"]["lines"][1]["text"],
            "note_selected_text": page["ocr"]["lines"][3]["text"]}
    independent = "Section 2\nLiability Partnership Act, 2008.]5\n(2) Technical Qualifications\nAdded vide First Amendment"
    lines, second = corrected_footnote_text(page, independent, [link])
    assert lines[0] == page["ocr"]["lines"][0]
    assert lines[2] == page["ocr"]["lines"][2]
    assert lines[1]["text"] == "Liability Partnership Act, 2008.]5"
    assert lines[3]["text"] == "5 Added vide First Amendment"
    assert second.endswith("5 Added vide First Amendment")
    with pytest.raises(ValueError, match="overlap|duplicate"):
        corrected_footnote_text(page, independent, [link, link])
    stale = {**link, "body_selected_text": "different source line"}
    with pytest.raises(ValueError, match="changed"):
        corrected_footnote_text(page, independent, [stale])


def test_handwritten_signature_does_not_become_a_footnote_candidate():
    page = {"ocr": {"lines": [
        {"text": "Dated the 9th December, 2016.", "bbox": [0.1, 0.3, 0.6, 0.32]},
        {"text": "(Shubha Sarma)", "bbox": [0.2, 0.91, 0.5, 0.93]},
    ]}}
    assert candidate_footnote_pairs(page) == []


def test_missing_terminal_superscript_requires_exact_independent_body_and_note():
    page = {"ocr": {"lines": [
        {"text": "The application uses [short-term open access]", "bbox": [0.1, 0.4, 0.8, 0.42]},
        {"text": "31 Substituted vide Amendment Regulations", "bbox": [0.1, 0.91, 0.8, 0.93]},
    ]}}
    assert candidate_footnote_pairs(page) == [(0, 1)]
    link = {"body_line_index": 0, "note_line_index": 1, "marker": "31",
            "body_selected_text": page["ocr"]["lines"][0]["text"],
            "note_selected_text": page["ocr"]["lines"][1]["text"]}
    independent = "The application uses [short-term open access]31\n31 Substituted vide Amendment Regulations"
    lines, second = corrected_footnote_text(page, independent, [link])
    assert lines[0]["text"].endswith("]31")
    assert second == independent
    with pytest.raises(ValueError, match="absent or ambiguous"):
        corrected_footnote_text(page, independent.replace("access]31", "access]32"), [link])


def test_independent_nominates_entirely_missing_terminal_marker_without_body_rewrite():
    page = {"ocr": {"lines": [
        {"text": "deciding the installed capacity for the next month.", "bbox": [0.1, 0.08, 0.8, 0.1]},
        {"text": "Other operative text stays intact", "bbox": [0.1, 0.5, 0.8, 0.52]},
        {"text": "3 Added vide Third Amendment", "bbox": [0.1, 0.91, 0.8, 0.93]},
    ]}}
    independent = ("deciding the installed capacity for the next month.]3\n"
                   "Other operative text stays intact\n3 Added vide Third Amendment")
    assert candidate_footnote_pairs(page) == []
    assert candidate_footnote_pairs(page, independent) == [(0, 2)]
    link = {"body_line_index": 0, "note_line_index": 2, "marker": "3",
            "body_selected_text": page["ocr"]["lines"][0]["text"],
            "note_selected_text": page["ocr"]["lines"][2]["text"]}
    lines, second = corrected_footnote_text(page, independent, [link])
    assert lines[0]["text"].endswith("month.]3")
    assert lines[1] == page["ocr"]["lines"][1]
    assert second == independent
    assert candidate_footnote_pairs(page, independent.replace("next month.", "last month.")) == []


def test_independent_nominates_marker_inside_terminal_punctuation():
    page = {"ocr": {"lines": [
        {"text": "methodology specified in clause (2) of this regulation]:", "bbox": [0.1, 0.39, 0.8, 0.41]},
        {"text": "$ Substituted vide Second Amendment", "bbox": [0.1, 0.92, 0.8, 0.94]},
    ]}}
    independent = ("methodology specified in clause (2) of this regulation]5:\n"
                   "5 Substituted vide Second Amendment")
    assert candidate_footnote_pairs(page, independent) == [(0, 1)]
    link = {"body_line_index": 0, "note_line_index": 1, "marker": "5",
            "body_selected_text": page["ocr"]["lines"][0]["text"],
            "note_selected_text": page["ocr"]["lines"][1]["text"]}
    lines, second = corrected_footnote_text(page, independent, [link])
    assert [line["text"] for line in lines] == independent.splitlines()
    assert second == independent
    assert candidate_footnote_pairs(page, independent.replace("regulation", "regulations")) == []
    assert linked_footnote_marker("s regulation]⁵:", "5 Substituted vide Second Amend",
                                  "5 Substituted vide Second Amend", independent) == "5"


def test_standalone_body_marker_links_to_numbered_note_without_synthesizing_independent_text():
    page = {"ocr": {"lines": [
        {"text": "[ ]°7", "bbox": [0.18, 0.08, 0.22, 0.10]},
        {"text": "Body provision", "bbox": [0.18, 0.30, 0.50, 0.32]},
        {"text": "$ Deleted vide Third Amendment", "bbox": [0.13, 0.92, 0.50, 0.94]},
    ]}}
    assert candidate_footnote_pairs(page) == [(0, 2)]
    link = {"body_line_index": 0, "note_line_index": 2, "marker": "67",
            "body_selected_text": "[ ]°7", "note_selected_text": "$ Deleted vide Third Amendment"}
    independent = "[]67\nBody provision\n67 Deleted vide Third Amendment"
    lines, second = corrected_footnote_text(page, independent, [link])
    assert [line["text"] for line in lines] == [
        "[]67", "Body provision", "67 Deleted vide Third Amendment"]
    assert second == independent


def test_split_independent_note_marker_is_merged_once():
    page = {"ocr": {"lines": [
        {"text": "A sufficiently long clause.]s", "bbox": [0.2, 0.6, 0.8, 0.62]},
        {"text": "$ Substituted vide Third Amendment", "bbox": [0.1, 0.92, 0.8, 0.94]},
    ]}}
    link = {"body_line_index": 0, "note_line_index": 1, "marker": "8",
            "body_selected_text": page["ocr"]["lines"][0]["text"],
            "note_selected_text": page["ocr"]["lines"][1]["text"]}
    lines, independent = corrected_footnote_text(page,
        "A sufficiently long clause.]8\n8\nSubstituted vide Third Amendment", [link])
    assert independent == "A sufficiently long clause.]8\n8 Substituted vide Third Amendment"
    assert lines[1]["text"] == "8 Substituted vide Third Amendment"


def test_complete_line_omission_requires_long_unique_context_and_stale_guard():
    before = "To undertake planning for maintaining proper voltages through review of"
    after = "monitoring of installed capacitors."
    independent = ["(g)", "To undertake planning for maintaining proper voltages through review of", after]
    assert _missing_line_anchors(independent, before="(g) " + before, after=after) == [1]
    assert _missing_line_anchors(["and 86", after], "and 86", after) == []
    proof = {"line_index": 1, "old_line": "missing clause", "proposed_line": "complete clause",
             "independent_mode": "insert", "independent_anchor": 1,
             "neighbor_before": "(g) " + before, "neighbor_after": after}
    lines = [{"text": "(g) " + before}, {"text": "missing clause"}, {"text": after}]
    composed, text = compose_complete_line_proof(lines, "\n".join(independent), proof)
    assert text.splitlines()[2] == "complete clause"
    assert composed[1]["text"] == "complete clause"
    with pytest.raises(ValueError, match="neighbors"):
        compose_complete_line_proof([{"text": "(g) " + before}, {"text": "missing clause"},
                                     {"text": after}], "changed\n" + after, proof)


def test_grouped_complete_line_proofs_relocate_after_verified_insertion():
    before = "A long preceding source line with enough exact context"
    after = "following source words"
    lines = [{"text": before}, {"text": "omitted"}, {"text": after},
             {"text": "¡i)"}, {"text": "full later clause"}]
    omission = {"line_index": 1, "old_line": "omitted", "proposed_line": "omitted",
                "independent_mode": "insert", "independent_anchor": 0,
                "neighbor_before": before, "neighbor_after": after}
    roman = {"line_index": 3, "old_line": "¡i)", "proposed_line": "iii)",
             "independent_mode": "prefix", "independent_anchor": 2,
             "neighbor_before": after, "neighbor_after": "full later clause"}
    lines, independent = compose_complete_line_proof(lines,
        before + "\n" + after + "\niii) full later clause", omission)
    lines, independent = compose_complete_line_proof(lines, independent, roman)
    assert lines[3]["text"] == "iii)"
    assert independent.splitlines() == [before, "omitted", after, "iii) full later clause"]


def test_complete_numeric_enumerator_requires_exact_standalone_context():
    lines = [{"text": "prior clause"}, {"text": "(Z)"},
             {"text": "The notice may be served at the registered office"}]
    proof = {"line_index": 1, "old_line": "(Z)", "proposed_line": "(2)",
             "independent_mode": "replace", "independent_anchor": 1,
             "neighbor_after": lines[2]["text"]}
    corrected, independent = compose_complete_line_proof(
        lines, "prior clause\n(2)\nThe notice may be served at the registered office", proof)
    assert corrected[1]["text"] == "(2)"
    assert independent.splitlines()[1] == "(2)"
    with pytest.raises(ValueError, match="independent complete line changed"):
        compose_complete_line_proof(
            [{"text": "prior clause"}, {"text": "(Z)"}, {"text": lines[2]["text"]}],
            "prior clause\n(2)\n(2)\nThe notice may be served at the registered office", proof)


def test_source_proven_footer_replaces_only_isolated_last_line():
    proof = {"line_index": 1, "old_line": "30", "proposed_line": "30",
             "independent_mode": "footer_replace", "independent_anchor": 1}
    lines = [{"text": "Complete regulation text", "bbox": [.1, .2, .8, .3]},
             {"text": "30", "bbox": [.86, .94, .89, .96]}]
    fixed, independent = compose_complete_line_proof(lines, "Complete regulation text\n0ε", proof)
    assert fixed[-1]["text"] == "30" and independent.endswith("\n30")
    with pytest.raises(ValueError, match="footer source anchor"):
        compose_complete_line_proof(lines, "Complete regulation text\n0ε\nextra", proof)
    with pytest.raises(ValueError, match="footer source anchor"):
        compose_complete_line_proof([lines[0], {"text": "30", "bbox": [.86, .8, .89, .82]}],
                                    "Complete regulation text\n0ε", proof)


def test_source_line_word_proof_refuses_numeric_and_logical_changes_before_render(tmp_path):
    page = {"page": 1, "ocr": {"lines": [{"text": "The charge is 10 MW.",
                                           "bbox": [0.1, 0.1, 0.9, 0.2]}]}}
    with pytest.raises(ValueError, match="numeric or logical"):
        source_bound_line_words(page, tmp_path / "missing.pdf", 0, "The charge is 20 MW.")
    page["ocr"]["lines"][0]["text"] = "The applicant must not pay."
    with pytest.raises(ValueError, match="numeric or logical"):
        source_bound_line_words(page, tmp_path / "missing.pdf", 0, "The applicant must and pay.")


def test_footnote_stage_rejects_authoritative_corpus_tree_before_any_write(tmp_path):
    from local_vector_batch.footnote_repair import apply_footnote_marker_repair
    authoritative = tmp_path / "pdf-backfill" / "extracted" / "source-hash"
    authoritative.mkdir(parents=True)
    marker = authoritative / "preserve.txt"
    marker.write_text("unchanged")
    with pytest.raises(ValueError, match="authoritative extraction"):
        apply_footnote_marker_repair(authoritative, [{"page": 1}], tmp_path / "proof")
    assert marker.read_text() == "unchanged"


def test_multiple_marker_pairs_stay_distinct():
    page = {"ocr": {"lines": [
        {"text": "Long regulation clause, 2008.J", "bbox": [0.1, 0.4, 0.6, 0.42]},
        {"text": "Another regulation clause, 2012.$", "bbox": [0.1, 0.6, 0.6, 0.62]},
        {"text": "$ First amendment", "bbox": [0.1, 0.91, 0.5, 0.93]},
        {"text": "$ Second amendment", "bbox": [0.1, 0.95, 0.5, 0.97]},
    ]}}
    links = [
        {"body_line_index": 0, "note_line_index": 2, "marker": "5",
         "body_selected_text": page["ocr"]["lines"][0]["text"],
         "note_selected_text": page["ocr"]["lines"][2]["text"]},
        {"body_line_index": 1, "note_line_index": 3, "marker": "6",
         "body_selected_text": page["ocr"]["lines"][1]["text"],
         "note_selected_text": page["ocr"]["lines"][3]["text"]},
    ]
    independent = ("Long regulation clause, 2008.]5\nAnother regulation clause, 2012.]6\n"
                   "First amendment\nSecond amendment")
    lines, second = corrected_footnote_text(page, independent, links)
    assert [lines[i]["text"] for i in (0, 1, 2, 3)] == [
        "Long regulation clause, 2008.]5", "Another regulation clause, 2012.]6",
        "5 First amendment", "6 Second amendment"]
    assert second.endswith("5 First amendment\n6 Second amendment")
