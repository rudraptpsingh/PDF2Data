"""Fast, local inventory of PDF pages still requiring source review."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlsplit


def _render_group(group: tuple[str, str, list[dict], int]) -> list[dict]:
    """One PDF per process: PDFium is not safe to share across threads."""
    import numpy as np
    import pypdfium2 as pdfium

    source_sha, path, rows, dpi = group
    result = []
    try:
        source_digest = sha256()
        with Path(path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                source_digest.update(block)
        if source_digest.hexdigest() != source_sha:
            return [{**row, "render_error": "source PDF SHA-256 mismatch"} for row in rows]
        pdf = pdfium.PdfDocument(path)
        for row in rows:
            try:
                page = pdf[int(row["page"]) - 1]
                native = page.get_textpage().get_text_range().strip()
                image = page.render(scale=dpi / 72).to_pil().convert("L")
                pixels = np.asarray(image)
                ink = pixels < 250
                ys, xs = np.where(ink)
                result.append({**row,
                               "source_pdf_sha_verified": True, "render_dpi": dpi,
                               "render_size": [image.width, image.height],
                               "render_gray_sha256": sha256(image.tobytes()).hexdigest(),
                               "render_min_gray": int(pixels.min()),
                               "ink_pixels_below_250": int(ink.sum()),
                               "ink_pixels_below_200": int((pixels < 200).sum()),
                               "ink_bbox_250": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
                               if len(xs) else None,
                               "pdfium_text_chars": len(native),
                               "pdfium_text_sample": native[:120],
                               "render_visually_blank": not ink.any() and not native})
            except Exception as error:
                result.append({**row, "render_error": str(error)[:180]})
    except Exception as error:
        result.extend({**row, "render_error": str(error)[:180]} for row in rows)
    return result


def audit_rendered_review_pages(manifest: Path, output: Path, workers: int = 4,
                                dpi: int = 72) -> dict:
    """Read-only rendered-original audit for short/missing pages.

    Input JSONL requires source_pdf_sha256, source_pdf_path, page and category.
    A zero-ink result is evidence for review, not an automatic release decision.
    """
    if not 1 <= workers <= 16:
        raise ValueError("workers must be 1–16")
    if not 72 <= dpi <= 300:
        raise ValueError("DPI must be 72–300")
    started = time.monotonic()
    grouped = defaultdict(list)
    for line in Path(manifest).open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        sha, path = row.get("source_pdf_sha256"), row.get("source_pdf_path")
        page = row.get("page")
        if not isinstance(sha, str) or len(sha) != 64 or not isinstance(path, str) or not isinstance(page, int) or page < 1:
            raise ValueError("render audit row needs source SHA, PDF path and positive physical page")
        grouped[(sha, path)].append(row)
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(_render_group, (sha, path, rows, dpi))
                                    for (sha, path), rows in grouped.items()]):
            results.extend(future.result())
    results.sort(key=lambda row: (row["source_pdf_sha256"], row["page"]))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in results:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"pages": len(results), "documents": len(grouped),
            "visually_blank": sum(row.get("render_visually_blank", False) for row in results),
            "pdfium_text_ge_80": sum(row.get("pdfium_text_chars", 0) >= 80 for row in results),
            "render_errors": sum("render_error" in row for row in results),
            "output": str(output), "elapsed_seconds": round(time.monotonic() - started, 3),
            "note": "Rendered evidence never changes page status without explicit review."}


def _category(page: dict, issues: set[str]) -> tuple[str, str]:
    if not page.get("text") or "missing_text" in issues or "ocr_failed" in issues:
        if page.get("ocr") is None:
            return "missing_text", "retry_ocr_then_review"
        return "missing_text", "inspect_source_or_alternate_ocr"
    if "low_confidence_ocr" in issues or page.get("selected_quality", {}).get("needs_ocr"):
        return "uncertain_ocr", "independent_ocr_and_source_review"
    if "native_parser_disagreement" in issues or "secondary_parser_failed" in issues:
        return "parser_disagreement", "compare_native_parsers_and_source"
    if page.get("method") != "pdf_text":
        return "visual_ocr_review", "source_visual_review"
    if page.get("chars", 0) < 80:
        return "short_page_review", "inspect_short_or_cover_page"
    return "other_review", "inspect_source"


def audit_review_pages(root: Path, details: Path | None = None) -> dict:
    """Stream current packages in one pass; never change review or fact status.

    A full local scan is only a few seconds for roughly 21k packages and avoids
    a second state store that could go stale during concurrent extraction.
    Re-running after a batch is therefore the incremental update operation.
    """
    started = time.monotonic()
    root = Path(root)
    if not root.is_dir():
        raise ValueError("extraction root does not exist")
    details_handle = None
    if details is not None:
        details = Path(details)
        details.parent.mkdir(parents=True, exist_ok=True)
        details_handle = details.open("w", encoding="utf-8")
    scanned = 0
    documents_with_review = 0
    review_pages = 0
    categories = Counter()
    actions = Counter()
    source_families = Counter()
    methods = Counter()
    issue_kinds = Counter()
    top_documents = []
    try:
        for directory in root.iterdir():
            report_path, pages_path = directory / "report.json", directory / "pages.jsonl"
            if not (directory.is_dir() and report_path.is_file() and pages_path.is_file()):
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scanned += 1
            issues = defaultdict(set)
            for issue in report.get("issues", []):
                issues[issue.get("page")].add(issue.get("kind"))
            source_url = report.get("source_url") or ""
            family = urlsplit(source_url).hostname or "unknown"
            document_count = 0
            with pages_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    page = json.loads(line)
                    if page.get("status") != "review_required":
                        continue
                    document_count += 1
                    review_pages += 1
                    issue_set = issues[page["page"]]
                    category, action = _category(page, issue_set)
                    categories[category] += 1
                    actions[action] += 1
                    source_families[(family, category)] += 1
                    methods[page.get("method") or "unknown"] += 1
                    issue_kinds.update(issue_set)
                    if details_handle:
                        details_handle.write(json.dumps({
                            "source_pdf_sha256": report.get("source_pdf_sha256"),
                            "source_url": source_url, "page": page["page"],
                            "status": page["status"], "method": page.get("method"),
                            "chars": page.get("chars"), "category": category,
                            "next_action": action, "issues": sorted(issue_set),
                        }, ensure_ascii=False, separators=(",", ":")) + "\n")
            if document_count:
                documents_with_review += 1
                top_documents.append((document_count, report.get("source_pdf_sha256"), source_url))
    finally:
        if details_handle:
            details_handle.close()
    return {
        "packages_scanned": scanned, "documents_with_review": documents_with_review,
        "review_pages": review_pages, "categories": dict(categories),
        "next_actions": dict(actions), "methods": dict(methods),
        "issue_kinds": dict(issue_kinds),
        "top_source_families": [
            {"host": host, "category": category, "pages": count}
            for (host, category), count in source_families.most_common(30)
        ],
        "top_documents": [
            {"source_pdf_sha256": sha, "source_url": url, "review_pages": count}
            for count, sha, url in sorted(top_documents, reverse=True)[:30]
        ],
        "detail_path": str(details) if details else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "scope_note": "Review categories are work queues, not assertions that OCR text or facts are verified.",
    }
