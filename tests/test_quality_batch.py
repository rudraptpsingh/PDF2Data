import hashlib
import json

import pytest

from local_vector_batch.quality_batch import bound_independent, assess_batch


def test_empty_manifest_does_not_leave_old_summary_looking_current(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("")
    with pytest.raises(ValueError, match="empty"):
        assess_batch(manifest, tmp_path / "quality")


def test_stale_independent_page_is_never_used_to_accept_new_text():
    text = "A source value of 51%."
    row = {"source_pdf_sha256": "a"*64, "page": 1, "selected_page_text_sha256": "b"*64,
           "independent": {"engine": "independent", "text": text,
                           "text_sha256": hashlib.sha256(text.encode()).hexdigest()}}
    texts, _, stale = bound_independent([row], {1: hashlib.sha256(text.encode()).hexdigest()}, "a"*64)
    assert texts == {}
    assert stale == 1


def test_current_cache_can_coexist_with_old_extraction_and_different_selected_engine():
    text = "51%"
    digest = hashlib.sha256(text.encode()).hexdigest()
    current = {"source_pdf_sha256": "a"*64, "page": 1, "selected_page_text_sha256": digest,
               "selected": {"engine": "rapidocr-en"}, "independent": {
                   "engine": "vision", "text": text, "text_sha256": digest}}
    texts, metadata, stale = bound_independent([{**current, "selected_page_text_sha256": "b"*64}, current],
                                              {1: digest}, "a"*64)
    assert texts == {1: text}
    assert metadata[1]["engine"] == "vision"
    assert stale == 1
