"""SOFTWARE boundary controls; these fixtures are not source-accuracy gold."""
import hashlib
import json
from pathlib import Path

import pytest

from local_vector_batch.export_projection import candidate_snapshot, page_states
from local_vector_batch.presentation import export_presentation


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def packet(tmp_path):
    package = tmp_path / "packages" / "example"
    extraction = package / "extraction"
    extraction.mkdir(parents=True)
    source = "a" * 64
    pages = [{"id": f"example:p{n}", "page": n, "text": text,
              "text_sha256": digest(text.encode()), "status": "ready", "method": "pdf_text"}
             for n, text in enumerate(("Issuer", "Rationale for 9.30%"), 1)]
    fact = {"kind": "percentage", "page": 2, "page_id": "example:p2",
            "page_text_sha256": pages[1]["text_sha256"], "char_start": 14,
            "char_length": 5, "value_text": "9.30%", "status": "unverified_candidate"}
    structure = {"kind": "party_role", "proposed_role": "issuer_masthead", "status": "review_required",
                 "evidence": [{"page": 1, "page_id": "example:p1", "page_text_sha256": pages[0]["text_sha256"],
                               "char_start": 0, "char_length": 6, "quote": "Issuer"}]}
    files = {}
    for name, value in (("fact-candidates.jsonl", fact), ("structure-candidates.jsonl", structure)):
        raw = (json.dumps(value) + "\n").encode()
        (extraction / name).write_bytes(raw)
        files[name] = {"sha256": digest(raw)}
    manifest = {"document": {"pdf_sha256": source}, "files": files}
    (extraction / "evidence-package.json").write_text(json.dumps(manifest))
    (package / "l1-package.json").write_text('{"SOFTWARE": "parent"}')
    (extraction / "pages.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pages))
    (extraction / "tables.json").write_text("[]")
    quality = {"source_pdf_sha256": source, "results": [
        {"page": p["page"], "source_pdf_sha256": source, "page_text_sha256": p["text_sha256"],
         "outcome": "repair_required"} for p in pages]}
    return package, pages, source, quality


def test_candidate_snapshot_keeps_values_statuses_and_artifact_pointers(packet):
    package, pages, source, _ = packet
    snapshot = candidate_snapshot(package, pages, source)
    assert snapshot["fact_candidates"][0]["value_text"] == "9.30%"
    assert snapshot["structure_candidates"][0]["proposed_role"] == "issuer_masthead"
    inventory = snapshot["candidate_inventory"]
    assert inventory["binding_scope"] == "export_snapshot"
    assert inventory["artifacts"]["fact_candidates"]["document_pointer"] == "/fact_candidates"
    assert inventory["artifacts"]["structure_candidates"]["count"] == 1
    assert inventory["publication_eligible"] is False


@pytest.mark.parametrize("mode", ["changed_bytes", "foreign_source", "wrong_span", "promoted"])
def test_candidate_snapshot_refuses_loss_of_binding(packet, mode):
    package, pages, source, _ = packet
    path = package / "extraction/evidence-package.json"
    manifest = json.loads(path.read_text())
    candidate = package / "extraction/fact-candidates.jsonl"
    if mode == "foreign_source":
        manifest["document"]["pdf_sha256"] = "b" * 64
    else:
        value = json.loads(candidate.read_text())
        if mode == "wrong_span":
            value["char_start"] -= 1
        elif mode == "promoted":
            value["status"] = "verified"
        else:
            value["value_text"] = "90%"
        candidate.write_text(json.dumps(value) + "\n")
        if mode != "changed_bytes":
            manifest["files"][candidate.name]["sha256"] = digest(candidate.read_bytes())
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        candidate_snapshot(package, pages, source)


def test_page_projection_never_promotes_selection_ready_to_source_quality(packet):
    _, pages, source, quality = packet
    unknown = page_states(pages, source)
    assert unknown[1]["selection_status"] == "ready"
    assert unknown[1]["source_quality"] == "not_assessed"
    projected = page_states(pages, source, quality, {"path": "quality.json", "sha256": "c" * 64})
    assert projected[1]["source_quality"] == "repair_required"
    assert projected[1]["display_status"] == "Source quality: repair required"
    assert projected[1]["publication_eligible"] is False
    quality["results"][1]["page_text_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="witness"):
        page_states(pages, source, quality)


def test_separate_presentation_uses_quality_and_never_changes_source(packet, tmp_path):
    package, pages, source, quality = packet
    extraction = package / "extraction"
    before = {p: p.read_bytes() for p in extraction.iterdir() if p.is_file()}
    report = {"source_pdf_sha256": source, "document_id": "example", "title": "SOFTWARE", "source_url": None}
    out = tmp_path / "presentation"
    export_presentation(extraction, report, quality=quality, destination=out)
    for filename in ("pages/page-0002.html", "pages/page-0002.md", "index.html", "index.md"):
        assert "Source quality: repair required" in (out / filename).read_text()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["pages"][1]["status_scope"] == "extraction_selection"
    assert manifest["pages"][1]["page_state"]["source_quality"] == "repair_required"
    assert all(p.read_bytes() == raw for p, raw in before.items())
    with pytest.raises(ValueError, match="new directory"):
        export_presentation(extraction, report, destination=out)


def test_overlay_quality_is_available_even_without_geometry(packet):
    from local_vector_batch.layout_overlay import build_overlays
    _, pages, source, quality = packet
    states = page_states(pages, source, quality)
    overlays = build_overlays({"source_pdf_sha256": source, "pages": pages, "page_states": states})
    assert overlays["pages"][1]["unlocated_blocks"] == ["page_dimensions_unavailable"]
    assert overlays["pages"][1]["page_state"]["source_quality"] == "repair_required"
