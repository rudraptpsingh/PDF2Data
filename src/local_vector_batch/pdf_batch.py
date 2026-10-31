"""Bounded, resumable local PDF extraction from a source-download manifest."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import shutil
import signal
import time

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH = re.compile(r"^[a-f0-9]{64}$")


@contextmanager
def _document_deadline(seconds: float):
    """Bound one PDF without abandoning the rest of a batch (POSIX workers)."""
    if not seconds:
        yield
        return
    old = signal.getsignal(signal.SIGALRM)
    def expired(_signum, _frame):
        raise TimeoutError(f"PDF extraction exceeded {seconds:g} seconds; retry with a higher timeout")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def read_manifest(path: Path, *, limit: int) -> list[dict]:
    """Resolve local paths and reject ambiguous identities before any work."""
    if not 1 <= limit <= 100_000:
        raise ValueError("limit must be 1–100000")
    rows, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        identity = row.get("document_id") or row.get("id")
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError(f"manifest line {number}: unsafe or missing document_id")
        if identity in seen:
            raise ValueError(f"manifest line {number}: duplicate document_id {identity}")
        seen.add(identity)
        source = row.get("path") or row.get("pdf_path")
        url = row.get("url") or row.get("source_url")
        if source is not None:
            source = Path(source)
            if not source.is_absolute():
                source = source.resolve() if source.is_file() else (path.parent / source).resolve()
        expected_hash = row.get("sha256") or row.get("pdf_sha256")
        if expected_hash is not None and (not isinstance(expected_hash, str) or not HASH.fullmatch(expected_hash)):
            raise ValueError(f"manifest line {number}: invalid PDF SHA-256")
        if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "http://"))):
            raise ValueError(f"manifest line {number}: invalid source URL")
        title = row.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 240):
            raise ValueError(f"manifest line {number}: invalid title")
        rows.append({"document_id": identity, "path": str(source) if source else None,
                     "url": url, "title": title, "sha256": expected_hash,
                     "source_status": row.get("status")})
        if len(rows) >= limit:
            break
    return rows


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repair_improved(report: dict, prior: dict) -> bool:
    """Prefer fewer known extraction gaps, even when corrected OCR is shorter."""
    from .pdf_ingest import BLOCKING_ISSUES
    before = sum(item["kind"] in BLOCKING_ISSUES for item in prior["issues"])
    after = sum(item["kind"] in BLOCKING_ISSUES for item in report["issues"])
    if after != before:
        return after < before
    return (report["text_chars"] > prior["text_chars"] or
            report["page_status_counts"]["review_required"] <
            prior["page_status_counts"]["review_required"])


def extract_one(row: dict, output: Path, *, resume: bool, ocr: str,
                ocr_device: str = "auto", ocr_language: str = "eng", tables: bool,
                repair_ocr: bool = False, refresh: bool = False,
                document_timeout: float = 0) -> dict:
    from .evidence_package import verify_package
    from .pdf_ingest import extract_pdf

    start = time.monotonic()
    identity = row["document_id"]
    source = Path(row["path"]) if row.get("path") else None
    source_url = row.get("url")
    if source_url:
        source_url = source_url.replace(" ", "%20")
    base = {"document_id": identity, "source_url": source_url}
    if source is None or not source.is_file():
        return {**base, "status": "source_unavailable", "source_status": row.get("source_status")}
    try:
        source_sha = _hash(source)
        if row.get("sha256") and source_sha != row["sha256"]:
            raise ValueError("download receipt SHA-256 differs from source PDF bytes")
        target = output / identity
        prior_report = None
        if target.exists():
            if not resume:
                raise ValueError("output already exists; pass --resume to verify it")
            receipt = verify_package(target, source_pdf=source)
            report = json.loads((target / "report.json").read_text(encoding="utf-8"))
            if report.get("source_url") != source_url:
                raise ValueError("resumed source URL differs from original extraction")
            from .pdf_ingest import extractor_identity
            current_identity=extractor_identity()
            stale_extractor=any(report.get(key)!=value for key,value in current_identity.items())
            if stale_extractor and not refresh:
                return {**base,'status':'retry_required','reason':'cached_extractor_outdated',
                    'source_pdf_sha256':source_sha,'output':str(target),
                    'required_extractor_identity':current_identity,
                    'detail':'Preserve the old evidence package; extract to a fresh output directory or explicitly refresh a disposable cache.',
                    'elapsed_ms':round((time.monotonic()-start)*1000)}
            can_repair = repair_ocr and not report.get("complete_for_embedding") and any(
                item["kind"] in {"missing_text", "garbled_text", "low_confidence_ocr",
                                 "ocr_failed", "no_searchable_text", "native_parser_disagreement"}
                for item in report["issues"])
            if refresh or can_repair:
                prior_report = report
            elif not report.get("complete_for_embedding"):
                return {**base, "status": "review_required", "source_pdf_sha256": source_sha,
                        "pages": receipt["pages"], "page_status_counts": report.get("page_status_counts"),
                        "ocr_compute": report.get("ocr_compute"),
                        "requires_source_review": report.get("requires_source_review", True),
                        "output": str(target), "elapsed_ms": round((time.monotonic()-start)*1000)}
            else:
                return {**base, "status": "verified_cached", "source_pdf_sha256": source_sha,
                        "pages": receipt["pages"], "page_status_counts": report.get("page_status_counts"),
                        "ocr_compute": report.get("ocr_compute"),
                        "requires_source_review": report.get("requires_source_review", True),
                        "output": str(target), "elapsed_ms": round((time.monotonic()-start)*1000)}
        temporary = output / f".{identity}.part-{os.getpid()}"
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            with _document_deadline(document_timeout):
                report = extract_pdf(source, temporary, title=row.get("title") or identity,
                                     source_url=source_url,
                                     ocr=ocr, ocr_device=ocr_device,
                                     ocr_language=ocr_language, tables=tables)
            verify_package(temporary, source_pdf=source)
            if prior_report is not None:
                improved = refresh or _repair_improved(report, prior_report)
                if not improved:
                    report = prior_report
                    return {**base, "status": "review_required", "source_pdf_sha256": source_sha,
                            "pages": report["pages"], "page_status_counts": report.get("page_status_counts"),
                            "ocr_compute": report.get("ocr_compute"),
                            "requires_source_review": report.get("requires_source_review", True),
                            "output": str(target), "elapsed_ms": round((time.monotonic()-start)*1000)}
                backup = output / f".{identity}.backup-{os.getpid()}"
                target.replace(backup)
                try:
                    temporary.replace(target)
                except Exception:
                    backup.replace(target)
                    raise
                shutil.rmtree(backup)
            else:
                temporary.replace(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return {**base, "status": "extracted" if report["complete_for_embedding"] else "review_required",
                "source_pdf_sha256": source_sha, "pages": report["pages"], "tables": report["tables"],
                "issues": report["issues"], "page_status_counts": report.get("page_status_counts"),
                "ocr_compute": report.get("ocr_compute"),
                "requires_source_review": report.get("requires_source_review", True), "output": str(target),
                "repaired_ocr": prior_report is not None and repair_ocr,
                "refreshed": prior_report is not None and refresh,
                "elapsed_ms": round((time.monotonic()-start)*1000)}
    except TimeoutError as error:
        return {**base, "status": "retry_required", "error": str(error)[:300],
                "source_pdf_sha256": source_sha,
                "elapsed_ms": round((time.monotonic()-start)*1000)}
    except Exception as error:
        return {**base, "status": "failed", "error": str(error)[:300],
                "elapsed_ms": round((time.monotonic()-start)*1000)}


def extract_batch(manifest: Path, output: Path, *, workers: int = 4, limit: int = 500,
                  resume: bool = False, ocr: str = "auto", ocr_device: str = "auto",
                  ocr_language: str = "eng",
                  tables: bool = True, repair_ocr: bool = False,
                  refresh: bool = False,
                  document_timeout: float = 0) -> dict:
    """Keep at most 2×workers jobs in flight and write one local batch receipt."""
    if not 1 <= workers <= 16:
        raise ValueError("workers must be 1–16")
    if ocr_device == "gpu":
        raise ValueError("forced GPU OCR is unsupported: Vision does not report which accelerator ran the request")
    if repair_ocr and (not resume or ocr == "off"):
        raise ValueError("--repair-ocr requires --resume and an active OCR engine")
    if refresh and (not resume or repair_ocr):
        raise ValueError("--refresh requires --resume and cannot combine with --repair-ocr")
    if document_timeout < 0 or document_timeout > 3600:
        raise ValueError("document timeout must be 0–3600 seconds")
    if document_timeout and os.name != "posix":
        raise ValueError("per-document timeout is available on macOS/Linux; use 0 on Windows")
    rows = read_manifest(Path(manifest), limit=limit)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("manifest has no input rows")
    started = time.monotonic()
    results: list[dict] = []
    cursor = iter(rows)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {}
        def fill() -> None:
            while len(pending) < workers * 2:
                row = next(cursor, None)
                if row is None:
                    break
                pending[pool.submit(extract_one, row, output, resume=resume, ocr=ocr,
                                    ocr_device=ocr_device, ocr_language=ocr_language,
                                    tables=tables,
                                    repair_ocr=repair_ocr,
                                    refresh=refresh,
                                    document_timeout=document_timeout)] = row
        fill()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                row = pending.pop(future)
                try:
                    results.append(future.result())
                except Exception as error:
                    results.append({"document_id": row["document_id"], "status": "failed",
                                    "error": str(error)[:300]})
            fill()
    order = {row["document_id"]: index for index, row in enumerate(rows)}
    results.sort(key=lambda row: order[row["document_id"]])
    counts = {status: sum(row["status"] == status for row in results) for status in
              ("extracted", "verified_cached", "review_required", "retry_required",
               "source_unavailable", "failed")}
    report = {"input_manifest": str(Path(manifest).resolve()), "requested": len(rows),
              "workers": workers, "ocr_engine_requested": ocr,
              "ocr_device_requested": ocr_device,
              "ocr_language_requested": ocr_language, "repair_ocr": repair_ocr,
              "refresh": refresh,
              "document_timeout_seconds": document_timeout, "counts": counts,
              "ocr_queue_wait_ms": sum((row.get("ocr_compute") or {}).get("queue_wait_ms", 0) for row in results),
              "ocr_execution_ms": sum((row.get("ocr_compute") or {}).get("execution_ms", 0) for row in results),
              "pages_extracted": sum(row.get("pages", 0) for row in results if row["status"] in
                                     {"extracted", "verified_cached", "review_required"}),
              "pages_ready": sum((row.get("page_status_counts") or {}).get("ready", 0) for row in results),
              "pages_blank": sum((row.get("page_status_counts") or {}).get("blank", 0) for row in results),
              "pages_requiring_review": sum((row.get("page_status_counts") or {}).get("review_required", 0)
              for row in results),
              "documents_requiring_source_review": sum(bool(row.get("requires_source_review")) for row in results),
              "elapsed_seconds": round(time.monotonic()-started, 3), "results": results}
    reports = output / "batch-reports"
    reports.mkdir(exist_ok=True)
    receipt_key = (Path(manifest).read_bytes() +
                   json.dumps({"ocr": ocr, "ocr_device": ocr_device,
                               "ocr_language": ocr_language, "tables": tables,
                               "repair_ocr": repair_ocr, "refresh": refresh,
                               "document_timeout": document_timeout},
                              sort_keys=True).encode())
    receipt = reports / f"batch-{sha256(receipt_key).hexdigest()[:16]}.json"
    report["report_path"] = str(receipt)
    temporary = receipt.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    return report
