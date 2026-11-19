"""Page readings computed ahead of extraction, so the page ranges of one long scan can be read on several machines.

`read_pages` reads a range of physical pages with the same recognizer extract_pdf would build and writes one JSONL file:
a header binding the readings to the source PDF and the recognizer, then one row per page read. `load` checks those
bindings against the recognizer of the run that uses them; extract_pdf then takes a page's reading instead of rendering
and reading the page again. Pages without a reading are read as before, so a missing range only costs time.

Only pages extract_pdf would offer to OCR on their native text are read: no native text, fewer than `max_native_chars`
characters on a nonblank page, or garbled glyphs. Pages offered for a parser disagreement alone are left to the run.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SCHEMA = "pdf2data.ocr-readings.v1"
RESULT_KEYS = {"engine", "text", "lines", "mean_confidence", "render_dpi"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def reading_files(paths) -> list[Path]:
    """Files named, or the .jsonl files of directories named, in a stable order."""
    files: list[Path] = []
    for path in paths or []:
        path = Path(path)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix == ".jsonl" and p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f"OCR readings not found: {path}")
    return files


def read_pages(source: Path, first: int, last: int, output: Path, *, ocr: str = "rapidocr", language: str = "eng",
               max_native_chars: int = 80, cpu_threads: int = 2) -> dict:
    from pypdf import PdfReader
    from .ocr import LocalOCR
    from .pdf_ingest import _open_pdf_with_page_recovery, _release_page_after_use, text_quality

    source, output = Path(source), Path(output)
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise ValueError("page range must satisfy 1 <= first <= last")
    if type(max_native_chars) is not int or not 1 <= max_native_chars <= 200:
        raise ValueError("max_native_chars must be 1..200")
    sha = _sha(source)
    issues: list[dict] = []
    try:
        secondary = PdfReader(source).pages
    except Exception:
        secondary = None
    started = time.monotonic()
    read, skipped, failed, execution_ms = [], 0, [], 0
    options = {} if cpu_threads == 2 else {"cpu_threads": cpu_threads}
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    with LocalOCR(ocr, language, **options) as recognizer, \
            _open_pdf_with_page_recovery(source, secondary, issues) as pdf, partial.open("w", encoding="utf-8") as sink:
        total = len(pdf.pages)
        if last > total:
            raise ValueError(f"page range ends after the last physical page ({total})")
        sink.write(json.dumps({"schema": SCHEMA, "source_pdf_sha256": sha, "physical_pages": total,
                               "first": first, "last": last, "max_native_chars": max_native_chars,
                               "recognizer": recognizer.identity()}, sort_keys=True) + "\n")
        for index in range(first, last + 1):
            with _release_page_after_use(pdf.pages[index - 1]) as page:
                try:
                    text = (page.extract_text() or "").replace("\r\n", "\n").strip()
                except Exception:
                    text = ""
                try:
                    blank = not text and not any(getattr(page, "objects", {"unknown": [1]}).values())
                except Exception:
                    blank = False
                if blank or (len(text) >= max_native_chars and not text_quality(text)["needs_ocr"]):
                    skipped += 1
                    continue
                try:
                    result = recognizer.recognize(page)
                except Exception as error:
                    failed.append({"page": index, "error": str(error)[:180]})
                    continue
                if result is None:
                    continue
                execution_ms += int(result.get("execution_ms") or 0)
                sink.write(json.dumps({"page": index, "result": result}, ensure_ascii=False, sort_keys=True) + "\n")
                read.append(index)
    partial.replace(output)
    return {"schema": SCHEMA, "output": str(output), "sha256": _sha(output), "source_pdf_sha256": sha,
            "first": first, "last": last, "pages_read": len(read), "pages_skipped_native": skipped,
            "pages_failed": failed, "execution_ms": execution_ms,
            "wall_ms": round((time.monotonic() - started) * 1000)}


def load(paths, source_sha256: str, recognizer) -> tuple[dict[int, dict], dict]:
    """Readings by physical page, checked against this source and this recognizer. Raises on any mismatch."""
    files = reading_files(paths)
    identity = recognizer.identity()
    readings: dict[int, dict] = {}
    receipt = []
    for path in files:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            raise ValueError(f"OCR readings file is empty: {path.name}")
        head, body = rows[0], rows[1:]
        if head.get("schema") != SCHEMA:
            raise ValueError(f"OCR readings schema differs: {path.name}")
        if head.get("source_pdf_sha256") != source_sha256:
            raise ValueError(f"OCR readings were made from another source PDF: {path.name}")
        if head.get("recognizer") != identity:
            raise ValueError(f"OCR readings were made by another recognizer: {path.name}")
        first, last = head.get("first"), head.get("last")
        for row in body:
            page, result = row.get("page"), row.get("result")
            if type(page) is not int or not first <= page <= last:
                raise ValueError(f"OCR reading outside its declared range: {path.name}")
            if not isinstance(result, dict) or not RESULT_KEYS <= result.keys() or result["engine"] != identity["engine"]:
                raise ValueError(f"OCR reading is incomplete: {path.name} page {page}")
            if page in readings:
                raise ValueError(f"Two OCR readings for page {page}")
            readings[page] = result
        receipt.append({"file": path.name, "sha256": _sha(path), "first": first, "last": last, "pages": len(body)})
    return readings, {"files": receipt, "pages": len(readings)}
