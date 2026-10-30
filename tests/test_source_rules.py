import numpy as np
import cv2

from local_vector_batch.quality_gate import load_policy
from local_vector_batch.source_rules import (
    dotted_horizontal_rules, continuous_footer_rule_fragment,
)


def _classify(ink, **kwargs):
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype("uint8"), 8)
    return dotted_horizontal_rules(
        ink, stats, body_limit=400, table_count=0, policy=load_policy(), **kwargs
    )


def test_real_source_components_can_prove_uniform_dotted_header_rule():
    ink = np.zeros((500, 600), dtype=bool)
    for x in range(60, 541, 12):
        ink[20:23, x : x + 3] = True
    indexes, rules = _classify(ink)
    assert len(rules) == 1 and len(indexes) == 41
    assert rules[0]["classification"] == "header_dotted_horizontal_rule"
    assert rules[0]["ink_pixels"] == int(ink.sum())
    assert rules[0]["component_boxes"][0] == [60, 20, 63, 23]


def test_textlike_irregular_or_crossing_marks_are_never_dotted_rules():
    for variant in ("stain", "irregular_spacing", "hollow_letters", "body_rule"):
        ink = np.zeros((500, 600), dtype=bool)
        for n, x in enumerate(range(60, 541, 12)):
            y = 200 if variant == "body_rule" else 20
            if variant == "irregular_spacing" and n % 3 == 0:
                x += 5
            ink[y : y + 3, x : x + 3] = True
            if variant == "hollow_letters":
                ink[y + 1, x + 1] = False
        if variant == "stain":
            ink[18:30, 250:270] = True
        indexes, rules = _classify(ink)
        assert not indexes and not rules


def test_uncovered_footer_stripe_requires_long_clean_original_rule():
    ink = np.zeros((500, 600), dtype=bool)
    ink[460, 50:400] = True
    args = dict(x=300, y=460, width=100, height=1, area=100,
                body_limit=420, policy=load_policy())
    assert continuous_footer_rule_fragment(ink, **args)
    ink[459, 200] = True
    assert not continuous_footer_rule_fragment(ink, **args)
    ink[459, 200] = False
    assert not continuous_footer_rule_fragment(ink, **{**args, "y": 400})
    ink[460, :270] = False
    assert not continuous_footer_rule_fragment(ink, **args)
