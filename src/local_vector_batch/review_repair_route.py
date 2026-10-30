"""Turn focused OCR feedback into source-bound repair work, never approval."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json
import os

from .review_queue import load_item, render_page


KEYS = ("source_pdf_sha256", "page", "selected_text_sha256",
        "independent_text_sha256", "prompt_sha256", "focus_sha256", "proposal_sha256")


def classify_feedback(decision: str, has_focus: bool) -> str:
    if decision == "selected":
        return "selected_region_supported" if has_focus else "needs_focused_source_review"
    if decision == "independent":
        return "source_crop_proof_needed" if has_focus else "needs_focused_source_review"
    if decision in ("neither", "unclear"):
        return "alternate_source_ocr_needed" if has_focus else "needs_focused_source_review"
    raise ValueError("invalid review decision")


def route_feedback(manifest: Path, feedback: Path, package_root: Path, output: Path) -> dict:
    """Revalidate every saved choice and emit only hash-bound, non-promoting jobs.

    The feedback selects what to investigate. It is never OCR proof and never
    authorizes an entire page. A downstream repair must rerun source readers,
    pass the whole-page gate, commit with CAS, and publish/index separately.
    """
    manifest, feedback, package_root, output = map(Path, (manifest, feedback, package_root, output))
    manifest_rows = [json.loads(x) for x in manifest.read_text().splitlines() if x.strip()]
    feedback_rows = [json.loads(x) for x in feedback.read_text().splitlines() if x.strip()]
    wanted = {(row.get("source_pdf_sha256"), row.get("page"), row.get("selected_text_sha256"),
               row.get("independent_text_sha256")) for row in feedback_rows}
    by_identity = {}
    for index, row in enumerate(manifest_rows):
        if (row.get("source_pdf_sha256"), row.get("page"), row.get("selected_text_sha256"),
                row.get("independent_text_sha256")) not in wanted:
            continue
        item = load_item(manifest, index)
        key = tuple(item[k] for k in KEYS)
        if key in by_identity:
            raise ValueError("duplicate source disagreement in review manifest")
        by_identity[key] = item
    latest = {}
    for row in feedback_rows:
        if row.get("kind") != "ocr_disagreement_feedback" or row.get("promotes_page") is not False:
            raise ValueError("review feedback is not a focused non-promoting record")
        key = tuple(row.get(k) for k in KEYS)
        if key not in by_identity and row.get("proposal_sha256") is None:
            # Older feedback predates proposal hashes. Only accept it when
            # this manifest has no source-observation proposal to override.
            legacy = [identity for identity, item in by_identity.items()
                      if identity[:-1] == key[:-1] and item["proposal"]["choice"] is None]
            if len(legacy) == 1:
                key = legacy[0]
        if key not in by_identity:
            raise ValueError("saved feedback does not match the source-bound review manifest")
        latest[key] = row
    jobs = []
    crops = output.parent / (output.stem + "-source-crops")
    for key, row in latest.items():
        item = by_identity[key]
        package = package_root / item["source_pdf_sha256"]
        current = None
        if (package / "pages.jsonl").is_file():
            current = next((json.loads(x) for x in (package / "pages.jsonl").read_text().splitlines()
                            if x.strip() and json.loads(x).get("page") == item["page"]), None)
        stale = (current is None or current.get("text_sha256") != item["selected_text_sha256"] or
                 current.get("status") != "review_required" or bool(current.get("review_decision")))
        focus = item["focus"]
        action = "stale_or_unavailable" if stale else classify_feedback(row["decision"], bool(focus.get("boxes")))
        crop_path = None
        crop_sha = None
        if not stale and focus.get("boxes"):
            image = render_page(item, focus=True)
            crop_sha = sha256(image).hexdigest()
            crops.mkdir(parents=True, exist_ok=True)
            crop_path = crops / f"{item['source_pdf_sha256'][:16]}-p{item['page']}-{item['focus_sha256'][:12]}.png"
            if crop_path.exists() and sha256(crop_path.read_bytes()).hexdigest() != crop_sha:
                raise ValueError("existing source crop differs from current original")
            crop_path.write_bytes(image)
        jobs.append({"source_pdf": item["source_pdf"], "source_pdf_sha256": item["source_pdf_sha256"],
                     "package": str(package), "page": item["page"],
                     "selected_text_sha256": item["selected_text_sha256"],
                     "independent_text_sha256": item["independent_text_sha256"],
                     "prompt_sha256": item["prompt_sha256"], "focus_sha256": item["focus_sha256"],
                     "decision": row["decision"], "action": action,
                     "focus": focus, "source_crop": str(crop_path) if crop_path else None,
                     "source_crop_sha256": crop_sha, "publication_allowed": False})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for job in jobs:
            stream.write(json.dumps(job, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(output)
    return {"feedback": len(feedback_rows), "unique_jobs": len(jobs),
            "actions": dict(Counter(job["action"] for job in jobs)), "output": str(output),
            "publication_allowed": False}
