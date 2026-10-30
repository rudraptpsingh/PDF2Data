import json
import pytest
from local_vector_batch.repair_commit import merge_page_rows, _check_original


def test_multiple_same_pdf_replacements_preserve_every_untargeted_row():
    current = {1: [{"text": "old1"}], 2: [{"text": "keep2"}], 3: [{"text": "old3"}]}
    replacement = {1: [{"text": "new1"}], 3: [{"text": "new3"}]}
    assert merge_page_rows(current, replacement) == [{"text": "new1"}, {"text": "keep2"}, {"text": "new3"}]
    assert current[1][0]["text"] == "old1"


def test_commit_cas_rejects_stale_page_and_source_review(tmp_path, monkeypatch):
    import local_vector_batch.repair_commit as commit
    monkeypatch.setattr(commit, "verify_package", lambda *args, **kwargs: {})
    package = tmp_path / "package"
    package.mkdir()
    (package / "pages.jsonl").write_text(json.dumps({"page": 2, "text_sha256": "current"}) + "\n")
    job = {"page": 2, "original_text_sha256": "stale", "source_pdf_sha256": "source"}
    with pytest.raises(ValueError, match="compare-and-swap"):
        _check_original(package, tmp_path / "source.pdf", [job], None)
    job["original_text_sha256"] = "current"
    sidecar = tmp_path / "reviews.jsonl"
    sidecar.write_text(json.dumps({"page": 2, "source_pdf_sha256": "source", "decision": "keep_held"}) + "\n")
    with pytest.raises(ValueError, match="source-reviewed"):
        _check_original(package, tmp_path / "source.pdf", [job], sidecar)
    sidecar.unlink()
    # A retry after a successful directory swap may see the same candidate
    # already present. It must not reject that exact version as unrelated.
    job.update(original_text_sha256="earlier", candidate_text_sha256="current")
    assert _check_original(package, tmp_path / "source.pdf", [job], sidecar)[2]["text_sha256"] == "current"
