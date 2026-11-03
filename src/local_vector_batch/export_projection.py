"""Additive, source-bound export views; never change readings or acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .evidence import check_quote
from .jsonl import jsonl_physical_lines

PAGE_SCHEMA = "pdf2data.page-state.v1"
CANDIDATE_SCHEMA = "pdf2data.candidate-inventory.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def page_states(pages, source_sha256, quality=None, quality_ref=None):
    """Keep the extractor's selection status separate from package quality."""
    results = {}
    if quality is not None:
        if quality.get("source_pdf_sha256") != source_sha256:
            raise ValueError("page projection quality source differs")
        rows = quality.get("results", [])
        if [r.get("page") for r in rows] != [p["page"] for p in pages]:
            raise ValueError("page projection quality coverage differs")
        results = {r["page"]: r for r in rows}
    projected = []
    for page in pages:
        result = results.get(page["page"])
        outcome = "not_assessed"
        if result is not None:
            if (result.get("page_text_sha256") != page["text_sha256"] or
                    result.get("source_pdf_sha256") != source_sha256 or
                    result.get("outcome") not in {"auto_accepted", "repair_required", "source_blocked"}):
                raise ValueError("page projection quality witness differs")
            outcome = result["outcome"]
        projected.append({
            "schema_version": PAGE_SCHEMA, "page": page["page"],
            "page_text_sha256": page["text_sha256"], "source_pdf_sha256": source_sha256,
            "selection_status": page["status"], "source_quality": outcome,
            "quality_receipt": quality_ref if result is not None else None,
            "display_status": "Source quality: " + outcome.replace("_", " "),
            "publication_eligible": False,
        })
    return projected


def candidate_snapshot(package: Path, pages, source_sha256):
    """Export existing candidates and their exact artifact/page witnesses.

    The caller verifies the L1 package first. This reader rechecks candidate
    bytes and anchors while taking the export snapshot. Older parent manifests
    need not contain this sidecar: the new export explicitly binds its hash.
    """
    package = Path(package)
    manifest_path = package / "extraction/evidence-package.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get("document", {}).get("pdf_sha256") != source_sha256:
        raise ValueError("candidate inventory belongs to another source")
    by_id = {p["id"]: p for p in pages}
    artifacts, data = {}, {}
    for field, name, status in (
            ("fact_candidates", "fact-candidates.jsonl", "unverified_candidate"),
            ("structure_candidates", "structure-candidates.jsonl", "review_required")):
        raw = (package / "extraction" / name).read_bytes()
        digest = _sha(raw)
        if digest != manifest.get("files", {}).get(name, {}).get("sha256"):
            raise ValueError("candidate artifact hash differs: " + name)
        items = [json.loads(line) for line in jsonl_physical_lines(raw.decode("utf-8")) if line.strip(" \t\r")]
        for item in items:
            if item.get("status") != status:
                raise ValueError("candidate status was promoted")
            spans = ([{**item, "quote": item.get("value_text")}] if field == "fact_candidates"
                     else item.get("evidence", []))
            for span in spans:
                page = by_id.get(span.get("page_id"))
                if page is None or span.get("page") != page["page"]:
                    raise ValueError("candidate page differs")
                check_quote(page, span)
        data[field] = items
        artifacts[field] = {"path": "extraction/" + name, "sha256": digest,
                            "count": len(items), "document_pointer": "/" + field}
    inventory = {
        "schema_version": CANDIDATE_SCHEMA, "source_pdf_sha256": source_sha256,
        "parent_package_sha256": _sha((package / "l1-package.json").read_bytes()),
        "evidence_manifest": {"path": "extraction/evidence-package.json", "sha256": _sha(manifest_raw)},
        "binding_scope": "export_snapshot", "artifacts": artifacts,
        "status": "unverified_candidates_only", "publication_eligible": False,
    }
    return {**data, "candidate_inventory": inventory}
