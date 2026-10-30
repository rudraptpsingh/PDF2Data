import pytest

from local_vector_batch.quality_calibration import evaluate_calibration


def row(page=1, **extra):
    return {"source_pdf_sha256": "a"*64, "page": page,
            "page_text_sha256": "b"*64, **extra}


def reference(page=1, **extra):
    return row(page, acceptable=True, split="holdout", category="prose",
               visual_evidence_sha256="c"*64, **extra)


def test_false_accepts_are_visible_and_missing_labels_are_not_passes():
    refs = [reference(), {**reference(2), "acceptable": False}]
    assessments = [row(i, outcome="auto_accepted") for i in (1, 2, 3)]
    report = evaluate_calibration(assessments, refs)
    assert report["acceptance_precision"] == .5
    assert report["false_accepts"] == 1
    assert report["labelled_pages"] == 2
    assert report["unlabelled_assessments"] == 1
    assert report["false_accept_pages"][0]["page"] == 2


def test_changed_extraction_is_not_graded_by_old_reference():
    report = evaluate_calibration([row(outcome="auto_accepted", page_text_sha256="d"*64)],
                                  [reference()])
    assert report["acceptance_precision"] is None
    assert report["references_not_assessed"] == 1


def test_no_errors_in_tiny_sample_does_not_claim_certainty():
    report = evaluate_calibration([row(outcome="auto_accepted")], [reference()])
    assert report["acceptance_precision"] == 1
    assert report["acceptance_precision_lower_95"] < .3


def test_split_duplicates_and_missing_visual_evidence_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate_calibration([], [reference(), {**reference(), "split": "calibration"}])
    with pytest.raises(ValueError, match="source-visual"):
        evaluate_calibration([], [{**reference(), "visual_evidence_sha256": None}])


def test_all_held_exposes_zero_acceptance_and_false_hold_rate():
    report = evaluate_calibration([row(outcome="repair_required")], [reference()])
    assert report["acceptance_precision"] is None
    assert report["false_holds"] == 1
    assert report["acceptable_page_recall"] == 0


def test_holdout_is_separated_by_source_pdf_to_avoid_document_leakage():
    with pytest.raises(ValueError, match="same PDF"):
        evaluate_calibration([], [reference(), {**reference(2), "split": "calibration"}])
