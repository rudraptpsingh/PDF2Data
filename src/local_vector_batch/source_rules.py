"""Strict geometric receipts for visible dotted header/footer rules.

This recognizes repeated compact solid marks, never masks arbitrary pixels,
changes the source-ink threshold, or treats text-like/stained marks as rules.
"""

from __future__ import annotations

import statistics


def continuous_footer_rule_fragment(ink, *, x: int, y: int, width: int,
                                    height: int, area: int, body_limit: int,
                                    policy: dict) -> bool:
    """Prove that a short uncovered stripe belongs to a long original footer rule.

    OCR boxes can mask part of a printed rule. Inspect the unmodified raster,
    rather than lowering the width threshold for arbitrary uncovered marks.
    """
    page_height, page_width = ink.shape
    if (y < body_limit or height != 1 or width < 1 or area != width or
            y <= 0 or y + 1 >= page_height):
        return False
    row = ink[y]
    if not row[x:x + width].all():
        return False
    left, right = x, x + width
    while left > 0 and row[left - 1]:
        left -= 1
    while right < page_width and row[right]:
        right += 1
    if (right - left < policy["footer_rule_min_width_fraction"] * page_width or
            (right - left) / height < policy["footer_rule_min_aspect"]):
        return False
    # A text underline or crossed out phrase has glyph ink immediately above
    # or below the stripe. A free standing printed footer rule does not.
    return not ink[y - 1, left:right].any() and not ink[y + 1, left:right].any()


def dotted_horizontal_rules(
    ink, stats, *, body_limit: int, table_count: int, policy: dict
):
    import cv2
    import numpy as np

    height, width = ink.shape
    candidates = []
    for index in range(1, len(stats)):
        x, y, w, h, area = [int(v) for v in stats[index]]
        if (
            not w
            or not h
            or 0.6 > w / h
            or w / h > 4
            or area / (w * h) < 0.7
            or h > policy["footer_rule_max_height_fraction"] * height
        ):
            continue
        # Hollow compact glyphs (0/O and similar letters) are not dots.
        white = (~ink[y : y + h, x : x + w]).astype("uint8")
        white_count, _, white_stats, _ = cv2.connectedComponentsWithStats(white, 8)
        if any(
            white_stats[i, 0] > 0
            and white_stats[i, 1] > 0
            and white_stats[i, 0] + white_stats[i, 2] < w
            and white_stats[i, 1] + white_stats[i, 3] < h
            for i in range(1, white_count)
        ):
            continue
        patch = ink[y : y + h, x : x + w].astype("int8")
        horizontal_runs = (np.diff(np.pad(patch, ((0, 0), (1, 1))), axis=1) == 1).sum(
            axis=1
        )
        vertical_runs = (np.diff(np.pad(patch, ((1, 1), (0, 0))), axis=0) == 1).sum(
            axis=0
        )
        if any(horizontal_runs > 1) or any(vertical_runs > 1):
            continue
        footer = y >= body_limit
        header = (
            table_count == 0
            and y + h <= policy["header_rule_max_bottom_fraction"] * height
        )
        if footer or header:
            candidates.append(
                (index, x, y, w, h, area, "footer" if footer else "header")
            )
    groups = []
    for candidate in sorted(candidates, key=lambda item: item[2] + item[4] / 2):
        center = candidate[2] + candidate[4] / 2
        matches = [
            group
            for group in groups
            if group[0][6] == candidate[6]
            and abs(statistics.median(item[2] + item[4] / 2 for item in group) - center)
            <= 1
        ]
        if len(matches) == 1:
            matches[0].append(candidate)
        else:
            groups.append([candidate])
    proven, indexes = [], set()
    for group in groups:
        if len(group) < 12:
            continue
        group.sort(key=lambda item: item[1])
        left = min(item[1] for item in group)
        right = max(item[1] + item[3] for item in group)
        top = min(item[2] for item in group)
        bottom = max(item[2] + item[4] for item in group)
        span, thickness = right - left, bottom - top
        if (
            span < policy["footer_rule_min_width_fraction"] * width
            or thickness > policy["footer_rule_max_height_fraction"] * height
            or span / max(1, thickness) < policy["footer_rule_min_aspect"]
        ):
            continue
        widths = [item[3] for item in group]
        heights = [item[4] for item in group]
        areas = [item[5] for item in group]
        if max(widths) / min(widths) > 1.5 or max(heights) / min(heights) > 1.5:
            continue
        if statistics.median(widths) < statistics.median(heights):
            continue
        if statistics.pstdev(areas) / statistics.mean(areas) > 0.25:
            continue
        centers = [item[1] + item[3] / 2 for item in group]
        steps = [b - a for a, b in zip(centers, centers[1:])]
        step = statistics.median(steps)
        if min(steps) <= max(widths) or any(
            abs(value - step) > max(1, 0.15 * step) for value in steps
        ):
            continue
        # Check the untouched original raster, including any OCR-covered ink.
        # A word, crossing mark or stain inside the stripe invalidates the rule.
        source_pixels = int(ink[top:bottom, left:right].sum())
        if source_pixels != sum(areas):
            continue
        proven.append(
            {
                "classification": group[0][6] + "_dotted_horizontal_rule",
                "bbox_pixels": [left, top, right, bottom],
                "bbox_normalized": [
                    round(left / width, 6),
                    round(top / height, 6),
                    round(right / width, 6),
                    round(bottom / height, 6),
                ],
                "ink_pixels": source_pixels,
                "component_count": len(group),
                "component_boxes": [
                    [x, y, x + w, y + h] for _, x, y, w, h, _, _ in group
                ],
                "component_ink_pixels": areas,
                "median_spacing_pixels": step,
                "method": "uniform_compact_collinear_source_components_v1",
            }
        )
        indexes.update(item[0] for item in group)
    return indexes, proven
