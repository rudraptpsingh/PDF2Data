"""Attach PP-Structure page blocks as source-bound layout candidates."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

import hashlib
import json
from pathlib import Path

from .evidence_package import create_package, verify_package


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_paddle_layout(package: Path, source_pdf: Path, manifest_path: Path) -> dict:
    package, source_pdf, manifest_path = map(Path, (package, source_pdf, manifest_path))
    verify_package(package, source_pdf=source_pdf)
    report = json.loads((package / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_pdf_sha256") != report["source_pdf_sha256"] or manifest.get("engine") not in {"PPStructureV2", "PPStructureV3"}:
        raise ValueError("Paddle source PDF or engine identity mismatch")
    pages = {row["page"]: row for row in (json.loads(line) for line in
             jsonl_physical_lines((package / "pages.jsonl").read_text(encoding="utf-8")) if line.strip(' \t\r'))}
    rows = []
    seen = set()
    for entry in manifest.get("pages", []):
        number = entry.get("physical_pdf_page")
        if type(number) is not int or number not in pages or number in seen:
            raise ValueError("Paddle physical page identity mismatch")
        seen.add(number)
        image_path = manifest_path.parent / f"page-{number}.png"
        if not image_path.is_file() or _sha(image_path) != entry.get("source_image_sha256"):
            raise ValueError("Paddle source page image hash mismatch")
        directory = Path(entry["output_dir"])
        if not directory.is_absolute() and not directory.exists():
            directory = manifest_path.parent / directory
        result_path = directory / f"page-{number}_res.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("page_index") not in {None, 0, number - 1} or not isinstance(result.get("parsing_res_list"), list):
            raise ValueError("Paddle result page or block list mismatch")
        blocks = []
        for block in result["parsing_res_list"]:
            box = block.get("block_bbox")
            if not isinstance(box, list) or len(box) != 4 or not all(isinstance(x, (int, float)) for x in box):
                raise ValueError("Paddle layout block has no valid bounding box")
            blocks.append({"label": str(block.get("block_label", "unknown")),
                           "content": str(block.get("block_content", "")),
                           "bbox": box, "order": block.get("block_order"),
                           "block_id": block.get("block_id")})
        rows.append({"page": number, "page_id": pages[number]["id"],
                     "source_pdf_sha256": report["source_pdf_sha256"],
                     "selected_page_text_sha256": pages[number]["text_sha256"],
                     "source_image_sha256": entry["source_image_sha256"],
                     "candidate_engine": manifest["engine"], "result_sha256": _sha(result_path),
                     "status": "review_required", "blocks": blocks})
    path = package / "paddle-layout-candidates.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    receipt = create_package(package)
    return {"document_id": report["document_id"], "pages": len(rows),
            "blocks": sum(len(row["blocks"]) for row in rows), "status": "review_required",
            "candidate_sha256": receipt["candidate_artifacts"]["paddle-layout-candidates.jsonl"]}
