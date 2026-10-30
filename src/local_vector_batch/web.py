"""Single-user localhost PDF review and embedding UI; no network service."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
import uuid
import zipfile

from .pdf_ingest import extract_pdf
from .pipeline import Config, run, verify
from .model import select_backend

WEB_SCRIPT = Path(__file__).with_name("web.js").read_text(encoding="utf-8")
REVIEW_PAGE = Path(__file__).with_name("review.html").read_text(encoding="utf-8")


def embedding_options(options: object) -> dict:
    """Validate web/agent controls before starting an expensive model job."""
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    result = {"overflow_policy": "split", "token_overlap": 64, "model": options.get("model", "BAAI/bge-base-en-v1.5"),
              "backend": options.get("backend", "auto"), "device": options.get("device", "auto"),
              "pooling": options.get("pooling", "cls"), "offline": options.get("offline", False)}
    if not isinstance(result["model"], str) or not result["model"].strip() or len(result["model"]) > 160:
        raise ValueError("invalid model")
    result["model"] = result["model"].strip()
    if result["backend"] not in {"auto", "mlx", "torch"} or result["device"] not in {"auto", "gpu", "mps", "cuda", "cpu"}:
        raise ValueError("invalid backend or device")
    if result["pooling"] not in {"cls", "mean"} or not isinstance(result["offline"], bool):
        raise ValueError("invalid pooling or cached-only option")
    for field, default, low, high in [("batch_size", 16, 1, 256), ("chunk_chars", 1500, 80, 12000),
                                      ("overlap", 150, 0, 11999), ("workers", 1, 1, 32),
                                      ("threads_per_worker", 1, 1, 32), ("max_tokens", 512, 16, 8192)]:
        value = options.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{field} must be an integer from {low} to {high}")
        result[field] = value
    if result["overlap"] >= result["chunk_chars"]:
        raise ValueError("overlap must be smaller than the window")
    backend = select_backend(result["backend"])
    if result["workers"] > 1 and (backend != "torch" or result["device"] != "cpu"):
        raise ValueError("parallel workers require Torch on CPU; GPU uses one batched encoder")
    if backend == "mlx" and (result["pooling"] != "cls" or result["offline"]):
        raise ValueError("MLX supports CLS pooling; choose Torch for mean pooling or cached-only mode")
    return result


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Local Vector Batch</title><style>
:root{color-scheme:light;font:15px/1.5 system-ui,-apple-system,sans-serif;background:#f7f7f4;color:#1d2625}*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:32px 20px 80px}h1{font-size:25px;letter-spacing:-.04em;margin:0}h2{font-size:16px;margin:0 0 12px}p{margin:6px 0 16px;color:#64706c}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}.mark{font-size:12px;color:#53736b;border:1px solid #d5ded8;border-radius:99px;padding:4px 9px}.grid{display:grid;grid-template-columns:minmax(290px,390px) minmax(0,1fr);gap:18px}.card{background:#fff;border:1px solid #dce1dc;border-radius:14px;padding:20px;box-shadow:0 3px 18px #11221b08}.stack{display:grid;gap:18px;align-content:start}.drop{border:1.5px dashed #a6b5ac;border-radius:11px;padding:26px;text-align:center;cursor:pointer;background:#fafcf9}.drop.drag{border-color:#295d49;background:#edf7ef}.drop strong{display:block}.drop small,.muted{color:#6a7670}.drop input{display:none}label{display:block;font-size:12px;font-weight:650;margin:14px 0 5px}input[type=text],select,input[type=number]{width:100%;height:38px;padding:8px 10px;border:1px solid #ccd5cf;border-radius:8px;background:#fff;color:#1d2625}button{border:0;border-radius:8px;background:#245d49;color:#fff;font:inherit;font-weight:600;padding:9px 13px;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}.secondary{background:#edf2ee;color:#244e3d}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row>*{flex:1}.row button{flex:0 0 auto}.check{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:400}.check input{margin:0}.status{border-left:3px solid #7b9a87;padding-left:12px;min-height:42px}.metrics{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0}.metrics b{display:block;font-size:18px}.metrics span{font-size:12px;color:#6a7670}.issue{color:#9b493a}.ok{color:#25654c}.preview{white-space:pre-wrap;max-height:360px;overflow:auto;background:#f6f8f6;padding:14px;border-radius:8px;font:12px/1.5 ui-monospace,SFMono-Regular,monospace}.table{overflow:auto;max-height:260px}.table table{border-collapse:collapse;font-size:12px}.table td{border:1px solid #dce1dc;padding:5px 7px;min-width:70px}details{margin-top:12px}summary{cursor:pointer;color:#245d49}.hidden{display:none}@media(max-width:720px){main{padding:18px 14px 48px}.grid{grid-template-columns:1fr}.card{padding:16px}}
</style></head><body><main><div class="top"><div><h1>Local Vector Batch</h1><p>Extract, review, and embed a PDF on this computer.</p></div><span class="mark">Local only</span></div><div class="grid"><div class="stack"><section class="card"><h2>1 · PDF</h2><label class="drop" id="drop"><strong>Drop a PDF here</strong><small>or click to choose a file</small><input type="file" id="file" accept="application/pdf,.pdf"></label><p id="fileName" class="muted">No file selected</p><label for="title">Document title</label><input id="title" type="text" placeholder="Use the document's real title"><label for="url">Original source URL · optional</label><input id="url" type="text" placeholder="https://authority.gov.in/document.pdf"><label class="check"><input id="tables" type="checkbox" checked> Inspect table cells</label><details><summary>Extraction options</summary><label for="ocr">Local OCR</label><select id="ocr"><option>auto</option><option>off</option><option>vision</option><option>tesseract</option><option>rapidocr</option></select><label for="language">OCR languages (eng or eng+hin)</label><input id="language" type="text" value="eng"></details><button id="extract" disabled>Extract PDF</button></section><section class="card"><h2>2 · Embeddings</h2><p>Available after every page has searchable text. Raw tables and numbers remain marked for review.</p><label for="model">Model</label><input id="model" type="text" value="BAAI/bge-base-en-v1.5"><div class="row"><div><label for="backend">Backend</label><select id="backend"><option>auto</option><option>mlx</option><option>torch</option></select></div><div><label for="device">Device</label><select id="device"><option>auto</option><option>gpu</option><option>mps</option><option>cuda</option><option>cpu</option></select></div></div><div class="row"><div><label for="batch">Batch size</label><input id="batch" type="number" min="1" max="256" value="16"></div><div><label for="window">Window characters</label><input id="window" type="number" min="80" max="12000" value="1500"></div></div><details><summary>Advanced encoding</summary><div class="row"><div><label for="pooling">Pooling</label><select id="pooling"><option>cls</option><option>mean</option></select></div><div><label for="overlap">Overlap characters</label><input id="overlap" type="number" min="0" value="150"></div></div><div class="row"><div><label for="workers">CPU workers</label><input id="workers" type="number" min="1" max="32" value="1"></div><div><label for="threads">Threads per worker</label><input id="threads" type="number" min="1" max="32" value="1"></div></div><label for="tokens">Maximum input tokens</label><input id="tokens" type="number" min="16" max="8192" value="512"><label class="check"><input id="offline" type="checkbox"> Cached model only · Torch</label><p>GPU uses one batched encoder. Parallel workers require Torch on CPU.</p></details><div class="row"><button id="embed" disabled>Generate embeddings</button><button id="download" class="secondary" disabled>Download bundle</button></div></section></div><div class="stack"><section class="card"><h2>Processing</h2><div id="status" class="status">Choose a PDF to begin.</div><div id="metrics" class="metrics"></div><div id="issues"></div></section><section class="card"><h2>Review</h2><p id="reviewHint">Page text, candidate values, and table cells appear here after extraction.</p><label for="reviewPage">Review page</label><select id="reviewPage" disabled><option>Choose a PDF first</option></select><p id="pageEvidence" class="muted"></p><details id="pageDetails" class="hidden" open><summary>Extracted page text</summary><div class="preview" id="pagePreview"></div></details><details id="factsDetails" class="hidden"><summary>Candidate values · unverified</summary><div class="preview" id="factPreview"></div></details><details id="structureDetails" class="hidden"><summary>Structure cues · review required</summary><div class="preview" id="structurePreview"></div></details><details id="tableDetails" class="hidden"><summary>Table cells · review row spans</summary><div class="table" id="tablePreview"></div></details></section></div></div></main><script>
__SCRIPT__
</script></body></html>"""


