"""Small, scriptable CLI; stdout is JSON with --json."""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

from .pipeline import Config, run, verify


def doctor() -> dict:
    result = {"platform": platform.platform(), "machine": platform.machine(),
              "mlx_installed": importlib.util.find_spec("mlx_embedding_models") is not None,
              "torch_installed": importlib.util.find_spec("torch") is not None,
              "pdf_installed": importlib.util.find_spec("pdfplumber") is not None,
              "rapidocr_installed": importlib.util.find_spec("rapidocr") is not None,
              "docling_installed": importlib.util.find_spec("docling") is not None,
              "vision_ocr_available": platform.system() == "Darwin" and shutil.which("swiftc") is not None,
              "tesseract_available": shutil.which("tesseract") is not None}
    if result["torch_installed"]:
        import torch
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["mps_available"] = bool(torch.backends.mps.is_available())
    return result


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="local-vector-batch",
                                   description="Generate verifiable local embeddings from JSONL")
    sub = main.add_subparsers(dest="command", required=True)
    health = sub.add_parser("doctor", help="show installed backends and accelerator availability")
    health.add_argument("--json", action="store_true")
    embed = sub.add_parser("embed", help="embed documents from JSONL")
    embed.add_argument("--input", required=True, type=Path)
    embed.add_argument("--output", required=True, type=Path)
    embed.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    embed.add_argument("--backend", choices=["auto", "mlx", "torch"], default="auto")
    embed.add_argument("--device", choices=["auto", "gpu", "mps", "cuda", "cpu"], default="auto")
    embed.add_argument("--pooling", choices=["cls", "mean"], default="cls")
    embed.add_argument("--format", choices=["vectorize", "generic"], default="vectorize")
    embed.add_argument("--input-mode", choices=["documents", "chunks", "passages"], default="documents",
                       help="chunks requires chunk_index; passages also requires absolute char_start and preserves each exact slice")
    embed.add_argument("--batch-size", type=int, default=32)
    embed.add_argument("--chunk-chars", type=int, default=1500)
    embed.add_argument("--overlap", type=int, default=150)
    embed.add_argument("--min-chars", type=int, default=80)
    embed.add_argument("--workers", type=int, default=1)
    embed.add_argument("--max-tokens", type=int, default=512)
    embed.add_argument("--overflow-policy", choices=["error", "split"], default="error")
    embed.add_argument("--token-overlap", type=int, default=64)
    embed.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True)
    embed.add_argument("--revision", help="Hugging Face revision, Torch backend only")
    embed.add_argument("--threads-per-worker", type=int, default=1)
    embed.add_argument("--offline", action="store_true", help="use cached model files only")
    embed.add_argument("--resume", action="store_true")
    embed.add_argument("--json", action="store_true")
    check = sub.add_parser("verify", help="check hashes, dimensions, and unique vector IDs")
    check.add_argument("--output", required=True, type=Path)
    check.add_argument("--json", action="store_true")
    pdf = sub.add_parser("pdf-extract", help="extract and review PDF pages, tables, and candidate values locally")
    pdf.add_argument("--input", required=True, type=Path)
    pdf.add_argument("--output", required=True, type=Path)
    pdf.add_argument("--title")
    pdf.add_argument("--source-url")
    pdf.add_argument("--tables", action=argparse.BooleanOptionalAction, default=True)
    pdf.add_argument("--ocr", choices=["auto", "off", "vision", "tesseract", "rapidocr"], default="auto")
    pdf.add_argument("--ocr-device", choices=["auto", "cpu", "gpu"], default="auto",
                     help="Vision auto lets macOS schedule hardware; forced GPU is unsupported")
    pdf.add_argument("--ocr-language", default="eng", help="OCR language hint, such as eng, hin or eng+hin")
    pdf.add_argument("--json", action="store_true")
    repair = sub.add_parser("pdf-repair", help="repair selected original physical pages without reprocessing the PDF")
    repair.add_argument("--input", required=True, type=Path, help="original source PDF")
    repair.add_argument("--package", required=True, type=Path, help="existing verified extraction package")
    repair.add_argument("--pages", required=True, type=lambda value: [int(p) for p in value.split(",")])
    repair.add_argument("--ocr", choices=["auto", "vision", "tesseract", "rapidocr"], default="auto")
    repair.add_argument("--ocr-language", default="eng")
    repair.add_argument("--tables", action=argparse.BooleanOptionalAction, default=True)
    repair.add_argument("--json", action="store_true")
    native_repair = sub.add_parser("pdf-native-repair", help="atomically replace one page from a source-bound PDFium/pypdf/raster receipt")
    native_repair.add_argument("--package", required=True, type=Path)
    native_repair.add_argument("--receipt", required=True, type=Path, help="one JSON source-bound machine receipt")
    native_repair.add_argument("--json", action="store_true")
    batch = sub.add_parser("pdf-batch", help="extract a bounded manifest of local PDFs with resumable page receipts")
    batch.add_argument("--input", required=True, type=Path, help="JSONL rows with document_id, path, url and optional sha256")
    batch.add_argument("--output", required=True, type=Path)
    batch.add_argument("--workers", type=int, default=4, help="parallel PDF processes, 1–16")
    batch.add_argument("--limit", type=int, default=500, help="maximum manifest rows in this run")
    batch.add_argument("--document-timeout", type=float, default=0,
                       help="per-PDF extraction seconds; 0 disables, macOS/Linux only")
    batch.add_argument("--resume", action="store_true", help="verify and reuse existing complete outputs")
    batch.add_argument("--repair-ocr", action="store_true",
                       help="with --resume and OCR active, retry unreadable pages and replace only improved packages")
    batch.add_argument("--refresh", action="store_true",
                       help="with --resume, rebuild existing packages after a parser change using a verified atomic swap")
    batch.add_argument("--ocr", choices=["auto", "off", "vision", "tesseract", "rapidocr"], default="auto")
    batch.add_argument("--ocr-language", default="eng", help="OCR language hint, such as eng, hin or eng+hin")
    batch.add_argument("--ocr-device", choices=["auto", "cpu", "gpu"], default="auto")
    batch.add_argument("--tables", action=argparse.BooleanOptionalAction, default=True)
    batch.add_argument("--json", action="store_true")
    evidence = sub.add_parser("validate-evidence", help="check structured candidate quotes and typed fields without verification")
    evidence.add_argument("--pages", required=True, type=Path, help="extracted pages.jsonl")
    evidence.add_argument("--candidates", required=True, type=Path, help="typed candidates JSONL with evidence quotes")
    evidence.add_argument("--json", action="store_true")
    package = sub.add_parser("verify-package", help="verify a PDF evidence package and optional vector anchors")
    package.add_argument("--output", required=True, type=Path, help="pdf-extract output directory")
    package.add_argument("--vectors", type=Path, help="optional completed embed output directory")
    package.add_argument("--source-pdf", type=Path, help="optional original PDF for byte hash verification")
    package.add_argument("--require-ready", action="store_true", help="fail unless the original PDF is checked and every page text is ready or blank")
    package.add_argument("--json", action="store_true")
    present = sub.add_parser("render-package", help="add linked Markdown/HTML and layout JSON to an existing verified PDF package without re-extracting")
    present.add_argument("--output", required=True, type=Path, help="pdf-extract output directory")
    present.add_argument("--json", action="store_true")
    paddle = sub.add_parser("import-paddle-layout", help="attach PP-StructureV2/V3 page blocks as review-only candidates")
    paddle.add_argument("--package", required=True, type=Path)
    paddle.add_argument("--source-pdf", required=True, type=Path)
    paddle.add_argument("--manifest", required=True, type=Path)
    paddle.add_argument("--json", action="store_true")
    reviewed = sub.add_parser("review-package", help="export only explicitly reviewed PDF evidence units")
    reviewed.add_argument("--output", required=True, type=Path, help="pdf-extract output directory")
    reviewed.add_argument("--decisions", required=True, type=Path, help="review decisions JSON")
    reviewed.add_argument("--source-pdf", required=True, type=Path, help="original PDF for byte hash verification")
    reviewed.add_argument("--json", action="store_true")
    consensus = sub.add_parser("ocr-consensus", help="dry-run independent RapidOCR audit of existing Vision pages")
    consensus.add_argument("--input", required=True, type=Path, help="JSONL rows: package and source_pdf paths")
    consensus.add_argument("--output", required=True, type=Path, help="page-level audit JSONL")
    consensus.add_argument("--limit", type=int, default=50)
    consensus.add_argument("--dpi", type=int, default=180)
    consensus.add_argument("--workers", type=int, default=1, help="parallel OCR processes, 1–16")
    consensus.add_argument("--page-batch-size", type=int, default=8, help="pages per queued job, 1–64; models stay loaded per process")
    consensus.add_argument("--resume", action="store_true", help="reuse exact source/page/DPI/engine-matched comparison receipts")
    consensus.add_argument("--json", action="store_true")
    audit = sub.add_parser("audit-review", help="inventory review-required PDF pages by reason and source")
    audit.add_argument("--input", required=True, type=Path, help="pdf-batch extraction directory")
    audit.add_argument("--details", type=Path, help="optional per-page JSONL work queue")
    audit.add_argument("--json", action="store_true")
    render_audit = sub.add_parser("audit-rendered-review", help="inspect rendered originals for short or missing PDF pages")
    render_audit.add_argument("--input", required=True, type=Path, help="JSONL page rows with source PDF paths")
    render_audit.add_argument("--output", required=True, type=Path, help="render evidence JSONL")
    render_audit.add_argument("--workers", type=int, default=4)
    render_audit.add_argument("--json", action="store_true")
    compare = sub.add_parser("pdf-compare", help="compare review pages with optional local Docling OCR/layout")
    compare.add_argument("--package", required=True, type=Path)
    compare.add_argument("--source-pdf", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument("--pages", type=lambda value: [int(p) for p in value.split(",")])
    compare.add_argument("--limit", type=int, default=25)
    compare.add_argument("--language", default="iso:en", help="BCP-47 language, e.g. iso:en or iso:hi")
    compare.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    compare.add_argument("--json", action="store_true")
    quality = sub.add_parser("pdf-quality", help="score source-bound PDF extraction; auto-accept safe pages and list repair reasons")
    quality.add_argument("--package", required=True, type=Path)
    quality.add_argument("--source-pdf", required=True, type=Path)
    quality.add_argument("--output", required=True, type=Path, help="machine quality receipt outside the extraction package")
    quality.add_argument("--pages", type=lambda value: [int(p) for p in value.split(",")])
    quality.add_argument("--independent", type=Path, help="existing source-bound ocr-consensus JSONL; does not run OCR again")
    quality.add_argument("--policy", type=Path, help="override the packaged quality policy")
    quality.add_argument("--references", type=Path, help="source-reviewed calibration/holdout reference JSONL")
    quality.add_argument("--source-raster", action=argparse.BooleanOptionalAction, default=True)
    quality.add_argument("--require-accepted", action="store_true", help="exit 3 after writing the report if any assessed page needs repair or a source is blocked")
    quality.add_argument("--json", action="store_true")
    quality_batch = sub.add_parser("pdf-quality-batch", help="parallel quality gates over cached PDF extraction, without new OCR")
    quality_batch.add_argument("--input", required=True, type=Path, help="JSONL package/source_pdf/pages manifest")
    quality_batch.add_argument("--output", required=True, type=Path)
    quality_batch.add_argument("--independent", type=Path, help="source-bound cached ocr-consensus JSONL")
    quality_batch.add_argument("--workers", type=int, default=6)
    quality_batch.add_argument("--policy", type=Path)
    quality_batch.add_argument("--json", action="store_true")
    repair_queue = sub.add_parser("pdf-repair-queue", help="resume source-bound staged page repairs without publishing unverified candidates")
    repair_queue.add_argument("--input", required=True, type=Path, help="JSONL package/source_pdf/quality_report/pages manifest")
    repair_queue.add_argument("--output", required=True, type=Path, help="isolated candidates and durable repair ledger")
    repair_queue.add_argument("--workers", type=int, default=4)
    repair_queue.add_argument("--limit", type=int, default=100)
    repair_queue.add_argument("--config", type=Path, help="JSON repair configuration; changes invalidate cached attempts")
    repair_queue.add_argument("--json", action="store_true")
    auto_proof = sub.add_parser("pdf-auto-proof", help="resume source-bound footnote proof and guarded local commits")
    auto_proof.add_argument("--input", required=True, type=Path)
    auto_proof.add_argument("--independent", required=True, type=Path)
    auto_proof.add_argument("--output", required=True, type=Path)
    auto_proof.add_argument("--limit", type=int, default=10)
    auto_proof.add_argument("--workers", type=int, default=1)
    auto_proof.add_argument("--retry-unresolved", action="store_true", help="retry prior held jobs; committed and stale jobs remain excluded")
    auto_proof.add_argument("--json", action="store_true")
    repair_status = sub.add_parser("pdf-repair-status", help="read repair queue status without starting work")
    repair_status.add_argument("--output", required=True, type=Path)
    repair_status.add_argument("--json", action="store_true")
    footnote_proof = sub.add_parser("pdf-footnote-proof", help="source-bound crop proof for linked printed footnote markers")
    footnote_proof.add_argument("--package", required=True, type=Path)
    footnote_proof.add_argument("--source-pdf", required=True, type=Path)
    footnote_proof.add_argument("--independent", required=True, type=Path, help="source-bound independent OCR JSONL")
    footnote_proof.add_argument("--page", required=True, type=int)
    footnote_proof.add_argument("--output", required=True, type=Path)
    footnote_proof.add_argument("--max-pairs", type=int, default=4)
    footnote_proof.add_argument("--json", action="store_true")
    vision_merge = sub.add_parser("pdf-vision-merge", help="opt-in local vision suggestions for source-bound OCR lines")
    vision_merge.add_argument("--package", required=True, type=Path)
    vision_merge.add_argument("--source-pdf", required=True, type=Path)
    vision_merge.add_argument("--independent", required=True, type=Path, help="source-bound independent OCR JSONL")
    vision_merge.add_argument("--page", required=True, type=int)
    vision_merge.add_argument("--line", action="append", required=True, type=int, help="selected OCR line index; repeat at most eight times")
    vision_merge.add_argument("--output", required=True, type=Path)
    vision_merge.add_argument("--json", action="store_true")
    vision_batch = sub.add_parser("pdf-vision-merge-batch", help="resumable source-bound local OCR merge proposals for held pages")
    vision_batch.add_argument("--manifest", required=True, type=Path)
    vision_batch.add_argument("--independent", required=True, type=Path)
    vision_batch.add_argument("--base", required=True, type=Path, help="base directory for manifest-relative package paths")
    vision_batch.add_argument("--output", required=True, type=Path)
    vision_batch.add_argument("--limit", type=int, default=20)
    vision_batch.add_argument("--lines-per-page", type=int, default=4)
    vision_batch.add_argument("--json", action="store_true")
    blank_commit = sub.add_parser("pdf-blank-commit", help="CAS-mark exactly white source PDF pages blank from 180-DPI receipts")
    blank_commit.add_argument("--manifest", required=True, type=Path)
    blank_commit.add_argument("--json", action="store_true")
    footnote_repair = sub.add_parser("pdf-footnote-repair", help="reprove linked markers and stage only a fully gated page")
    footnote_repair.add_argument("--package", required=True, type=Path, help="disposable staging package")
    footnote_repair.add_argument("--receipt", action="append", required=True, type=Path)
    footnote_repair.add_argument("--output", required=True, type=Path, help="source crop and quality proof directory")
    footnote_repair.add_argument("--docling-output", type=Path,
                                 help="source-bound one-page Docling table comparison, when a table is present")
    footnote_repair.add_argument("--json", action="store_true")
    commit = sub.add_parser("pdf-repair-commit", help="atomically merge freshly verified staged page repairs; index publication is separate")
    commit.add_argument("--package", required=True, type=Path)
    commit.add_argument("--source-pdf", required=True, type=Path)
    commit.add_argument("--results", required=True, type=Path, help="repair queue result JSON")
    commit.add_argument("--independent", required=True, type=Path)
    commit.add_argument("--quality-output", required=True, type=Path)
    commit.add_argument("--review-sidecar", type=Path)
    commit.add_argument("--json", action="store_true")
    review_queue = sub.add_parser("pdf-review-queue", help="prepare a source-bound OCR disagreement review queue for held pages")
    review_queue.add_argument("--input", required=True, type=Path, help="held PDF manifest with package, source_pdf, pages and optional quality_report")
    review_queue.add_argument("--independent", required=True, type=Path, help="source-bound OCR consensus JSONL")
    review_queue.add_argument("--output", required=True, type=Path, help="new local queue directory")
    review_queue.add_argument("--limit", type=int, default=100)
    review_queue.add_argument("--reviewed", action="append", type=Path, default=[], help="feedback JSONL to skip already reviewed exact differences; repeatable")
    review_queue.add_argument("--all-differences", action="store_true", help="include text and line-wrap differences, grouped per page")
    review_queue.add_argument("--per-page", type=int, default=64, help="maximum differences per page when using --all-differences")
    review_queue.add_argument("--json", action="store_true")
    feedback = sub.add_parser("pdf-review-feedback", help="summarize saved OCR disagreement review labels")
    feedback.add_argument("--input", required=True, action="append", type=Path, help="feedback JSONL; repeatable")
    feedback.add_argument("--json", action="store_true")
    route = sub.add_parser("pdf-review-route", help="revalidate focused OCR feedback and queue source crop proof work")
    route.add_argument("--manifest", required=True, type=Path)
    route.add_argument("--feedback", required=True, type=Path)
    route.add_argument("--packages", required=True, type=Path)
    route.add_argument("--output", required=True, type=Path)
    route.add_argument("--json", action="store_true")
    proposals = sub.add_parser("pdf-review-proposals", help="attach source-crop observations as unverified review suggestions")
    proposals.add_argument("--input", required=True, type=Path, help="review manifest JSONL")
    proposals.add_argument("--observations", required=True, type=Path, help="source-bound visual observations JSONL")
    proposals.add_argument("--output", required=True, type=Path, help="new proposal manifest JSONL")
    proposals.add_argument("--json", action="store_true")
    review_filter = sub.add_parser("pdf-review-filter", help="skip already reviewed exact source and OCR differences")
    review_filter.add_argument("--input", required=True, type=Path)
    review_filter.add_argument("--reviewed", required=True, action="append", type=Path, help="saved review feedback JSONL; repeatable")
    review_filter.add_argument("--output", required=True, type=Path, help="new filtered review manifest")
    review_filter.add_argument("--json", action="store_true")
    web = sub.add_parser("serve", help="open the local PDF review and embedding webpage")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--max-upload-mb", type=int, default=150)
    web.add_argument("--review-manifest", type=Path, help="optional trusted local OCR review queue JSONL")
    web.add_argument("--review-decisions", type=Path, help="local feedback JSONL (defaults beside the manifest)")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "verify":
            result = verify(args.output)
        elif args.command == "pdf-extract":
            from .pdf_ingest import extract_pdf
            result = extract_pdf(args.input, args.output, title=args.title,
                                 source_url=args.source_url, tables=args.tables,
                                 ocr=args.ocr, ocr_language=args.ocr_language,
                                 ocr_device=args.ocr_device)
        elif args.command == "pdf-batch":
            from .pdf_batch import extract_batch
            batch_report = extract_batch(args.input, args.output, workers=args.workers,
                                   limit=args.limit, resume=args.resume, ocr=args.ocr,
                                   ocr_device=args.ocr_device,
                                   tables=args.tables, repair_ocr=args.repair_ocr,
                                   refresh=args.refresh, ocr_language=args.ocr_language,
                                   document_timeout=args.document_timeout)
            result = {key: batch_report[key] for key in ("requested", "workers", "counts", "pages_extracted",
                      "pages_ready", "pages_blank", "pages_requiring_review",
                      "documents_requiring_source_review", "elapsed_seconds")}
            result["report"] = batch_report["report_path"]
        elif args.command == "pdf-repair":
            from .pdf_repair import repair_pdf_pages
            result = repair_pdf_pages(args.input, args.package, set(args.pages), ocr=args.ocr,
                                      ocr_language=args.ocr_language, tables=args.tables)
        elif args.command == "pdf-native-repair":
            from .native_repair import repair_native_page
            result = repair_native_page(args.package, json.loads(args.receipt.read_text(encoding="utf-8")))
        elif args.command == "validate-evidence":
            from .evidence import validate_candidates
            def records(path):
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError("evidence inputs must contain JSON objects")
                return rows
            result = validate_candidates(records(args.candidates), records(args.pages))
        elif args.command == "verify-package":
            from .evidence_package import verify_package
            result = verify_package(args.output, args.vectors, args.source_pdf)
            if args.require_ready and (not result["original_pdf_checked"] or result["page_text_readiness"] != "complete"):
                raise ValueError("page text is not ready for retrieval: check original PDF and resolve review_required_pages")
        elif args.command == "render-package":
            from .evidence_package import create_package, verify_package
            verify_package(args.output)
            package = create_package(args.output)
            result = {"document_id": package["document"]["id"], **package["presentation"]}
        elif args.command == "import-paddle-layout":
            from .paddle_layout_import import import_paddle_layout
            result = import_paddle_layout(args.package, args.source_pdf, args.manifest)
        elif args.command == "review-package":
            from .reviewed_evidence import review_package
            result = review_package(args.output, args.decisions, args.source_pdf)
        elif args.command == "ocr-consensus":
            from .ocr_consensus import audit_manifest
            result = audit_manifest(args.input, args.output, limit=args.limit, dpi=args.dpi, workers=args.workers,
                                    resume=args.resume, page_batch_size=args.page_batch_size)
        elif args.command == "pdf-compare":
            from .pdf_compare import compare_pdf
            result = compare_pdf(args.package, args.source_pdf, args.output, pages=args.pages,
                                 limit=args.limit, language=args.language, device=args.device)
        elif args.command == "pdf-quality-batch":
            from .quality_batch import assess_batch
            result = assess_batch(args.input, args.output, independent=args.independent,
                                  workers=args.workers, policy=args.policy)
        elif args.command == "pdf-repair-queue":
            from .repair_pipeline import run_repairs
            config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
            if not isinstance(config, dict):
                raise ValueError("repair config must be a JSON object")
            result = run_repairs(args.input, args.output, workers=args.workers, limit=args.limit, config=config)
        elif args.command == "pdf-auto-proof":
            from .auto_proof_queue import run_auto_proof
            result = run_auto_proof(args.input, args.independent, args.output, limit=args.limit, workers=args.workers,
                                    retry_unresolved=args.retry_unresolved)
        elif args.command == "pdf-repair-status":
            from .repair_pipeline import repair_status
            result = repair_status(args.output)
        elif args.command == "pdf-footnote-proof":
            from .region_ocr import candidate_footnote_pairs, propose_footnote_marker_link
            from .evidence_package import verify_package
            verify_package(args.package, source_pdf=args.source_pdf)
            report = json.loads((args.package / "report.json").read_text())
            page = next((row for row in (json.loads(line) for line in
                (args.package / "pages.jsonl").read_text().splitlines()) if row["page"] == args.page), None)
            if page is None or not 1 <= args.max_pairs <= 16:
                raise ValueError("valid physical page and max-pairs 1..16 required")
            independent = next((row for row in (json.loads(line) for line in args.independent.read_text().splitlines())
                if row.get("source_pdf_sha256") == report["source_pdf_sha256"] and
                row.get("page") == args.page and
                row.get("selected_page_text_sha256") == page["text_sha256"]), None)
            if independent is None:
                raise ValueError("source-bound independent OCR receipt missing")
            from .repair_pipeline import _bound_independent_text
            independent_text = _bound_independent_text(independent, args.source_pdf,
                source_sha=report["source_pdf_sha256"], page=args.page,
                original_text_sha=page["text_sha256"],
                candidate_engine=page.get("ocr", {}).get("engine", page["method"]))
            pairs = candidate_footnote_pairs(page, independent_text)[:args.max_pairs]
            result = {"page": args.page, "candidates": len(pairs), "results": [
                propose_footnote_marker_link(args.package, args.source_pdf, args.page,
                    independent, args.output, body, note)
                for body, note in pairs]}
        elif args.command == "pdf-footnote-repair":
            from .footnote_repair import apply_footnote_marker_repair
            result = apply_footnote_marker_repair(args.package,
                [json.loads(path.read_text()) for path in args.receipt], args.output,
                docling_output=args.docling_output)
        elif args.command == "pdf-vision-merge":
            from .local_vision_merge import propose_local_vision_merge
            from .evidence_package import verify_package
            verify_package(args.package, source_pdf=args.source_pdf)
            report = json.loads((args.package / "report.json").read_text())
            page = next((row for row in (json.loads(line) for line in
                (args.package / "pages.jsonl").read_text().splitlines()) if row["page"] == args.page), None)
            if page is None:
                raise ValueError("source page missing")
            independent = next((row for row in (json.loads(line) for line in args.independent.read_text().splitlines())
                if row.get("source_pdf_sha256") == report["source_pdf_sha256"] and
                row.get("page") == args.page and
                row.get("selected_page_text_sha256") == page["text_sha256"]), None)
            if independent is None:
                raise ValueError("source-bound independent OCR receipt missing")
            result = propose_local_vision_merge(args.package, args.source_pdf,
                independent, args.page, args.line, args.output)
        elif args.command == "pdf-vision-merge-batch":
            from .local_vision_merge import run_local_vision_batch
            result = run_local_vision_batch(args.manifest, args.independent,
                args.output, base=args.base, limit=args.limit,
                lines_per_page=args.lines_per_page)
        elif args.command == "pdf-blank-commit":
            from .blank_page_repair import commit_exact_white_manifest
            commits = commit_exact_white_manifest(args.manifest)
            result = {"packages_committed": len(commits),
                      "pages_blank": sum(len(row["pages_blank"]) for row in commits),
                      "commits": commits}
        elif args.command == "pdf-review-queue":
            from .review_queue import generate_review_queue
            result = generate_review_queue(args.input, args.independent, args.output, limit=args.limit,
                                           reviewed=args.reviewed, all_differences=args.all_differences,
                                           per_page=args.per_page)
        elif args.command == "pdf-review-feedback":
            from .review_disagreements import feedback_summary
            result = feedback_summary(args.input)
        elif args.command == "pdf-review-route":
            from .review_repair_route import route_feedback
            result = route_feedback(args.manifest, args.feedback, args.packages, args.output)
        elif args.command == "pdf-review-proposals":
            from .review_queue import attach_observations
            result = attach_observations(args.input, args.observations, args.output)
        elif args.command == "pdf-review-filter":
            from .review_queue import filter_reviewed_focus
            result = filter_reviewed_focus(args.input, args.reviewed, args.output)
        elif args.command == "pdf-repair-commit":
            from .repair_commit import commit_page_repairs
            report = json.loads((args.package / "report.json").read_text())
            jobs = json.loads(args.results.read_text())["results"]
            jobs = [job for job in jobs if job.get("status") == "ready_for_publication" and
                    job.get("source_pdf_sha256") == report["source_pdf_sha256"]]
            result = commit_page_repairs(args.package, args.source_pdf, jobs,
                quality_output=args.quality_output, independent_cache=args.independent,
                review_sidecar=args.review_sidecar)
        elif args.command == "pdf-quality":
            from .quality_gate import assess_package, write_quality_report
            from .quality_calibration import evaluate_calibration
            from .quality_batch import bound_independent
            target = args.output.resolve()
            inputs = [args.source_pdf, args.independent, args.policy, args.references]
            if target.is_relative_to(args.package.resolve()) or any(
                    target == path.resolve() for path in inputs if path is not None):
                raise ValueError("quality output must not overwrite source, package or evaluation inputs")
            independent, metadata, stale = {}, {}, 0
            if args.independent:
                document = json.loads((args.package / "evidence-package.json").read_text())["document"]
                page_versions = {row["page"]: row["text_sha256"] for row in (
                    json.loads(line) for line in (args.package / "pages.jsonl").read_text().splitlines() if line.strip())}
                rows = [json.loads(line) for line in args.independent.read_text(encoding="utf-8").splitlines() if line.strip()]
                rows = [row for row in rows if row.get("source_pdf_sha256") == document["pdf_sha256"] and
                        (args.pages is None or row.get("page") in args.pages)]
                independent, metadata, stale = bound_independent(rows, page_versions, document["pdf_sha256"])
            result = assess_package(args.package, args.source_pdf, pages=args.pages,
                                    independent_by_page=independent, policy_path=args.policy,
                                    independent_metadata_by_page=metadata,
                                    derive_ocr_source_raster=args.source_raster)
            result["stale_independent_versions_ignored"] = stale
            if args.references:
                references = [json.loads(line) for line in args.references.read_text(encoding="utf-8").splitlines() if line.strip()]
                result["calibration"] = evaluate_calibration(result["results"], references)
            write_quality_report(result, args.output)
        elif args.command == "audit-review":
            from .review_audit import audit_review_pages
            result = audit_review_pages(args.input, args.details)
        elif args.command == "audit-rendered-review":
            from .review_audit import audit_rendered_review_pages
            result = audit_rendered_review_pages(args.input, args.output, args.workers)
        elif args.command == "serve":
            from .web import serve
            serve(args.port, args.max_upload_mb, review_manifest=args.review_manifest,
                  review_decisions=args.review_decisions)
            return 0
        else:
            keys = Config.__dataclass_fields__.keys()
            values = vars(args)
            result = run(Config(input_path=values["input"], output_dir=values["output"],
                                **{key: values[key] for key in keys if key not in {"input_path", "output_dir"}}))
            result = {"complete": result["complete"], "batches": len(result["batches"]),
                      "vectors": sum(item["count"] for item in result["batches"]),
                      "dimensions": result["dimensions"], "model": result["model"],
                      "device": result["device"], "output": str(args.output),
                      "input_coverage_complete": result.get("input_coverage_complete"),
                      "token_budget_validated": result.get("token_budget_validated", False),
                      "skipped_input_records": result.get("input_audit", {}).get("records_without_passages")}
        print(json.dumps(result, ensure_ascii=False) if args.json else json.dumps(result, indent=2, ensure_ascii=False))
        if args.command == "pdf-quality" and args.require_accepted and (
                result.get("packages_blocked") or result.get("counts", {}).get("repair_required") or
                result.get("counts", {}).get("source_blocked") or not result.get("pages_assessed")):
            return 3
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(json.dumps({"error": str(error)}) if getattr(args, "json", False) else f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
