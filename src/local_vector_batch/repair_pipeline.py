"""Resumable, source-bound repair jobs for existing PDF evidence packages.

This module only prepares verified local candidates. A project adapter decides
when to publish a candidate and update its search index.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from io import BytesIO
import json
import shutil
import sqlite3
import time

from .evidence_package import verify_package
from .quality_gate import assess_package, write_quality_report
from .quality_routes import matching_routes


TERMINAL = {"ready_for_publication", "awaiting_evidence", "repair_required", "source_blocked"}
REPAIR_HANDLER_REVISION = 8


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _manifest_path(value: str, manifest: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    for base in (Path.cwd(), *manifest.resolve().parents):
        candidate = base / path
        if candidate.exists():
            return candidate.resolve()
    return (manifest.parent / path).resolve()


def _engine_family(engine: str | None) -> str:
    """Treat runtime labels for the same OCR reader as one provenance family."""
    value = str(engine or "").casefold().replace("-", "_")
    if "rapidocr" in value or "pp_ocr" in value:
        return "rapidocr"
    if "vision" in value:
        return "apple_vision"
    if "tesseract" in value:
        return "tesseract"
    if value in {"mlx_vlm", "qwen_vl", "vision_language_model"}:
        return "vlm"
    if value in {"pdf_text", "pypdf", "pdfium", "native"}:
        return "native_pdf_text"
    return ""


def _selected_ocr_engine(page: dict, requested_engine: str) -> str:
    """Bind the selected text to the actual OCR result before using its reader identity."""
    ocr = page.get("ocr") or {}
    engine = ocr.get("engine")
    family = _engine_family(engine)
    lines = ocr.get("lines")
    if (not family or family == "native_pdf_text" or
            family != _engine_family(page.get("method")) or
            family != _engine_family(requested_engine) or
            not isinstance(lines, list) or not lines or
            any(not isinstance(line, dict) or not isinstance(line.get("text"), str) for line in lines) or
            ocr.get("text") != page.get("text") or
            "\n".join(line["text"] for line in lines) != page.get("text")):
        raise ValueError("selected OCR text is not bound to the stated reader and lines")
    return engine


def _bound_independent_text(receipt_path: Path | dict, source: Path, *, source_sha: str,
                            page: int, original_text_sha: str, candidate_engine: str,
                            reuse_source_ocr: bool = False) -> str:
    """Accept a third reader only when its full-page image matches the PDF bytes."""
    import pypdfium2 as pdfium

    receipt = (receipt_path if isinstance(receipt_path, dict) else
               json.loads(Path(receipt_path).read_text(encoding="utf-8")))
    if sha256(Path(source).read_bytes()).hexdigest() != source_sha:
        raise ValueError("third-reader source PDF SHA mismatch")
    cached = receipt.get("independent")
    reader = cached if isinstance(cached, dict) else receipt
    engine = reader.get("engine")
    model = reader.get("recognition_model", reader.get("model"))
    revision = reader.get("recognition_model_sha256", reader.get("model_revision"))
    selected_sha = (receipt.get("original_text_sha256") or receipt.get("selected_page_text_sha256") or
                    receipt.get("selected_text_sha256"))
    if ((receipt.get("source_pdf_sha256") or receipt.get("sha")) != source_sha or
            receipt.get("page") != page or
            (selected_sha != original_text_sha and not (reuse_source_ocr and cached)) or
            not _engine_family(engine) or not _engine_family(candidate_engine) or
            _engine_family(engine) == _engine_family(candidate_engine) or
            not engine or not model or not revision):
        raise ValueError("third-reader receipt provenance or independence mismatch")
    if cached and (receipt.get("render_engine") != "pypdfium2" or
                   engine != "rapidocr_onnx" or not reader.get("version") or
                   not isinstance(revision, str) or len(revision) != 64):
        raise ValueError("cached independent OCR model or source render provenance missing")
    dpi = receipt.get("render_dpi")
    if type(dpi) is not int or not 120 <= dpi <= 360:
        raise ValueError("third-reader receipt requires explicit source render DPI")
    image_path = receipt.get("source_image_path", receipt.get("body_image"))
    image_sha = receipt.get("source_image_sha256", receipt.get("body_image_sha256"))
    if not cached and (not image_path or not image_sha or sha256(Path(image_path).read_bytes()).hexdigest() != image_sha):
        raise ValueError("third-reader source image SHA mismatch")
    pdf = pdfium.PdfDocument(str(source))
    try:
        source_page = pdf[page-1]
        try:
            buffer = BytesIO()
            source_page.render(scale=dpi / 72).to_pil().save(buffer, format="PNG")
        finally:
            source_page.close()
    finally:
        pdf.close()
    if cached and image_sha and sha256(buffer.getvalue()).hexdigest() != image_sha:
        raise ValueError("cached third-reader source image SHA mismatch")
    if not cached and sha256(buffer.getvalue()).hexdigest() != image_sha:
        raise ValueError("third-reader image is not the original physical PDF page")
    text = reader.get("text", reader.get("independent_text", reader.get("body_text")))
    text_sha = reader.get("text_sha256", reader.get("independent_text_sha256", reader.get("body_text_sha256")))
    if not isinstance(text, str) or not text.strip() or sha256(text.encode()).hexdigest() != text_sha:
        raise ValueError("third-reader transcription SHA mismatch")
    if receipt.get("footer_text"):
        raise ValueError("third-reader separate crop requires its own source-bound geometry proof")
    return text


def reader_provenance(receipt_path: Path | dict, source: Path, candidate: dict,
                      independent_text: str) -> dict:
    """Persist the exact OCR identities and source render behind a fresh gate."""
    import pypdfium2 as pdfium
    receipt = receipt_path if isinstance(receipt_path, dict) else json.loads(Path(receipt_path).read_text())
    reader = receipt.get("independent", receipt)
    selected = candidate.get("ocr", {})
    candidate_engine = selected.get("engine", candidate.get("method"))
    reader_engine = reader.get("engine")
    if not _engine_family(candidate_engine) or not _engine_family(reader_engine) or \
            _engine_family(candidate_engine) == _engine_family(reader_engine):
        raise ValueError("OCR reader provenance is not independent")
    source_sha = sha256(Path(source).read_bytes()).hexdigest()
    if receipt.get("source_pdf_sha256") != source_sha:
        raise ValueError("OCR receipt source PDF changed")
    dpi = receipt.get("render_dpi")
    if type(dpi) is not int or not 120 <= dpi <= 360:
        raise ValueError("OCR render DPI missing")
    with pdfium.PdfDocument(str(source)) as pdf:
        physical = pdf[candidate["page"] - 1]
        try:
            buffer = BytesIO()
            physical.render(scale=dpi / 72).to_pil().save(buffer, format="PNG")
        finally:
            physical.close()
    rendered_sha = sha256(buffer.getvalue()).hexdigest()
    claimed_sha = receipt.get("source_image_sha256")
    if claimed_sha and claimed_sha != rendered_sha:
        raise ValueError("OCR receipt source image changed")
    return {"source_pdf_sha256": source_sha, "page": candidate["page"],
            "selected_engine": candidate_engine,
            "selected_engine_family": _engine_family(candidate_engine),
            "selected_model": selected.get("model", selected.get("recognition_model")),
            "selected_page_text_sha256": candidate["text_sha256"],
            "independent_engine": reader_engine,
            "independent_engine_family": _engine_family(reader_engine),
            "independent_model": reader.get("model", reader.get("recognition_model")),
            "independent_model_revision": reader.get("model_revision", reader.get("recognition_model_sha256")),
            "independent_text_sha256": sha256(independent_text.encode()).hexdigest(),
            "independent_receipt_sha256": _digest(receipt), "render_dpi": dpi,
            "render_engine": receipt.get("render_engine", "source_crop"),
            "source_image_sha256": rendered_sha,
            "source_image_claimed_in_receipt": bool(claimed_sha)}


def capabilities() -> dict:
    """Report executable routes separately from routes requiring outside proof."""
    return {
        "native_consensus": "source-bound native receipt; candidate staged and re-gated",
        "column_order": "source-bound OCR column-order receipt; every line preserved and re-gated",
        "ocr_region": "source-bound crop with distinct local OCR/VLM agreement; full page re-gated",
        "ocr_text": "targeted RapidOCR EN/Devanagari; distinct original Vision text and source raster re-gated",
        "verify_selected_ocr": "selected RapidOCR checked against source-bound alternate reader; no OCR rerun",
        "table_or_layout": "awaiting source-bound cell/region proof; no automatic text promotion",
        "source_blankness": "awaiting source visual decision; no automatic blank approval",
        "publication": "staged candidate only; external corpus/index adapter required",
    }


def _route(page: dict, quality: dict, native_receipt: str | None,
           column_receipt: str | None = None, region_receipt: str | None = None) -> str:
    if region_receipt and page.get("method") in {"vision", "rapidocr"}:
        return "ocr_region"
    if column_receipt and page.get("method") != "pdf_text":
        return "column_order"
    if native_receipt and page.get("method") == "pdf_text":
        return "native_consensus"
    if page.get("method") == "rapidocr" and page.get("status") == "review_required":
        return "verify_selected_ocr"
    if quality.get("components", {}).get("source_identity", {}).get("state") == "fail":
        return "source_blocked"
    if not page.get("text", "").strip():
        return "source_blankness"
    if page.get("method") == "pdf_text":
        return "table_or_layout"
    for rule in matching_routes(quality):
        execution = rule.get("execution", "awaiting_evidence")
        if execution == "ocr_text" and page.get("method") == "vision" and page.get("status") == "review_required":
            return "ocr_text"
        if execution == "awaiting_evidence":
            return "table_or_layout"
    return "table_or_layout"


def _source_sha(package: Path, source: Path) -> str:
    verify_package(package, source_pdf=source)
    doc = json.loads((package / "evidence-package.json").read_text(encoding="utf-8"))["document"]
    return doc["pdf_sha256"]


def prepare_jobs(manifest: Path, *, limit: int = 100, config: dict | None = None) -> list[dict]:
    """Validate original PDF/package/page before deriving stable job identities."""
    if not 1 <= limit <= 100_000:
        raise ValueError("limit must be 1..100000")
    config = config or {}
    route_policy_sha = sha256(files("local_vector_batch").joinpath("quality_routes.json").read_bytes()).hexdigest()
    independent_cache = {}
    if config.get("independent_cache"):
        for cached in _rows(_manifest_path(config["independent_cache"], Path(manifest))):
            identity = (cached.get("source_pdf_sha256"), cached.get("page"), cached.get("selected_page_text_sha256"))
            independent_cache[identity] = cached
    jobs, seen = [], set()
    manifest = Path(manifest)
    for row in _rows(manifest):
        package, source = _manifest_path(row["package"], manifest), _manifest_path(row["source_pdf"], manifest)
        source_sha = _source_sha(package, source)
        if row.get("source_pdf_sha256") and row["source_pdf_sha256"] != source_sha:
            raise ValueError("manifest PDF SHA differs from verified source")
        pages = {item["page"]: item for item in _rows(package / "pages.jsonl")}
        quality_path = _manifest_path(row["quality_report"], manifest)
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        if quality.get("source_pdf_sha256") != source_sha:
            raise ValueError("quality report PDF SHA differs from verified source")
        qpages = {item["page"]: item for item in quality["results"]}
        for page_no in row["pages"]:
            if type(page_no) is not int or page_no not in pages or page_no not in qpages:
                raise ValueError("manifest page absent from original package or quality report")
            original, prior = pages[page_no], qpages[page_no]
            if prior.get("page_text_sha256") != original["text_sha256"]:
                raise ValueError("stale quality report page text hash")
            if prior.get("outcome") != "repair_required":
                continue
            if original.get("review_decision"):
                continue
            review_sidecar = row.get("review_sidecar")
            review_sidecar = str(_manifest_path(review_sidecar, manifest)) if review_sidecar else None
            if review_sidecar and Path(review_sidecar).is_file():
                decisions = _rows(Path(review_sidecar))
                if any(decision.get("page") == page_no and
                       decision.get("source_pdf_sha256") == source_sha for decision in decisions):
                    continue
            receipt = (row.get("native_receipts") or {}).get(str(page_no))
            receipt = str(_manifest_path(receipt, manifest)) if receipt else None
            column_receipt = (row.get("column_order_receipts") or {}).get(str(page_no))
            column_receipt = str(_manifest_path(column_receipt, manifest)) if column_receipt else None
            region_values = (row.get("region_receipts") or {}).get(str(page_no))
            region_receipts = ([str(_manifest_path(value, manifest)) for value in
                                (region_values if isinstance(region_values, list) else [region_values])]
                               if region_values else [])
            independent_receipt = (row.get("independent_receipts") or {}).get(str(page_no))
            independent_receipt = str(_manifest_path(independent_receipt, manifest)) if independent_receipt else None
            independent_inline = (independent_cache.get((source_sha, page_no, original["text_sha256"]))
                                  if not independent_receipt else None)
            route = _route(original, prior, receipt, column_receipt,
                           region_receipts[0] if region_receipts else None)
            key = _digest({"source_pdf_sha256": source_sha, "page": page_no,
                           "original_text_sha256": original["text_sha256"],
                           "policy_sha256": quality.get("policy_sha256"),
                           "route": route, "config": config,
                           "route_policy_sha256": route_policy_sha,
                           "handler_revision": REPAIR_HANDLER_REVISION,
                           "native_receipt_sha256": sha256(Path(receipt).read_bytes()).hexdigest() if receipt else None,
                           "column_receipt_sha256": sha256(Path(column_receipt).read_bytes()).hexdigest()
                           if column_receipt else None,
                           "region_receipt_sha256": [sha256(Path(path).read_bytes()).hexdigest()
                                                     for path in region_receipts],
                           "independent_receipt_sha256": sha256(Path(independent_receipt).read_bytes()).hexdigest()
                           if independent_receipt else _digest(independent_inline) if independent_inline else None})
            if key in seen:
                continue
            seen.add(key)
            jobs.append({"key": key, "route": route, "source_pdf_sha256": source_sha,
                         "page": page_no, "original_text_sha256": original["text_sha256"],
                         "package": str(package), "source_pdf": str(source),
                         "quality_report": str(quality_path), "native_receipt": receipt,
                         "column_order_receipt": column_receipt,
                         "region_receipts": region_receipts,
                         "independent_receipt": independent_receipt,
                         "independent_receipt_inline": independent_inline,
                         "review_sidecar": review_sidecar})
            if len(jobs) >= limit:
                return jobs
    return jobs


def _run_job(job: dict, staging_root: str, config: dict) -> dict:
    """One isolated page attempt; never writes the watched source package."""
    from .native_repair import repair_native_page
    from .pdf_repair import repair_pdf_pages, apply_column_order_repair, apply_ocr_region_repair

    started = time.monotonic()
    route = job["route"]
    base = {"key": job["key"], "route": route, "source_pdf_sha256": job["source_pdf_sha256"],
            "page": job["page"], "original_text_sha256": job["original_text_sha256"]}
    if route in {"table_or_layout", "source_blankness", "source_blocked"}:
        return {**base, "status": "source_blocked" if route == "source_blocked" else "awaiting_evidence",
                "reason": "source-bound structured/visual proof required", "elapsed_ms": 0}
    third_reader = job.get("independent_receipt_inline") or job.get("independent_receipt")
    if route == "verify_selected_ocr" and not third_reader:
        return {**base, "status": "awaiting_evidence", "reason": "source-bound alternate OCR receipt required",
                "elapsed_ms": 0}
    ocr_engine = config.get("ocr_engine", "rapidocr")
    ocr_dpi = config.get("ocr_dpi", 180)
    if route == "ocr_text" and ocr_engine == "vision" and not third_reader:
        return {**base, "status": "awaiting_evidence",
                "reason": "same-engine Vision re-read requires distinct source-bound alternate receipt",
                "elapsed_ms": 0}
    target = Path(staging_root) / job["key"] / "package"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(job["package"], target)
    source, page_no = Path(job["source_pdf"]), job["page"]
    old = {p["page"]: p for p in _rows(target / "pages.jsonl")}[page_no]
    try:
        verify_package(target, source_pdf=source)
        if old["text_sha256"] != job["original_text_sha256"]:
            raise ValueError("source package page changed after repair job was queued")
        sidecar = job.get("review_sidecar")
        if sidecar and Path(sidecar).is_file() and any(
                row.get("page") == page_no and row.get("source_pdf_sha256") == job["source_pdf_sha256"]
                for row in _rows(Path(sidecar))):
            raise ValueError("source-reviewed decision now exists for this page")
        if route == "native_consensus":
            receipt = json.loads(Path(job["native_receipt"]).read_text(encoding="utf-8"))
            if receipt.get("original_text_sha256") != old["text_sha256"]:
                raise ValueError("native receipt page text hash changed")
            repair_native_page(target, receipt)
            result = assess_package(target, source, pages=[page_no], derive_ocr_source_raster=True)
        elif route == "column_order":
            receipt = json.loads(Path(job["column_order_receipt"]).read_text(encoding="utf-8"))
            if receipt.get("original_page_text_sha256") != old["text_sha256"]:
                raise ValueError("column receipt page text hash changed")
            apply_column_order_repair(target, receipt)
            result = assess_package(target, source, pages=[page_no],
                                    independent_by_page={page_no: receipt["independent_text"]},
                                    derive_ocr_source_raster=True)
        elif route == "ocr_region":
            receipts = [json.loads(Path(path).read_text(encoding="utf-8"))
                        for path in job["region_receipts"]]
            receipt = receipts[0]
            if any(item.get("original_page_text_sha256") != old["text_sha256"] for item in receipts):
                raise ValueError("region receipt page text hash changed")
            apply_ocr_region_repair(target, receipts)
            independent = _bound_independent_text(receipt["independent_full_receipt"], source,
                source_sha=job["source_pdf_sha256"], page=page_no,
                original_text_sha=receipt["independent_original_text_sha256"],
                candidate_engine=old.get("ocr", {}).get("engine", old["method"]))
            result = assess_package(target, source, pages=[page_no],
                                    independent_by_page={page_no: independent},
                                    derive_ocr_source_raster=True)
        elif route == "verify_selected_ocr":
            if old.get("method") != "rapidocr":
                raise ValueError("verification-only route requires selected RapidOCR page")
            independent = _bound_independent_text(third_reader, source,
                            source_sha=job["source_pdf_sha256"], page=page_no,
                            original_text_sha=old["text_sha256"],
                            candidate_engine=old.get("ocr", {}).get("engine", "rapidocr"))
            result = assess_package(target, source, pages=[page_no],
                                    independent_by_page={page_no: independent},
                                    derive_ocr_source_raster=True)
        else:
            if old.get("method") != "vision" or not old.get("text", "").strip():
                raise ValueError("distinct nonempty Vision reader required for OCR repair proof")
            language = "eng+hin" if any("\u0900" <= c <= "\u097f" for c in old["text"]) else "eng"
            repair_pdf_pages(source, target, {page_no}, ocr=ocr_engine,
                             ocr_language=language, ocr_dpi=ocr_dpi, tables=True)
            candidate = {p["page"]: p for p in _rows(target / "pages.jsonl")}[page_no]
            candidate_engine = _selected_ocr_engine(candidate, ocr_engine)
            if (_engine_family(candidate_engine) == _engine_family(old.get("ocr", {}).get("engine")) and
                    not third_reader):
                raise ValueError("candidate OCR reader is not independent of original")
            independent = (_bound_independent_text(third_reader, source,
                            source_sha=job["source_pdf_sha256"], page=page_no,
                            original_text_sha=old["text_sha256"],
                            candidate_engine=candidate_engine)
                           if third_reader else old["text"])
            result = assess_package(target, source, pages=[page_no],
                                    independent_by_page={page_no: independent},
                                    derive_ocr_source_raster=True)
        verify_package(target, source_pdf=source)
        scored = result["results"][0]
        if third_reader:
            third = (third_reader if isinstance(third_reader, dict) else
                     json.loads(Path(third_reader).read_text(encoding="utf-8")))
            reader = third.get("independent", third)
            scored["independent_recognition"] = {
                "engine": reader["engine"], "model": reader.get("model", reader.get("recognition_model")),
                "model_revision": reader.get("model_revision", reader.get("recognition_model_sha256")),
                "receipt_sha256": _digest(third),
                "source_image_sha256": third.get("source_image_sha256", third.get("body_image_sha256")),
            }
        receipt_path = target.parent / "quality.json"
        write_quality_report(result, receipt_path)
        if scored["outcome"] == "auto_accepted":
            if sidecar and Path(sidecar).is_file() and any(
                    row.get("page") == page_no and row.get("source_pdf_sha256") == job["source_pdf_sha256"]
                    for row in _rows(Path(sidecar))):
                raise ValueError("source-reviewed decision arrived during repair")
            status = "ready_for_publication"
        else:
            status = ("repair_required" if third_reader else
                      "awaiting_evidence" if route == "ocr_text" else "repair_required")
        return {**base, "status": status, "reason": scored.get("failed_components", []),
                "candidate_package": str(target),
                "quality_receipt": str(receipt_path),
                "receipt_outcome": scored["outcome"],
                "candidate_text_sha256": scored["page_text_sha256"],
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except Exception as error:
        return {**base, "status": "repair_required", "reason": str(error)[:300],
                "candidate_package": str(target),
                "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _run_source_group(jobs: list[dict], staging_root: str, config: dict) -> list[dict]:
    """Keep pages from one original PDF in one worker without sharing mutations."""
    return [_run_job(job, staging_root, config) for job in jobs]


def run_repairs(manifest: Path, output: Path, *, workers: int = 4, limit: int = 100,
                config: dict | None = None) -> dict:
    """Run a bounded queue with durable terminal states and no retry loop."""
    started = time.monotonic()
    if not 1 <= workers <= 16:
        raise ValueError("workers must be 1..16")
    config = config or {}
    if (config.get("ocr_engine", "rapidocr") not in {"rapidocr", "vision"} or
            type(config.get("ocr_dpi", 180)) is not int or not 72 <= config.get("ocr_dpi", 180) <= 300):
        raise ValueError("repair OCR configuration requires vision or rapidocr and DPI 72..300")
    source_batch_pages = config.get("source_batch_pages", 4)
    if type(source_batch_pages) is not int or not 1 <= source_batch_pages <= 32:
        raise ValueError("source_batch_pages must be an integer in 1..32")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    jobs = prepare_jobs(Path(manifest), limit=limit, config=config)
    db = sqlite3.connect(output / "repair-jobs.sqlite")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL)")
        # Early versions mislabeled multiprocessing spawn failures as evidence
        # repairs. Preserve the receipt while making those jobs retryable.
        db.execute("UPDATE jobs SET status='retry_required', result=json_set(result,'$.status','retry_required') "
                   "WHERE status='repair_required' AND json_valid(result) "
                   "AND json_extract(result,'$.reason') LIKE '%process in the process pool was terminated abruptly%'")
        db.commit()
        pending = []
        for job in jobs:
            found = db.execute("SELECT status FROM jobs WHERE key=?", (job["key"],)).fetchone()
            if found and found[0] in TERMINAL:
                continue
            pending.append(job)
            db.execute("INSERT INTO jobs(key,status,result,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(key) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",
                       (job["key"], "queued", None, datetime.now(timezone.utc).isoformat()))
        db.commit()
        groups: dict[str, list[dict]] = {}
        for job in pending:
            groups.setdefault(job["source_pdf_sha256"], []).append(job)
        # A large PDF must not occupy one worker while every other worker is
        # idle. Jobs read the source package and write to distinct keyed
        # candidate directories, so bounded groups can run independently.
        work = [group[start:start + source_batch_pages]
                for group in groups.values() for start in range(0, len(group), source_batch_pages)]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_source_group, group, str(output / "candidates"), config): group
                       for group in work}
            for future in as_completed(futures):
                group = futures[future]
                try:
                    results = future.result()
                except Exception as error:
                    results = [{"key": job["key"], "status": "retry_required", "reason": str(error)[:300]}
                               for job in group]
                for result in results:
                    db.execute("UPDATE jobs SET status=?,result=?,updated_at=? WHERE key=?",
                               (result["status"], json.dumps(result, ensure_ascii=False),
                                datetime.now(timezone.utc).isoformat(), result["key"]))
                db.commit()
        rows = [json.loads(raw) for (raw,) in db.execute(
            "SELECT result FROM jobs WHERE key IN (" + ",".join("?" for _ in jobs) + ") AND result IS NOT NULL",
            [job["key"] for job in jobs])] if jobs else []
        counts = Counter(row["status"] for row in rows)
        elapsed = time.monotonic() - started
        pending_keys = {job["key"] for job in pending}
        fresh_ready = sum(row.get("status") == "ready_for_publication" and
                          row["key"] in pending_keys for row in rows)
        return {"schema_version": 1, "jobs_selected": len(jobs), "jobs_run": len(pending),
                "elapsed_seconds": round(elapsed, 3), "new_ready_pages": fresh_ready,
                "attempted_pages_per_second": round(len(pending) / elapsed, 3) if pending and elapsed else 0,
                "counts": dict(counts), "capabilities": capabilities(), "results": rows,
                "state_db": str(output / "repair-jobs.sqlite")}
    finally:
        db.close()


def repair_status(output: Path) -> dict:
    """Read durable job progress without launching new work."""
    path = Path(output) / "repair-jobs.sqlite"
    if not path.is_file():
        return {"schema_version": 1, "attempt_counts": {}, "current_page_counts": {},
                "attempts": 0, "current_pages": [], "state_db": str(path)}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        attempts = dict(db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        rows = db.execute("SELECT key, status, result, updated_at FROM jobs WHERE result IS NOT NULL "
                          "ORDER BY updated_at, key").fetchall()
    latest = {}
    for key, status, raw, updated_at in rows:
        result = json.loads(raw)
        source, page, text_sha = (result.get("source_pdf_sha256"), result.get("page"),
                                  result.get("original_text_sha256"))
        if not source or type(page) is not int or not text_sha:
            continue  # old infrastructure failures have no trustworthy page identity
        latest[(source, page, text_sha)] = {"source_pdf_sha256": source, "page": page,
                                            "original_text_sha256": text_sha,
                                            "status": status, "route": result.get("route"),
                                            "job_key": key, "updated_at": updated_at}
    pages = sorted(latest.values(), key=lambda row: (row["source_pdf_sha256"], row["page"], row["original_text_sha256"]))
    current = dict(Counter(row["status"] for row in pages))
    return {"schema_version": 1, "attempt_counts": attempts, "current_page_counts": current,
            "attempts": sum(attempts.values()), "current_pages": pages,
            "current_page_total": len(pages), "state_db": str(path)}
