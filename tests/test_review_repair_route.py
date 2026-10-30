import hashlib
import json

from local_vector_batch import review_repair_route as route


def test_feedback_routes_only_current_focused_page(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    packages = tmp_path / "packages"
    package = packages / ("a" * 64)
    package.mkdir(parents=True)
    page = {"page": 2, "text_sha256": "b" * 64, "status": "review_required"}
    (package / "pages.jsonl").write_text(json.dumps(page) + "\n")
    item = {"source_pdf": "/original.pdf", "source_pdf_sha256": "a" * 64,
            "page": 2, "selected_text_sha256": "b" * 64,
            "independent_text_sha256": "c" * 64, "prompt_sha256": "d" * 64,
            "focus_sha256": "e" * 64, "proposal_sha256": "f" * 64,
            "proposal": {"choice": None}, "focus": {"boxes": [[0.1, 0.2, 0.3, 0.4]]}}
    manifest.write_text(json.dumps({key: item[key] for key in route.KEYS[:4]}) + "\n")
    monkeypatch.setattr(route, "load_item", lambda path, index: item)
    monkeypatch.setattr(route, "render_page", lambda item, focus: b"source-crop")
    feedback = tmp_path / "feedback.jsonl"
    feedback.write_text(json.dumps({"kind": "ocr_disagreement_feedback", "promotes_page": False,
                                    "decision": "independent", **{key: item[key] for key in route.KEYS}}) + "\n")
    output = tmp_path / "jobs.jsonl"
    result = route.route_feedback(manifest, feedback, packages, output)
    job = json.loads(output.read_text())
    assert result["actions"] == {"source_crop_proof_needed": 1}
    assert job["source_crop_sha256"] == hashlib.sha256(b"source-crop").hexdigest()
    assert job["publication_allowed"] is False
    page["text_sha256"] = "0" * 64
    (package / "pages.jsonl").write_text(json.dumps(page) + "\n")
    result = route.route_feedback(manifest, feedback, packages, output)
    assert result["actions"] == {"stale_or_unavailable": 1}


def test_feedback_rejects_unbound_choice(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n")
    monkeypatch.setattr(route, "load_item", lambda path, index: {
        **{key: "a" * 64 for key in route.KEYS if key != "page"}, "page": 1,
        "proposal": {"choice": None}})
    feedback = tmp_path / "feedback.jsonl"
    feedback.write_text(json.dumps({"kind": "ocr_disagreement_feedback", "promotes_page": False,
                                    "decision": "independent", "source_pdf_sha256": "b" * 64}) + "\n")
    import pytest
    with pytest.raises(ValueError, match="does not match"):
        route.route_feedback(manifest, feedback, tmp_path, tmp_path / "jobs.jsonl")
