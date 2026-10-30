from local_vector_batch.quality_routes import repair_patterns


def test_empty_text_never_becomes_confirmed_blank_by_routing():
    result = repair_patterns([{"outcome": "repair_required", "components": {
        "text_content": {"state": "fail", "reason": "no_searchable_text"}}}])
    assert result["primary_counts"] == {"empty_text": 1}
    assert result["empty_text_is_confirmed_blank"] is False


def test_language_and_numbers_are_overlapping_but_primary_counts_are_not():
    result = repair_patterns([{"outcome": "repair_required", "components": {
        "language": {"state": "fail"}, "ordered_numbers": {"state": "fail"}}}])
    assert sum(result["primary_counts"].values()) == 1
    assert result["overlapping_pattern_counts"]["language_coverage"] == 1
    assert result["overlapping_pattern_counts"]["numbers_or_logic"] == 1


def test_agreed_text_routes_to_layout_work_without_repeating_ocr():
    components = {name: {"state": "pass"} for name in (
        "language", "words_and_order", "ordered_numbers", "ordered_operators")}
    result = repair_patterns([{"outcome": "repair_required", "components": {
        **components, "layout": {"state": "unknown"}}}, {"outcome": "auto_accepted"}])
    assert result["primary_counts"] == {"verified_text_visual_gap": 1}
    assert result["near_pass_text_pages"] == 1
