"""Measure machine extraction decisions against source-checked examples.

Unlabelled pages are never counted as correct, and an acceptance precision is
not an estimate of every word's accuracy. Source/page/text hashes prevent an
old review from silently grading a changed extraction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re


SHA256 = re.compile(r"[0-9a-f]{64}")


def _key(row: dict) -> tuple[str, int, str]:
    source, page, text = (row.get("source_pdf_sha256"), row.get("page"),
                          row.get("page_text_sha256"))
    if (not isinstance(source, str) or not SHA256.fullmatch(source) or
            type(page) is not int or page < 1 or
            not isinstance(text, str) or not SHA256.fullmatch(text)):
        raise ValueError("Calibration needs source SHA, physical page and extraction text SHA")
    return source, page, text


def _metrics(counts: Counter) -> dict:
    tp, fp = counts["correct_accepts"], counts["false_accepts"]
    fn, tn = counts["false_holds"], counts["correct_holds"]
    accepted, positive = tp + fp, tp + fn
    # Wilson lower bound: zero errors in two examples is not proof of reliability.
    lower = None
    if accepted:
        z = 1.959963984540054
        p = tp / accepted
        lower = ((p + z*z/(2*accepted) - z*math.sqrt(
            p*(1-p)/accepted + z*z/(4*accepted*accepted))) / (1+z*z/accepted))
    return {**{name: counts[name] for name in ("correct_accepts", "false_accepts",
                                              "false_holds", "correct_holds")},
            "labelled_pages": tp+fp+fn+tn,
            "acceptance_precision": tp/accepted if accepted else None,
            "acceptance_precision_lower_95": lower,
            "acceptable_page_recall": tp/positive if positive else None}


def evaluate_calibration(assessments: list[dict], references: list[dict]) -> dict:
    """Evaluate one frozen policy on disjoint calibration/holdout references.

    Each reference supplies ``acceptable`` (bool), ``split`` (calibration or
    holdout), optional ``category``, and a source-visual evidence SHA. It labels
    the original extraction version, not a subsequently corrected transcription.
    """
    known, source_splits = {}, {}
    for reference in references:
        key = _key(reference)
        if key in known:
            raise ValueError("Duplicate calibration reference, including across splits")
        if (type(reference.get("acceptable")) is not bool or
                reference.get("split") not in {"calibration", "holdout"} or
                not isinstance(reference.get("visual_evidence_sha256"), str) or
                not SHA256.fullmatch(reference["visual_evidence_sha256"])):
            raise ValueError("Reference needs a source-visual label and explicit evaluation split")
        if key[0] in source_splits and source_splits[key[0]] != reference["split"]:
            raise ValueError("Pages of the same PDF cannot cross calibration and holdout splits")
        source_splits[key[0]] = reference["split"]
        known[key] = reference
    seen, counts, groups, unlabelled = set(), Counter(), defaultdict(Counter), 0
    failures = []
    for assessment in assessments:
        key = _key(assessment)
        if key in seen:
            raise ValueError("Duplicate quality assessment")
        seen.add(key)
        if assessment.get("outcome") not in {"auto_accepted", "repair_required", "source_blocked"}:
            raise ValueError("Unknown machine extraction outcome")
        reference = known.get(key)
        if reference is None:
            unlabelled += 1
            continue
        accepts = assessment["outcome"] == "auto_accepted"
        label = ("correct_accepts" if accepts else "false_holds") if reference["acceptable"] else (
            "false_accepts" if accepts else "correct_holds")
        counts[label] += 1
        groups[(reference["split"], reference.get("category", "unspecified"))][label] += 1
        if label == "false_accepts":
            failures.append({"source_pdf_sha256": key[0], "page": key[1],
                             "page_text_sha256": key[2], "category": reference.get("category")})
    return {"schema_version": 1, **_metrics(counts),
            "unlabelled_assessments": unlabelled,
            "references_not_assessed": len(known.keys() - seen),
            "holdout_labelled_pages": sum(sum(group.values()) for (split, _), group in groups.items()
                                          if split == "holdout"),
            "by_split_and_category": [
                {"split": split, "category": category, **_metrics(group)}
                for (split, category), group in sorted(groups.items())],
            "false_accept_pages": failures,
            "interpretation": "Page acceptance precision on labelled extraction versions; not corpus-wide OCR accuracy."}
