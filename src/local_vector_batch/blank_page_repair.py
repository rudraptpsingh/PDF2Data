"""Source-raster proof and atomic status update for genuinely empty PDF pages."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import uuid

import numpy as np
import pypdfium2 as pdfium
from filelock import FileLock
from PIL import Image

from .evidence_package import create_package, verify_package
from .pdf_ingest import BLOCKING_ISSUES


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in jsonl_physical_lines(path.read_text()) if line.strip(' \t\r')]


def _raster_sha(source: Path, page_number: int, dpi: int) -> tuple[str, int, int, int]:
    pdf = pdfium.PdfDocument(str(source))
    page = pdf[page_number - 1]
    native_chars = len(page.get_textpage().get_text_range().strip())
    image = page.render(scale=dpi / 72).to_pil().convert("L")
    pixels = np.asarray(image)
    return (sha256(pixels.tobytes()).hexdigest(),
            int(np.count_nonzero(pixels < 200)),
            int(np.count_nonzero(pixels < 250)), native_chars)


def commit_exact_white_pages(package: Path, source: Path, receipts: list[dict]) -> dict:
    """Mark only source-bound, blank physical pages blank.

    Nonwhite source pages require a hash-bound visual role review plus zero OCR.
    It changes no text, facts or vector inputs, and refuses stale hashes.
    """
    package, source = Path(package), Path(source)
    if not receipts or len({r.get("page") for r in receipts}) != len(receipts):
        raise ValueError("distinct page receipts required")
    for receipt in receipts:
        if receipt.get("render_dpi") != 180 or receipt.get("source_pdf_sha_verified") is not True:
            raise ValueError("source raster receipt required")
        if receipt.get("status") == "raster_blank_180":
            if receipt.get("ink_pixels_below_200") != 0 or receipt.get("ink_pixels_below_250") != 0:
                raise ValueError("only exact-white source receipts qualify")
        elif receipt.get("status") == "visually_blank_nontext_180":
            if (receipt.get("nontext_role") not in {"border_only", "isolated_scan_specks"} or
                    receipt.get("visual_reviewed_by") != "codex_source_visual" or
                    receipt.get("independent_ocr_line_count") != 0 or
                    receipt.get("independent_ocr_text_sha256") != sha256(b"").hexdigest()):
                raise ValueError("source-bound visual nontext review and empty OCR required")
        else:
            raise ValueError("unsupported blank-page receipt")
    with FileLock(str(package) + ".commit.lock", timeout=0):
        verify_package(package, source_pdf=source)
        source_sha = sha256(source.read_bytes()).hexdigest()
        report = json.loads((package / "report.json").read_text())
        if source_sha != report["source_pdf_sha256"]:
            raise ValueError("source PDF changed")
        pages = _rows(package / "pages.jsonl")
        by_number = {p["page"]: p for p in pages}
        for receipt in receipts:
            number = receipt["page"]
            current = by_number.get(number)
            if (receipt.get("source_pdf_sha256") != source_sha or
                    receipt.get("source_pdf_path") != str(source) or
                    current is None or current["status"] != "review_required" or
                    current["text"] or current["text_sha256"] != receipt.get("page_text_sha256") or
                    current.get("review_decision")):
                raise ValueError(f"blank-page compare-and-swap failed for page {number}")
            raster_sha, dark, gray, native_chars = _raster_sha(source, number, 180)
            if raster_sha != receipt.get("render_gray_sha256") or native_chars:
                raise ValueError(f"source page {number} changed or has native text")
            if receipt["status"] == "raster_blank_180":
                if dark or gray:
                    raise ValueError(f"source page {number} is not exactly white")
            else:
                image_path = Path(receipt["visual_raster_png"])
                if (sha256(image_path.read_bytes()).hexdigest() != receipt.get("visual_raster_png_sha256") or
                        sha256(np.asarray(Image.open(image_path).convert("L")).tobytes()).hexdigest() != raster_sha):
                    raise ValueError("source visual review image changed")
        selected = {r["page"] for r in receipts}
        staged = Path(tempfile.mkdtemp(prefix=f".{package.name}.blank-", dir=package.parent))
        backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
        try:
            shutil.copytree(package, staged, dirs_exist_ok=True)
            for page in pages:
                if page["page"] in selected:
                    page["status"] = "blank"
                    proof = next(r for r in receipts if r["page"] == page["page"])
                    page["blank_source_proof"] = ("exact_white_original_raster_180_dpi" if
                        proof["status"] == "raster_blank_180" else
                        f"source_visual_nontext_{proof['nontext_role']}_180_dpi")
            (staged / "pages.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pages))
            report["issues"] = [issue for issue in report["issues"]
                                if not (issue.get("page") in selected and
                                        issue.get("kind") in {"missing_text", "low_confidence_ocr"})]
            report["page_status_counts"] = {state: sum(p["status"] == state for p in pages)
                                            for state in ("ready", "blank", "review_required")}
            if not (staged / "embedding-input.jsonl").read_text().strip() and not any(
                    issue.get("kind") == "no_searchable_text" for issue in report["issues"]):
                report["issues"].append({"page": 1, "kind": "no_searchable_text",
                                         "detail": "No page produced searchable text"})
            report["requires_source_review"] = bool(report["page_status_counts"]["review_required"] or
                                                    report.get("tables"))
            report["complete_for_embedding"] = not any(
                issue.get("kind") in BLOCKING_ISSUES for issue in report["issues"])
            (staged / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            create_package(staged)
            verify_package(staged, source_pdf=source)
            verify_package(package, source_pdf=source)
            live_pages = {p["page"]: p for p in _rows(package / "pages.jsonl")}
            if any(live_pages[n]["status"] != "review_required" or
                   live_pages[n]["text_sha256"] != by_number[n]["text_sha256"]
                   for n in selected):
                raise ValueError("source page status changed before commit")
            package.replace(backup)
            try:
                staged.replace(package)
            except Exception:
                backup.replace(package)
                raise
            shutil.rmtree(backup)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        return {"source_pdf_sha256": source_sha, "pages_blank": sorted(selected),
                "package": str(package), "index_ready": False}


def commit_exact_white_manifest(manifest: Path) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in _rows(Path(manifest)):
        if row.get("status") == "raster_blank_180":
            grouped[(row["source_pdf_sha256"], row["source_pdf_path"])].append(row)
    results = []
    for (source_sha, source_path), receipts in grouped.items():
        package = Path(receipts[0].get("package") or
                       Path(manifest).parent.parent / "extracted" / source_sha)
        results.append(commit_exact_white_pages(package, Path(source_path), receipts))
    return results


def refresh_blank_report(package: Path, source: Path) -> bool:
    """Reconcile an earlier blank CAS commit's document-level summary safely."""
    package, source = Path(package), Path(source)
    with FileLock(str(package) + ".commit.lock", timeout=0):
        verify_package(package, source_pdf=source)
        pages = _rows(package / "pages.jsonl")
        report = json.loads((package / "report.json").read_text())
        if not any(p.get("blank_source_proof") for p in pages):
            raise ValueError("package has no source-proven blank decision")
        blank_pages = {p["page"] for p in pages if p.get("blank_source_proof")}
        new_report = dict(report)
        new_report["issues"] = [issue for issue in report["issues"]
                                if not (issue.get("page") in blank_pages and
                                        issue.get("kind") in {"missing_text", "low_confidence_ocr"})]
        if not (package / "embedding-input.jsonl").read_text().strip() and not any(
                issue.get("kind") == "no_searchable_text" for issue in new_report["issues"]):
            new_report["issues"].append({"page": 1, "kind": "no_searchable_text",
                                         "detail": "No page produced searchable text"})
        new_report["requires_source_review"] = bool(new_report["page_status_counts"]["review_required"] or
                                                    new_report.get("tables"))
        new_report["complete_for_embedding"] = not any(
            issue.get("kind") in BLOCKING_ISSUES for issue in new_report["issues"])
        if new_report == report:
            return False
        staged = Path(tempfile.mkdtemp(prefix=f".{package.name}.summary-", dir=package.parent))
        backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
        try:
            shutil.copytree(package, staged, dirs_exist_ok=True)
            (staged / "report.json").write_text(json.dumps(new_report, ensure_ascii=False, indent=2) + "\n")
            create_package(staged)
            verify_package(staged, source_pdf=source)
            package.replace(backup)
            try:
                staged.replace(package)
            except Exception:
                backup.replace(package)
                raise
            shutil.rmtree(backup)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        return True