def serve(port: int = 8765, max_upload_mb: int = 150, *, review_manifest: Path | None = None,
          review_decisions: Path | None = None) -> None:
    if not 1 <= port <= 65535 or not 1 <= max_upload_mb <= 1000:
        raise ValueError("invalid port or upload limit")
    token = secrets.token_urlsafe(24)
    if review_manifest is not None:
        review_manifest = review_manifest.resolve(strict=True)
        review_decisions = (review_decisions or review_manifest.with_name('ocr-review-feedback.jsonl')).resolve()
        if review_decisions == review_manifest:
            raise ValueError('review feedback must not overwrite the queue')
    root = tempfile.TemporaryDirectory(prefix="local-vector-batch-")
    lock = threading.Lock()
    # PDFium does not support simultaneous rendering in separate threads.
    render_lock = threading.Lock()
    state: dict = {"stage": "idle", "progress": 0, "total": 0, "report": None,
                   "error": None, "job_dir": None, "vector_dir": None, "vectors": 0}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, data: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A reviewer may navigate away while an image is rendering.

        def _json(self, status: int, value: object) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode())

        def _download(self, file: Path) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("Content-Disposition", 'attachment; filename="local-vector-batch-export.zip"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with file.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        self.wfile.write(block)
            finally:
                file.unlink(missing_ok=True)

        def _authorized(self) -> bool:
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host == f"127.0.0.1:{port}" and (origin is None or origin == f"http://127.0.0.1:{port}")

        def _token(self, query: dict | None = None) -> bool:
            return self.headers.get("X-Local-Token") == token or (query or {}).get("token", [None])[0] == token

        def do_GET(self) -> None:
            if not self._authorized():
                return self._json(403, {"error": "localhost only"})
            path = urlsplit(self.path)
            if path.path == "/":
                page = PAGE.replace("__SCRIPT__", WEB_SCRIPT).replace("__TOKEN__", token)
                if review_manifest:
                    page = page.replace('<span class="mark">Local only</span>', '<a class="mark" href="/review">Review OCR differences</a>')
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if path.path == "/review" and review_manifest:
                return self._send(200, REVIEW_PAGE.replace('__TOKEN__', token).encode(), 'text/html; charset=utf-8')
            if path.path == "/api/status":
                with lock:
                    snapshot = {key: value for key, value in state.items() if key not in {"job_dir", "vector_dir"}}
                    directory = state["vector_dir"]
                if snapshot["stage"] == "embedding" and directory:
                    manifest = Path(directory) / "manifest.json"
                    if manifest.is_file():
                        try:
                            data = json.loads(manifest.read_text())
                            snapshot["vectors"] = sum(item["count"] for item in data["batches"])
                        except (OSError, ValueError, KeyError):
                            pass
                return self._json(200, snapshot)
            if not self._token(parse_qs(path.query)):
                return self._json(403, {"error": "missing local token"})
            if path.path == '/api/review-summary' and review_manifest and review_decisions:
                from .review_disagreements import feedback_summary
                with lock:
                    summary = feedback_summary([review_decisions]) if review_decisions.is_file() else {
                        'feedback_entries': 0, 'unique_pages': 0, 'decisions': {}, 'disagreement_types': {}}
                return self._json(200, summary)
            if path.path in {"/api/review-item", "/api/review-image", "/api/review-focus", "/api/review-group"} and review_manifest:
                try:
                    from .review_queue import load_item, render_page, latest_feedback, review_group, verify_proposal_source
                    index = int(parse_qs(path.query).get('index', ['0'])[0])
                    if path.path == '/api/review-group':
                        with lock:
                            return self._json(200, {'differences': review_group(review_manifest, index, review_decisions)})
                    item = load_item(review_manifest, index)
                    if path.path in ('/api/review-image', '/api/review-focus'):
                        try:
                            with render_lock:
                                image = render_page(item, focus=path.path == '/api/review-focus')
                        except Exception:
                            return self._json(500, {'error': 'Could not render the original PDF page'})
                        return self._send(200, image, 'image/png')
                    try:
                        with render_lock:
                            verify_proposal_source(item)
                    except Exception:
                        return self._json(400, {'error': 'Suggestion source crop could not be verified'})
                    if review_decisions:
                        with lock:
                            item['saved_feedback'] = latest_feedback(review_decisions, item)
                    item.pop('source_pdf')
                    return self._json(200, item)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    return self._json(400, {'error': str(error)[:180]})
                except Exception:
                    return self._json(500, {'error': 'Review could not be verified or saved'})
            with lock:
                directory = state["job_dir"]
                active = state["stage"] in {"extracting", "embedding"}
            if not directory:
                return self._json(404, {"error": "no PDF uploaded"})
            folder = Path(directory)
            if path.path == "/api/preview":
                def records(name: str):
                    with (folder / name).open(encoding="utf-8") as stream:
                        for line in stream:
                            yield json.loads(line)
                if not (folder / "report.json").is_file():
                    return self._json(409, {"error": "extraction still running"})
                report = json.loads((folder / "report.json").read_text())
                try:
                    number = int(parse_qs(path.query).get("page", ["1"])[0])
                    if not 1 <= number <= report["pages"]:
                        raise ValueError("page outside PDF")
                except ValueError as error:
                    return self._json(400, {"error": str(error)})
                selected = next(row for row in records("pages.jsonl") if row["page"] == number)
                return self._json(200, {"page": selected,
                                        "facts": [row for row in records("fact-candidates.jsonl") if row["page"] == number],
                                        "structure": [row for row in records("structure-candidates.jsonl")
                                                      if row["evidence"] and row["evidence"][0]["page"] == number]
                                                     if (folder / "structure-candidates.jsonl").is_file() else [],
                                        "tables": [row for row in json.loads((folder / "tables.json").read_text()) if row["page"] == number]})
            if path.path == "/api/download":
                if active:
                    return self._json(409, {"error": "wait for the current processing step before exporting"})
                if not (folder / "report.json").is_file():
                    return self._json(409, {"error": "nothing to export yet"})
                export = Path(root.name) / f"{folder.name}-{uuid.uuid4().hex}.zip"
                with zipfile.ZipFile(export, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in folder.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(folder))
                return self._download(export)
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized() or not self._token():
                return self._json(403, {"error": "localhost token required"})
            path = urlsplit(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 1 or length > max_upload_mb * 1024 * 1024:
                return self._json(413, {"error": f"request must be under {max_upload_mb} MB"})
            if path.path == '/api/review-decision' and review_manifest and review_decisions:
                if length > 16 * 1024:
                    return self._json(413, {'error': 'review feedback is too large'})
                try:
                    from .review_queue import record_decision
                    request = json.loads(self.rfile.read(length))
                    with render_lock:
                        with lock:
                            receipt = record_decision(review_manifest, review_decisions, request)
                    native_rows = [json.loads(line) for line in review_manifest.read_text(encoding='utf-8').splitlines() if line.strip()]
                    repair_jobs = None
                    if any(row.get('l1_package') for row in native_rows):
                        from .l1_review import sync_feedback_jobs
                        repair_jobs = sync_feedback_jobs(review_manifest, review_decisions,
                            review_decisions.with_name('repair-jobs.jsonl'))
                    return self._json(200, {'saved': True, 'promotes_page': receipt['promotes_page'],
                                           'repair_jobs': repair_jobs})
                except (OSError, ValueError, KeyError, TypeError) as error:
                    return self._json(400, {'error': str(error)[:180]})
                except Exception:
                    return self._json(500, {'error': 'Review could not be verified or saved'})
            if path.path == "/api/upload":
                if self.headers.get("Content-Type", "").split(";")[0] not in {"application/pdf", "application/octet-stream"}:
                    return self._json(415, {"error": "upload a PDF"})
                with lock:
                    if state["stage"] in {"extracting", "embedding"}:
                        return self._json(409, {"error": "a job is already running"})
                    folder = Path(root.name) / uuid.uuid4().hex
                    folder.mkdir()
                    state.update(stage="extracting", progress=0, total=0, report=None,
                                 error=None, job_dir=str(folder), vector_dir=None, vectors=0)
                source = folder / "source.pdf"
                try:
                    self.connection.settimeout(60)
                    with source.open("wb") as stream:
                        remaining = length
                        while remaining:
                            block = self.rfile.read(min(1024 * 1024, remaining))
                            if not block:
                                raise ConnectionError("incomplete upload")
                            stream.write(block)
                            remaining -= len(block)
                except (OSError, ConnectionError):
                    with lock:
                        state.update(stage="error", error="PDF upload did not finish; choose the file and retry")
                    try:
                        return self._json(400, {"error": state["error"]})
                    except OSError:
                        return
                query = parse_qs(path.query, keep_blank_values=True)
                title = query.get("title", [""])[0]
                source_url = query.get("url", [""])[0]
                inspect_tables = query.get("tables", ["1"])[0] == "1"
                ocr_engine = query.get("ocr", ["auto"])[0]
                ocr_language = query.get("language", ["eng"])[0]

                def extract_job() -> None:
                    try:
                        def progress(done: int, total: int) -> None:
                            with lock:
                                state.update(progress=done, total=total)
                        report = extract_pdf(source, folder, title=title or None,
                                             source_url=source_url or None, tables=inspect_tables,
                                             ocr=ocr_engine, ocr_language=ocr_language, progress=progress)
                        with lock:
                            state.update(report=report, stage="ready" if report["complete_for_embedding"] else "review_required")
                    except Exception as error:
                        with lock:
                            state.update(stage="error", error=str(error)[:240])
                threading.Thread(target=extract_job, daemon=True).start()
                return self._json(202, {"stage": "extracting"})
            if path.path == "/api/embed":
                try:
                    options = embedding_options(json.loads(self.rfile.read(length)))
                    with lock:
                        if state["stage"] not in {"ready", "complete"}:
                            return self._json(409, {"error": "review or OCR the PDF before embedding"})
                        folder = Path(state["job_dir"])
                        output = folder / "vectors" / f"run-{uuid.uuid4().hex[:8]}"
                        state.update(stage="embedding", error=None, vector_dir=str(output), vectors=0)
                except (ValueError, TypeError) as error:
                    return self._json(400, {"error": str(error)[:180]})

                def embed_job() -> None:
                    try:
                        config = Config(folder / "embedding-input.jsonl", output,
                                        min_chars=1, **options)
                        run(config)
                        result = verify(output)
                        if not result["input_coverage_complete"]:
                            raise RuntimeError("Some PDF page inputs produced no passages; inspect the input audit before using these vectors")
                        if not result["token_budget_validated"]:
                            raise RuntimeError("Tokenizer coverage was not validated; do not use these vectors as a complete PDF index")
                        with lock:
                            state.update(stage="complete", vectors=result["vectors"])
                    except Exception as error:
                        with lock:
                            state.update(stage="ready", error=str(error)[:240])
                threading.Thread(target=embed_job, daemon=True).start()
                return self._json(202, {"stage": "embedding"})
            return self._json(404, {"error": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Local Vector Batch: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        root.cleanup()
