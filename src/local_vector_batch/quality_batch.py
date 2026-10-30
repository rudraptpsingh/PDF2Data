"""Parallel source quality decisions over cached extraction, without new OCR."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path

from .quality_gate import assess_package, write_quality_report, write_quality_summary


def _worker_limits():
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[name] = "1"
    try:
        import cv2
        cv2.setNumThreads(1)
    except ImportError:
        pass


def bound_independent(rows: list[dict], versions: dict[int, str], source_sha: str):
    """Select exact extraction versions; retain old cache history without using it."""
    texts, metadata, stale = {}, {}, 0
    for row in rows:
        value = row.get("independent", {})
        text, page = value.get("text"), row.get("page")
        if row.get("source_pdf_sha256") != source_sha or page not in versions:
            raise ValueError("cached independent extraction source/page binding failed")
        if row.get("selected_page_text_sha256") != versions[page]:
            stale += 1
            continue
        if (page in texts or not isinstance(text, str) or
                hashlib.sha256(text.encode()).hexdigest() != value.get("text_sha256") or
                not value.get("engine") or
                value["engine"] == row.get("selected", row.get("vision", {})).get("engine")):
            raise ValueError("cached independent extraction text or engine binding failed")
        texts[page] = text
        metadata[page] = {**value, "source_pdf_sha256": source_sha, "page": page,
                          "selected_page_text_sha256": versions[page]}
    return texts, metadata, stale


def _score(task):
    package, source, pages, independent, policy, output = task
    try:
        versions = {p["page"]: p["text_sha256"] for p in (
            json.loads(line) for line in jsonl_physical_lines((Path(package)/"pages.jsonl").read_text()) if line.strip(' \t\r'))}
        source_sha = json.loads((Path(package)/"evidence-package.json").read_text())["document"]["pdf_sha256"]
        texts, metadata, stale = bound_independent(independent, versions, source_sha)
        report = assess_package(Path(package), Path(source), pages=pages,
                                independent_by_page=texts,
                                independent_metadata_by_page=metadata,
                                policy_path=Path(policy) if policy else None,
                                derive_ocr_source_raster=True)
        report["stale_independent_versions_ignored"] = stale
    except Exception as error:
        report = {"schema_version": 1, "package_outcome": "source_blocked", "source_error": str(error)[:300],
                  "pages_assessed": 0, "pages_requested": len(pages or []), "packages_blocked": 1,
                  "counts": {"auto_accepted": 0, "repair_required": 0, "source_blocked": 0},
                  "results": [], "human_review_receipts_created": 0}
    write_quality_report(report, Path(output))
    return report


def assess_batch(manifest: Path, output: Path, *, independent: Path | None = None,
                 workers: int = 6, policy: Path | None = None) -> dict:
    """One process per PDF; serial source rendering within each process.

    Manifest rows have package, source_pdf and optional physical pages. Cache
    rows use the existing ocr-consensus schema. There are no cloud/API calls.
    """
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("quality worker count must be 1..32")
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    rows = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text()) if line.strip(' \t\r')]
    if not rows:
        raise ValueError("quality batch manifest is empty")
    cached = {}
    if independent:
        for line in jsonl_physical_lines(Path(independent).read_text()):
            if line.strip():
                row = json.loads(line)
                cached.setdefault(row.get("source_pdf_sha256"), []).append(row)
    tasks, sources = [], set()
    for row in rows:
        package, source = Path(row["package"]).resolve(), Path(row["source_pdf"]).resolve()
        if output.is_relative_to(package) or output == source or output == manifest:
            raise ValueError("quality output must be separate from source and package")
        sha = json.loads((package/"evidence-package.json").read_text())["document"]["pdf_sha256"]
        if sha in sources:
            raise ValueError("duplicate source PDF in quality batch")
        sources.add(sha)
        pages = row.get("pages")
        if pages is not None and (not isinstance(pages, list) or not pages or
                                  any(type(p) is not int or p < 1 for p in pages) or len(set(pages)) != len(pages)):
            raise ValueError("pages must be unique positive physical page numbers")
        candidates = [r for r in cached.get(sha, []) if pages is None or r.get("page") in pages]
        tasks.append((str(package), str(source), pages, candidates, str(policy) if policy else None,
                      str(output/"packages"/(sha+".json"))))
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    reports = []
    # Spawn avoids inheriting PDFium's C handles between processes on macOS.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_worker_limits) as pool:
        futures = [pool.submit(_score, task) for task in tasks]
        for future in as_completed(futures):
            reports.append(future.result())
            write_quality_summary(reports, output/"summary.json", started_at=started)
    summary = json.loads((output/"summary.json").read_text()) if reports else {
        "pages_assessed": 0, "auto_accepted": 0, "repair_required": 0, "source_blocked": 0}
    return {**summary, "workers": workers, "packages_completed": len(reports),
            "summary_path": str(output/"summary.json")}
