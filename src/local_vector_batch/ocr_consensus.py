"""Independent OCR audit of existing Vision pages; never edits evidence packages.

Input JSONL rows contain ``package`` and ``source_pdf`` paths. The source PDF
hash and selected Vision page hash must match the immutable package. This is
an audit signal, not proof that either OCR engine read the source correctly.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import re
import time
import unicodedata


NUMBER = re.compile(r"(?<!\w)\d+(?:[.,:/-]\d+)*(?:\s*%)?(?!\w)")
WORD = re.compile(r"\w+", re.UNICODE)


def recognition_model_for_text(text: str) -> str:
    """Route the dominant alphabetic script, ignoring a short bilingual header."""
    devanagari = sum("\u0900" <= char <= "\u097f" for char in text)
    latin = sum("A" <= char <= "Z" or "a" <= char <= "z" for char in text)
    return "devanagari" if devanagari > latin else "en"


def recognition_receipt(model: str) -> dict:
    from .ocr import _rapidocr_model_sha256
    return {"recognition_model": f"{model}_PP-OCRv5_rec_mobile",
            "recognition_model_sha256": _rapidocr_model_sha256(model),
            "recognition_languages": ["hi", "en"] if model == "devanagari" else ["en"]}


def compare_ocr(vision: str, independent: str, *, vision_confidence: float,
                independent_confidence: float, has_table_or_layout_warning: bool) -> dict:
    """Require near-identical text and exactly identical ordered numeric tokens."""
    a = unicodedata.normalize("NFKC", vision).casefold()
    b = unicodedata.normalize("NFKC", independent).casefold()
    a_words, b_words = WORD.findall(a), WORD.findall(b)
    common = sum((Counter(a_words) & Counter(b_words)).values())
    recall = common / len(a_words) if a_words else 0.0
    precision = common / len(b_words) if b_words else 0.0
    order = None
    metric = "not_run_word_coverage_below_threshold"
    # Repetitive table cells make difflib quadratic. Failed word coverage
    # already proves disagreement; otherwise prefer the small C++ distance
    # implementation, retaining a conservative fallback without that extra.
    if min(recall, precision) >= .98:
        try:
            from rapidfuzz.distance import Levenshtein
            order = Levenshtein.normalized_similarity(a_words, b_words)
            metric = "levenshtein_normalized"
        except ImportError:
            order = SequenceMatcher(None, a_words, b_words, autojunk=False).ratio()
            metric = "difflib_gestalt_ratio"
    a_numbers, b_numbers = NUMBER.findall(a), NUMBER.findall(b)
    numeric_exact = a_numbers == b_numbers
    reasons = []
    if len(a_words) < 30 or len(b_words) < 30:
        reasons.append("too_little_text")
    if min(recall, precision) < .98 or (order is not None and order < .98):
        reasons.append("text_disagreement")
    if not numeric_exact:
        reasons.append("numeric_disagreement")
    if min(vision_confidence, independent_confidence) < .95:
        reasons.append("low_ocr_confidence")
    if has_table_or_layout_warning:
        reasons.append("table_or_layout_review")
    return {"word_recall": round(recall, 5), "word_precision": round(precision, 5),
            "word_order_agreement": round(order, 5) if order is not None else None,
            "word_order_metric": metric, "numeric_exact": numeric_exact,
            "vision_numeric_tokens": a_numbers, "independent_numeric_tokens": b_numbers,
            "consensus_candidate": not reasons, "review_reasons": reasons}


def _audit_partition(item: tuple[str, str, int, int]) -> dict:
    manifest, output, limit, dpi = item
    return audit_manifest(Path(manifest), Path(output), limit=limit, dpi=dpi)


@lru_cache(maxsize=2)
def _reader_for(model: str):
    """Persist recognizers across small jobs within each process."""
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    return RapidOCR(params={"Rec.lang_type": LangRec.DEVANAGARI if model == "devanagari" else LangRec.EN,
        "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Global.log_level": "warning"})


def _page_batches(item: dict, selected: list[int], size: int = 8) -> list[dict]:
    """Bound work units so one dense PDF cannot strand all other workers."""
    if size < 1:
        raise ValueError("page batch size must be positive")
    return [{**item, "pages": selected[start:start + size]}
            for start in range(0, len(selected), size)]


def _source_lines(result, width: int, height: int) -> list[dict]:
    """Retain model order and source geometry for targeted repair, not just text."""
    boxes = result.boxes if result.boxes is not None else ()
    texts = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    return [{"text": str(value), "confidence": float(score),
             "bbox": [min(float(p[0]) for p in polygon) / width,
                      min(float(p[1]) for p in polygon) / height,
                      max(float(p[0]) for p in polygon) / width,
                      max(float(p[1]) for p in polygon) / height]}
            for polygon, value, score in zip(boxes, texts, scores)]


def _unique_receipt_count(paths: list[Path]) -> int:
    """Count current source/page/text receipts once across output and worker copies."""
    identities = set()
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                identity = (row.get("source_pdf_sha256"), row.get("page"),
                            row.get("selected_page_text_sha256"))
                if all(value is not None for value in identity):
                    identities.add(identity)
    return len(identities)


def _parallel_audit(manifest: Path, output: Path, limit: int, dpi: int, workers: int, resume: bool,
                    page_batch_size: int = 8) -> dict:
    """One cached OCR engine per process; each physical page is assigned once."""
    started = time.monotonic()
    batches: list[dict] = []
    remaining = limit
    seen = set()
    parts = output.with_name(output.name + ".parts")
    existing, reused = {}, []
    if resume:
        for path in [output, *sorted(parts.glob("worker-*.output.jsonl"))]:
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = json.loads(line)
                        existing[(row["source_pdf_sha256"], row["page"])] = row
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or remaining == 0:
            continue
        item = json.loads(line)
        selected = []
        package = Path(item["package"])
        receipt = json.loads((package / "report.json").read_text())
        pdf_sha = receipt["source_pdf_sha256"]
        checked_source = False
        requested = set(item["pages"]) if "pages" in item else None
        for raw in (Path(item["package"]) / "pages.jsonl").read_text(encoding="utf-8").splitlines():
            page = json.loads(raw)
            identity = (pdf_sha, page["page"])
            if (page.get("method") == "vision" and page.get("status") == "review_required"
                    and (requested is None or page["page"] in requested) and identity not in seen and remaining):
                seen.add(identity)
                remaining -= 1
                prior = existing.get(identity)
                expected_model = recognition_model_for_text(page["text"])
                if (prior and prior.get("selected_page_text_sha256") == page["text_sha256"] and
                        prior.get("render_dpi") == dpi and
                        prior.get("independent", {}).get("version") == version("rapidocr") and
                        prior.get("independent", {}).get("recognition_model") ==
                            recognition_receipt(expected_model)["recognition_model"] and
                        prior.get("independent", {}).get("recognition_model_sha256") ==
                            recognition_receipt(expected_model)["recognition_model_sha256"] and
                        hashlib.sha256(prior["independent"]["text"].encode()).hexdigest() == prior["independent"]["text_sha256"]):
                    if not checked_source:
                        from .evidence_package import verify_package
                        verify_package(package, source_pdf=Path(item["source_pdf"]))
                        checked_source = True
                    prior["comparison"] = compare_ocr(page["text"], prior["independent"]["text"],
                        vision_confidence=float(page.get("ocr", {}).get("mean_confidence") or 0),
                        independent_confidence=prior["independent"]["mean_confidence"],
                        has_table_or_layout_warning=prior.get("table_on_page", False) or
                            "ocr_layout_review" in prior.get("source_warnings", []))
                    reused.append(prior)
                else:
                    selected.append(page["page"])
        if selected:
            batches.extend(_page_batches(item, selected, page_batch_size))
    if not seen:
        raise ValueError("no Vision review pages found")
    parts.mkdir(parents=True, exist_ok=True)
    # Consolidate validated receipts before replacing per-worker streams.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in reused), encoding="utf-8")
    for previous in parts.glob("worker-*.output.jsonl"):
        previous.unlink()
    jobs = []
    for index, batch in enumerate(batches):
        source = parts / f"worker-{index}.input.jsonl"
        destination = parts / f"worker-{index}.output.jsonl"
        source.write_text(json.dumps(batch) + "\n", encoding="utf-8")
        jobs.append((str(source), str(destination), len(batch["pages"]), dpi))
    counts, audited = Counter(), len(reused)
    for row in reused:
        comparison = row["comparison"]
        counts["consensus_candidate" if comparison["consensus_candidate"] else "review_required"] += 1
        counts.update(comparison["review_reasons"])
    output.parent.mkdir(parents=True, exist_ok=True)
    progress = output.with_name(output.name + ".progress.json")
    def update():
        # Finished worker streams are appended to output but retained in parts
        # for resumption. Count each source-bound physical page only once.
        completed = _unique_receipt_count([output, *parts.glob("worker-*.output.jsonl")])
        elapsed = time.monotonic()-started
        receipt = {"planned_pages": len(seen), "completed_pages": completed,
            "workers": min(workers, len(jobs)), "page_batches": len(jobs), "elapsed_seconds": round(elapsed, 2),
            "resumed_pages": len(reused),
            "pages_per_second": round((completed-len(reused))/max(elapsed, .001), 3), "pages_promoted": 0}
        temporary = progress.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        temporary.replace(progress)
    if not jobs:
        update()
        return {"audited_pages": audited, "counts": dict(counts), "workers": 0, "resumed_pages": len(reused),
                "elapsed_seconds": round(time.monotonic()-started, 2), "output": str(output), "packages_modified": 0}
    with output.open("a", encoding="utf-8") as sink, ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = {pool.submit(_audit_partition, job): job for job in jobs}
        update()
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                report = future.result()
                audited += report["audited_pages"]
                counts.update(report["counts"])
                sink.write(Path(futures[future][1]).read_text(encoding="utf-8"))
                sink.flush()
            update()
    if audited != len(seen) or _unique_receipt_count([output]) != len(seen):
        raise RuntimeError(f"OCR comparison coverage mismatch: {audited}/{len(seen)}")
    return {"audited_pages": audited, "counts": dict(counts), "workers": min(workers, len(jobs)), "page_batches": len(jobs), "resumed_pages": len(reused),
            "elapsed_seconds": round(time.monotonic()-started, 2), "output": str(output), "packages_modified": 0}


def audit_manifest(manifest: Path, output: Path, *, limit: int = 50, dpi: int = 180, workers: int = 1,
                   resume: bool = False, page_batch_size: int = 8) -> dict:
    from filelock import FileLock
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A resumable stream must have exactly one owner, including during restart.
    with FileLock(str(output) + ".lock", timeout=0):
        return _audit_manifest_unlocked(manifest, output, limit=limit, dpi=dpi, workers=workers,
                                        resume=resume, page_batch_size=page_batch_size)


def _audit_manifest_unlocked(manifest: Path, output: Path, *, limit: int, dpi: int, workers: int,
                             resume: bool, page_batch_size: int = 8) -> dict:
    if limit < 1 or dpi < 72 or dpi > 300 or workers < 1 or workers > 16:
        raise ValueError("limit must be positive and dpi must be 72..300")
    if type(page_batch_size) is not int or not 1 <= page_batch_size <= 64:
        raise ValueError("page_batch_size must be 1..64")
    if workers > 1 or resume:
        return _parallel_audit(Path(manifest), Path(output), limit, dpi, workers, resume, page_batch_size)
    try:
        from rapidocr import RapidOCR
        import pypdfium2 as pdfium
    except ImportError as error:
        raise RuntimeError("install local-vector-batch[pdf,ocr-consensus]") from error
    from .evidence_package import verify_package
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    counts = Counter()
    seen = 0
    with output.open("w", encoding="utf-8") as sink:
        for line in Path(manifest).read_text(encoding="utf-8").splitlines():
            if seen >= limit:
                break
            if not line.strip():
                continue
            item = json.loads(line)
            package, source = Path(item["package"]), Path(item["source_pdf"])
            verify_package(package, source_pdf=source)
            report = json.loads((package / "report.json").read_text(encoding="utf-8"))
            issues = report.get("issues", [])
            tables = json.loads((package / "tables.json").read_text(encoding="utf-8"))
            table_pages = {row["page"] for row in tables}
            with pdfium.PdfDocument(str(source)) as pdf:
                for raw in (package / "pages.jsonl").read_text(encoding="utf-8").splitlines():
                    if seen >= limit:
                        break
                    page = json.loads(raw)
                    if (page.get("method") != "vision" or page.get("status") != "review_required" or
                            ("pages" in item and page["page"] not in item["pages"])):
                        continue
                    if page["page"] > len(pdf):
                        continue
                    seen += 1
                    t0 = time.monotonic()
                    physical = pdf[page["page"] - 1]
                    bitmap = physical.render(scale=dpi/72)
                    image = bitmap.to_pil().copy()
                    bitmap.close()
                    physical.close()
                    model = recognition_model_for_text(page["text"])
                    result = _reader_for(model)(image)
                    source_image = BytesIO()
                    image.save(source_image, format="PNG")
                    text = "\n".join(result.txts if result.txts is not None else ())
                    scores = [float(x) for x in (result.scores if result.scores is not None else ())]
                    vision_conf = float(page.get("ocr", {}).get("mean_confidence") or 0)
                    independent_conf = sum(scores) / len(scores) if scores else 0
                    related = [x["kind"] for x in issues if x.get("page") == page["page"]]
                    layout_warning = page["page"] in table_pages or any(
                        kind in {"ocr_layout_review", "table_extraction_failed"} for kind in related)
                    comparison = compare_ocr(page["text"], text,
                        vision_confidence=vision_conf, independent_confidence=independent_conf,
                        has_table_or_layout_warning=layout_warning)
                    row = {"document_id": report["document_id"], "source_url": report.get("source_url"),
                           "source_pdf_sha256": report["source_pdf_sha256"], "page": page["page"],
                           "page_id": page["id"], "selected_page_text_sha256": page["text_sha256"],
                           "vision": {"engine": "macos_vision", "mean_confidence": vision_conf,
                                      "text_sha256": hashlib.sha256(page["text"].encode()).hexdigest()},
                           "independent": {"engine": "rapidocr_onnx", "version": version("rapidocr"),
                                           **recognition_receipt(model),
                                           "mean_confidence": round(independent_conf, 5),
                                           "min_confidence": round(min(scores), 5) if scores else 0,
                                           "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                           "text": text,
                                           "lines": _source_lines(result, image.width, image.height),
                                           "geometry": "normalized_top_left_line_boxes"},
                           "render_dpi": dpi,
                           "source_image_sha256": hashlib.sha256(source_image.getvalue()).hexdigest(),
                           "render_width": image.width, "render_height": image.height,
                           "render_engine": "pypdfium2",
                           "source_warnings": related, "table_on_page": page["page"] in table_pages,
                           "comparison": comparison, "elapsed_ms": round((time.monotonic() - t0) * 1000)}
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                    sink.flush()
                    counts["consensus_candidate" if comparison["consensus_candidate"] else "review_required"] += 1
                    for reason in comparison["review_reasons"]:
                        counts[reason] += 1
    return {"audited_pages": seen, "counts": dict(counts), "elapsed_seconds": round(time.monotonic()-started, 2),
            "output": str(output), "packages_modified": 0}
