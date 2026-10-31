"""Small, scriptable CLI; stdout is JSON with --json."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

import argparse
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

from .pipeline import Config, run, verify


def doctor() -> dict:
    result = {"tool": "PDF2Data", "platform": platform.platform(), "machine": platform.machine(),
              "mlx_installed": importlib.util.find_spec("mlx_embedding_models") is not None,
              "mlx_core_installed": importlib.util.find_spec("mlx") is not None,
              "torch_installed": importlib.util.find_spec("torch") is not None,
              "pdf_installed": importlib.util.find_spec("pdfplumber") is not None,
              "rapidocr_installed": importlib.util.find_spec("rapidocr") is not None,
              "docling_installed": importlib.util.find_spec("docling") is not None,
              "vision_ocr_available": platform.system() == "Darwin" and shutil.which("swiftc") is not None,
              "tesseract_available": shutil.which("tesseract") is not None}
    if importlib.util.find_spec("mlx") is not None:
        import mlx.core as mx
        result["mlx_default_device"] = str(mx.default_device())
        result["mlx_metal_available"] = mx.metal.is_available()
    if result["torch_installed"]:
        import torch
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["mps_available"] = bool(torch.backends.mps.is_available())
    return result


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="pdf2data",
                                   description="PDF2Data: source-backed PDF extraction, structured data, quality, local AI repair and embeddings")
    from . import __version__
    main.add_argument('--version',action='version',version='PDF2Data '+__version__)
    sub = main.add_subparsers(dest="command", required=True)
    from .regulatory_candidates import configure as regulatory_parser
    regulatory_parser(sub.add_parser('regulatory',help='source-bound amendment, condition, value, reference and transition candidates'))
    from .field_rules import configure as field_parser
    field_parser(sub.add_parser('fields',help='extract, catalog, query and evaluate configurable source-bound metadata rules'))
    controller=sub.add_parser('pdf-control',help='inventory, measure, repair and recheck documents through a durable local feedback loop')
    operations=controller.add_subparsers(dest='operation',required=True)
    for operation in ('init','run','status','feedback','tasks'):
        control=operations.add_parser(operation)
        control.add_argument('--output',type=Path,required=True,help='isolated controller workspace')
        control.add_argument('--json',action='store_true')
        if operation=='init':control.add_argument('--input',type=Path,required=True,help='JSONL original/source hash and optional extraction package inventory')
        if operation=='feedback':control.add_argument('--references',type=Path,required=True,help='source-visual, version-bound calibration references with reviewer identity')
        if operation=='tasks':
            control.add_argument('--limit',type=int,default=25);control.add_argument('--offset',type=int,default=0)
            control.add_argument('--include-unlabelled',action='store_true')
        if operation=='run':
            control.add_argument('--max-documents',type=int,default=10)
            control.add_argument('--max-rounds',type=int,default=2)
            control.add_argument('--ocr',choices=('off','auto','vision','tesseract','rapidocr'),default='off')
    prep = sub.add_parser("benchmark-prepare", help="freeze the pinned public olmOCR suite without label-based selection")
    prep.add_argument("--dataset-root", type=Path, required=True);prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--custom", action="store_true", help="local rules with explicitly custom provenance, not the public dataset")
    prep.add_argument("--per-category", type=int);prep.add_argument("--seed", default="pdf2data-v1");prep.add_argument("--json", action="store_true")
    bench = sub.add_parser("benchmark-run", help="run source-only extraction profiles over a frozen suite")
    bench.add_argument("--suite", type=Path, required=True);bench.add_argument("--profile", choices=("native","auto","layout-vlm"), required=True)
    bench.add_argument("--output", type=Path, required=True);bench.add_argument("--parser-python", type=Path, default=Path(sys.executable))
    bench.add_argument("--reuse-readings",type=Path);bench.add_argument("--layout-python",type=Path);bench.add_argument("--workers", type=int, default=4);bench.add_argument("--resume", action="store_true");bench.add_argument("--json", action="store_true")
    bench.add_argument("--reuse-layout",type=Path)
    bench.add_argument("--text-fallback",choices=('none','vision'),default='none')
    ollama_bench=sub.add_parser('benchmark-ollama-run',help='evaluate a locally installed OCR model on frozen source pages; no model pull or approval')
    for key in ('suite','output'):ollama_bench.add_argument('--'+key,type=Path,required=True)
    ollama_bench.add_argument('--host',default='http://127.0.0.1:11434');ollama_bench.add_argument('--model',default='deepseek-ocr:3b')
    ollama_bench.add_argument('--max-tokens',type=int,default=8192);ollama_bench.add_argument('--dpi',type=int,default=180)
    ollama_bench.add_argument('--timeout',type=int,default=240);ollama_bench.add_argument('--resume',action='store_true');ollama_bench.add_argument('--json',action='store_true')
    subset=sub.add_parser('benchmark-project-subset',help='project verified cached predictions to a frozen source-identical subset without inference')
    for key in ('suite','run','output'):subset.add_argument('--'+key,type=Path,required=True)
    subset.add_argument('--json',action='store_true')
    replay=sub.add_parser('benchmark-ollama-replay',help='revalidate hash-bound raw OCR responses after output-quality fixes; no model call')
    for key in ('suite','run','output'):replay.add_argument('--'+key,type=Path,required=True)
    replay.add_argument('--json',action='store_true')
    scoring = sub.add_parser("benchmark-score", help="score predictions with the pinned official olmOCR rule classes")
    for key in ("suite","run","upstream","eval-python","output"):scoring.add_argument("--"+key,type=Path,required=True)
    scoring.add_argument("--mode",choices=("text","tables"),default="tables");scoring.add_argument("--json",action="store_true")
    comparison = sub.add_parser("benchmark-compare", help="compare matched external evaluation reports")
    comparison.add_argument("--score",type=Path,action="append",required=True);comparison.add_argument("--output",type=Path,required=True);comparison.add_argument("--json",action="store_true")
    layout = sub.add_parser("pdf-layout-detect", help="detect source-bound page regions offline without changing existing extraction")
    layout.add_argument("--manifest",type=Path,required=True);layout.add_argument("--output",type=Path,required=True)
    layout.add_argument("--device",choices=("mps","cpu","cuda"),default="mps");layout.add_argument("--dpi",type=int,default=180);layout.add_argument("--json",action="store_true")
    recover=sub.add_parser("pdf-layout-recover",help="partition failed source regions at measured whitespace without benchmark labels")
    for key in ("layout","read","output"):recover.add_argument("--"+key,type=Path,required=True)
    recover.add_argument("--max-rows",type=int,default=4);recover.add_argument("--max-parts",type=int,default=16);recover.add_argument("--json",action="store_true")
    visual = sub.add_parser("pdf-region-read", help="read source-bound text table and formula crops with pinned local PaddleOCR-VL")
    visual.add_argument("--layout",type=Path,required=True);visual.add_argument("--output",type=Path,required=True)
    visual.add_argument("--reuse-readings",type=Path);visual.add_argument("--device",choices=("gpu","cpu"),default="gpu");visual.add_argument("--max-tokens",type=int,default=4096);visual.add_argument("--resume",action="store_true");visual.add_argument("--json",action="store_true")
    visual.add_argument("--text-fallback",choices=('none','vision'),default='none')
    reconcile = sub.add_parser("pdf-table-reconcile", help="retain native table cells only with source geometry and exact glyph evidence")
    for key in ("layout","read","native-packages","output"):reconcile.add_argument("--"+key,type=Path,required=True)
    reconcile.add_argument("--json",action="store_true")
    mapping = sub.add_parser("pdf-document-map", help="map existing extracted pages and supplied review notes to verified original rasters")
    for key in ("document", "layout", "output"):mapping.add_argument("--"+key,type=Path,required=True)
    mapping.add_argument("--notes",type=Path);mapping.add_argument("--json",action="store_true")
    structure_export=sub.add_parser("pdf-structure-export",help="adapt immutable region readings to shared source-linked structure, readable exports and search projections")
    for key in ("document","layout","output"):structure_export.add_argument("--"+key,type=Path,required=True)
    structure_export.add_argument("--json",action="store_true")
    visualize=sub.add_parser("pdf-visualize",help="render color-coded structure overlays on verified saved source pages")
    visualize.add_argument("--bundle",type=Path,required=True)
    visualize.add_argument("--output",type=Path,required=True)
    visualize.add_argument("--pages",type=int,nargs='+')
    visualize.add_argument("--opacity",type=float,default=.14)
    visualize.add_argument("--json",action="store_true")
    structure_eval=sub.add_parser("pdf-structure-evaluate",help="compare fixed-budget lexical retrieval of raw versus structured source evidence")
    for key in ("raw","structured","cases","output"):structure_eval.add_argument("--"+key,type=Path,required=True)
    structure_eval.add_argument("--max-chars",type=int,default=6000);structure_eval.add_argument("--seed-k",type=int,default=2);structure_eval.add_argument("--json",action="store_true")
    structure_bench=sub.add_parser("benchmark-structure-run",help="derive shared-structure predictions on a frozen benchmark without rerunning OCR")
    for key in ("suite","run","structured","output"):structure_bench.add_argument("--"+key,type=Path,required=True)
    structure_bench.add_argument("--json",action="store_true")
    derive = sub.add_parser("benchmark-reconcile-tables",help="reconcile native source tables without rerunning model inference")
    for key in ("suite","run","native-run","output"):derive.add_argument("--"+key,type=Path,required=True)
    derive.add_argument("--json",action="store_true")
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
    pdf.add_argument("--native-consensus", action="store_true", help="repair uniquely source-supported native lines using independent PDF readers")
    pdf.add_argument("--json", action="store_true")
    legal = sub.add_parser("legal-l1", help="run immutable offline legal-source extraction from a hash-bound manifest")
    legal.add_argument("--manifest", required=True, type=Path)
    legal.add_argument("--output", required=True, type=Path)
    legal.add_argument("--ocr", choices=["off", "auto", "vision", "tesseract", "rapidocr"], default="off")
    legal.add_argument("--ocr-language", default="eng")
    legal.add_argument("--resume", action="store_true")
    legal.add_argument("--require-content-ready", action="store_true", help="exit 3 if any page still requires source review or repair")
    legal.add_argument("--json", action="store_true")
    legal_verify = sub.add_parser("verify-legal-l1", help="verify original, extraction, layout and exact legal structure")
    legal_verify.add_argument("--output", required=True, type=Path)
    legal_verify.add_argument("--json", action="store_true")
    qprep = sub.add_parser("l1-qwen-prepare", help="freeze unresolved L1 original pages for source-bound offline Qwen reading")
    qprep.add_argument("--packages", required=True, type=Path)
    qprep.add_argument("--output", required=True, type=Path)
    qprep.add_argument("--json", action="store_true")
    qrun = sub.add_parser("l1-qwen-run", help="run pinned cached Qwen offline with resumable per-page receipts")
    qrun.add_argument("--cases", required=True, type=Path)
    qrun.add_argument("--output", required=True, type=Path)
    qrun.add_argument("--resume", action="store_true")
    qrun.add_argument("--max-tokens", type=int, default=4096)
    qrun.add_argument("--model-path", type=Path)
    qrun.add_argument("--device", choices=["gpu", "cpu"], default="gpu", help="MLX Metal GPU by default; fail rather than silently fall back")
    qrun.add_argument("--json", action="store_true")
    qvalid = sub.add_parser("l1-qwen-validate", help="compare source-bound readings without promoting unresolved content")
    qvalid.add_argument("--cases", required=True, type=Path)
    qvalid.add_argument("--readings", required=True, type=Path)
    qvalid.add_argument("--output", required=True, type=Path)
    qvalid.add_argument("--json", action="store_true")
    qproof = sub.add_parser("l1-qwen-reproof", help="reassess exact source-raster Qwen witnesses into a new immutable L1 version; never replace text")
    qproof.add_argument("--cases", required=True, type=Path)
    qproof.add_argument("--readings", required=True, type=Path)
    qproof.add_argument("--packages", required=True, type=Path)
    qproof.add_argument("--output", required=True, type=Path)
    qproof.add_argument("--json", action="store_true")
    export_l1 = sub.add_parser("export-l1-data", help="export ALL processed text/layout/tables and diagnostics, including flagged pages")
    export_l1.add_argument("--packages", required=True, type=Path)
    export_l1.add_argument("--output", required=True, type=Path)
    export_l1.add_argument("--json", action="store_true")
    export_l1.add_argument("--region-packets", type=Path, help="include verified mixed native/raster candidates without clearing quality flags")
    query_export=sub.add_parser('export-query-data',help='export complete cached pages/tables/units with hashed raw sidecar references; no extraction or approval')
    query_export.add_argument('--packages',required=True,type=Path)
    query_export.add_argument('--output',required=True,type=Path)
    query_export.add_argument('--json',action='store_true')
    locations = sub.add_parser('source-locations', help='export every physical PDF page/region, including held or textless pages; no model approval')
    locations.add_argument('--input', type=Path, required=True, help='original PDF; reads geometry without rerunning extraction')
    locations.add_argument('--output', type=Path, required=True, help='new JSONL output outside the immutable source package')
    locations.add_argument('--package', type=Path, help='optional existing L1 or PDF extraction package for hash-bound cached Unicode text/quality')
    locations.add_argument('--bbox', type=float, nargs=4, default=(0, 0, 1, 1), help='normalized x0 y0 x1 y1 in the rotated declared CropBox; defaults to whole page')
    locations.add_argument('--json', action='store_true')
    observations=sub.add_parser('structured-table-observations',help='source-bound FY range, quantity, caption and header candidates; no legal applicability')
    for option in ('package','output'):observations.add_argument('--'+option,type=Path,required=True)
    observations.add_argument('--pages',type=int,nargs='+',required=True)
    observations.add_argument('--calendar-policy',help='explicit configured calendar policy; default does not derive dates')
    observations.add_argument('--rules',type=Path);observations.add_argument('--json',action='store_true')
    logical=sub.add_parser("native-logical-repair",help="revalidate source-bound layout token spaces and filter exact resolved review focuses")
    logical.add_argument('--manifest',type=Path,required=True)
    logical.add_argument('--source-runtime',type=Path,required=True,help='original immutable extraction runtime; distinct from current proof tool')
    logical.add_argument('--output',type=Path,required=True)
    logical.add_argument('--json',action='store_true')
    blank=sub.add_parser('exact-white-blank-reproof',help='strict RGB255/180dpi and two-empty-reader proof into a new immutable L1 quality version')
    for option in ('package','source-runtime','output'):blank.add_argument('--'+option,type=Path,required=True)
    blank.add_argument('--pages',type=int,nargs='+',required=True);blank.add_argument('--json',action='store_true')
    table_queue=sub.add_parser('auto-held-table-review',help='generate current source-bound held-table questions and exact source crops')
    for option in ('input','proof-runtime','output'):table_queue.add_argument('--'+option,type=Path,required=True)
    table_queue.add_argument('--source-runtime',type=Path,action='append',required=True)
    table_queue.add_argument('--limit',type=int,default=8);table_queue.add_argument('--scan-limit',type=int,default=80)
    table_queue.add_argument('--reviewed',type=Path,action='append',default=[]);table_queue.add_argument('--cache-dir',type=Path)
    table_queue.add_argument('--json',action='store_true')
    table_consume=sub.add_parser('consume-held-table-review',help='consume exact held-table feedback into terminal source revalidation receipts')
    for option in ('manifest','feedback','output'):table_consume.add_argument('--'+option,type=Path,required=True)
    table_consume.add_argument('--json',action='store_true')
    for command,help_text in [('native-table-cell-revalidate','consume source-bound native table-cell feedback'),('native-table-region-reproof','reprove plural native table regions and outside prose into new immutable L1')]:
        table=sub.add_parser(command,help=help_text)
        for option in ('package','source-runtime','feedback','dossier','output'):table.add_argument('--'+option,type=Path,required=True)
        table.add_argument('--page',type=int,required=True);table.add_argument('--json',action='store_true')
    image=sub.add_parser('native-image-regions',help='include exact original embedded images with CPU OCR candidates and strict mixed-page completeness holds')
    for option in ('package','source-runtime','output'):image.add_argument('--'+option,type=Path,required=True)
    image.add_argument('--page',type=int,required=True);image.add_argument('--readings',type=Path,help='reuse original source-bound candidate readings without model inference')
    image.add_argument('--json',action='store_true')
    image_verify=sub.add_parser('verify-native-image-regions',help='regenerate exact original raster regions and completeness gates')
    image_verify.add_argument('--output',type=Path,required=True);image_verify.add_argument('--json',action='store_true')
    targeted=sub.add_parser('targeted-native-reproof',help='bounded CPU source reproof sidecar; preserve original L1')
    targeted.add_argument('--package',type=Path,required=True);targeted.add_argument('--source-runtime',type=Path,required=True)
    targeted.add_argument('--pages',type=int,nargs='+',required=True);targeted.add_argument('--output',type=Path,required=True);targeted.add_argument('--json',action='store_true')
    repair_verify=sub.add_parser('verify-native-quality-repair',help='freshly reconstruct registered source-quality repair evidence')
    repair_verify.add_argument('--package',type=Path,required=True);repair_verify.add_argument('--source-runtime',type=Path,required=True);repair_verify.add_argument('--json',action='store_true')
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
    readable = sub.add_parser("export-readable", help="export a portable PDF, Markdown, HTML, JSON and optional Word output folder from immutable L1")
    readable.add_argument('--package', required=True, type=Path)
    readable.add_argument('--output', required=True, type=Path)
    readable.add_argument('--docx-python', type=Path, help='optional Python runtime with python-docx installed')
    readable.add_argument('--rules',type=Path,help='versioned extraction rules; also configurable with PDF2DATA_RULES')
    readable.add_argument('--json', action='store_true')
    profiles=sub.add_parser('profile-documents',help='measure bilingual keyword and metadata patterns in verified L1 packages')
    profiles.add_argument('--manifest',required=True,type=Path)
    profiles.add_argument('--output',required=True,type=Path)
    profiles.add_argument('--json',action='store_true')
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
    workspace = sub.add_parser('workspace', help='start a persistent local document workspace with upload, CLI processing, status and readable outputs')
    workspace.add_argument('--workspace', type=Path, required=True)
    workspace.add_argument('--port', type=int, default=8771)
    workspace.add_argument('--max-upload-mb', type=int, default=150)
    workspace.add_argument('--docx-python', type=Path)
    web = sub.add_parser("serve", help="open the local PDF review and embedding webpage")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--max-upload-mb", type=int, default=150)
    web.add_argument("--review-manifest", type=Path, help="optional trusted local OCR review queue JSONL")
    web.add_argument("--review-decisions", type=Path, help="local feedback JSONL (defaults beside the manifest)")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'regulatory':
            from .regulatory_candidates import execute
            result=execute(args)
        elif args.command == 'fields':
            from .field_rules import execute
            result=execute(args)
        elif args.command == "pdf-layout-recover":
            from .region_recovery import recover_layout
            result=recover_layout(args.layout,args.read,args.output,args.max_rows,args.max_parts)
        elif args.command == "benchmark-structure-run":
            from .benchmark import structure_projection_run
            result=structure_projection_run(args.suite,args.run,args.structured,args.output)
        elif args.command == "pdf-structure-evaluate":
            from .structure_evaluation import evaluate
            result=evaluate(args.raw,args.structured,args.cases,args.output,args.max_chars,args.seed_k)
        elif args.command == "pdf-structure-export":
            from .region_document import export_regions
            result=export_regions(args.document,args.layout,args.output)
        elif args.command == "pdf-document-map":
            from .page_map import build_page_map
            result=build_page_map(args.document,args.layout,args.output,args.notes)
        elif args.command == "pdf-table-reconcile":
            from .table_reconcile import reconcile
            result=reconcile(args.layout,args.read,args.native_packages,args.output)
        elif args.command == "benchmark-reconcile-tables":
            from .benchmark import reconcile_tables_run
            result=reconcile_tables_run(args.suite,args.run,args.native_run,args.output)
        elif args.command == "pdf-layout-detect":
            from .structured import detect
            result=detect(args.manifest,args.output,args.device,args.dpi)
        elif args.command == "pdf-visualize":
            from .layout_overlay import visualize_bundle
            result=visualize_bundle(args.bundle,args.output,args.pages,args.opacity)
        elif args.command == "pdf-region-read":
            from .structured import read_regions
            result=read_regions(args.layout,args.output,args.device,args.max_tokens,args.resume,args.reuse_readings,args.text_fallback)
        elif args.command=='pdf-control':
            from . import control_loop
            if args.operation=='init':result=control_loop.initialize(args.input,args.output)
            elif args.operation=='feedback':result=control_loop.add_feedback(args.output,args.references)
            elif args.operation=='status':result=control_loop.status(args.output)
            elif args.operation=='tasks':result=control_loop.tasks(args.output,args.limit,args.offset,args.include_unlabelled)
            else:result=control_loop.run(args.output,args.max_documents,args.max_rounds,args.ocr)
        elif args.command == "benchmark-prepare":
            from .benchmark import prepare
            result = prepare(args.dataset_root,args.output,args.per_category,args.seed,args.custom)
        elif args.command == "benchmark-run":
            from .benchmark import run as benchmark_run
            result = benchmark_run(args.suite,args.profile,args.output,args.parser_python,args.workers,args.resume,args.layout_python,args.reuse_readings,args.text_fallback,args.reuse_layout)
        elif args.command=='benchmark-ollama-run':
            from .ollama_ocr_benchmark import run
            result=run(args.suite,args.output,args.host,args.model,args.max_tokens,args.dpi,args.timeout,args.resume)
        elif args.command=='benchmark-project-subset':
            from .ollama_ocr_benchmark import subset_run
            result=subset_run(args.suite,args.run,args.output)
        elif args.command=='benchmark-ollama-replay':
            from .ollama_ocr_benchmark import replay
            result=replay(args.suite,args.run,args.output)
        elif args.command == "benchmark-score":
            from .benchmark import score
            result = score(args.suite,args.run,args.upstream,args.eval_python,args.output,args.mode)
        elif args.command == "benchmark-compare":
            from .benchmark import compare
            result = compare(args.score,args.output)
        elif args.command == "doctor":
            result = doctor()
        elif args.command == "verify":
            result = verify(args.output)
        elif args.command == "legal-l1":
            from .legal_l1 import run as run_legal_l1
            result = run_legal_l1(args.manifest, args.output, ocr=args.ocr,
                                  language=args.ocr_language, resume=args.resume)
            print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
            return 3 if result["failed"] or not result["all_required_members_supplied"] or (args.require_content_ready and result["content_ready_documents"] != len(result["documents"])) else 0
        elif args.command == "verify-legal-l1":
            from .legal_l1 import verify_document
            result = verify_document(args.output)
        elif args.command == "l1-qwen-prepare":
            from .l1_qwen import prepare
            result = prepare(args.packages, args.output)
        elif args.command == "l1-qwen-run":
            from .l1_qwen import run as qwen_run
            result = qwen_run(args.cases, args.output, resume=args.resume, max_tokens=args.max_tokens, model_path=args.model_path, device=args.device)
        elif args.command == "l1-qwen-validate":
            from .l1_qwen import validate
            result = validate(args.cases, args.readings, args.output)
        elif args.command == "l1-qwen-reproof":
            from .l1_qwen import reproof
            result = reproof(args.cases, args.readings, args.packages, args.output)
        elif args.command == 'structured-table-observations':
            from .structured_table_observations import write_observations
            result=write_observations(args.package,args.pages,args.output,calendar_policy=args.calendar_policy,rules_path=args.rules)
        elif args.command == "export-l1-data":
            from .l1_export import export_processed
            result = export_processed(args.packages, args.output, args.region_packets)
        elif args.command == 'export-query-data':
            from .query_export import export_query_data
            result = export_query_data(args.packages,args.output)
        elif args.command == 'source-locations':
            from .source_locations import export_source_locations
            result = export_source_locations(args.input, args.output, package=args.package, bbox=args.bbox)
        elif args.command=='native-logical-repair':
            from .native_quality_repair import repair_review_queue
            result=repair_review_queue(args.manifest,args.source_runtime,args.output)
        elif args.command=='exact-white-blank-reproof':
            from .native_blank_proof import create_blank_version
            result=create_blank_version(args.package,args.source_runtime,args.pages,args.output)
        elif args.command=='auto-held-table-review':
            from .native_table_review_queue import generate_queue
            result=generate_queue(args.input,args.source_runtime,args.proof_runtime,args.output,limit=args.limit,scan_limit=args.scan_limit,reviewed=args.reviewed,cache_dir=args.cache_dir)
        elif args.command=='consume-held-table-review':
            from .native_table_review_queue import consume_answers
            result=consume_answers(args.manifest,args.feedback,args.output)
        elif args.command=='native-table-cell-revalidate':
            from .native_table_feedback import revalidate
            result=revalidate(args.package,args.source_runtime,args.feedback,args.dossier,args.output,args.page)
        elif args.command=='native-table-region-reproof':
            from .native_table_regions import build_version
            result=build_version(args.package,args.source_runtime,args.feedback,args.dossier,args.page,args.output)
        elif args.command=='native-image-regions':
            from .native_image_regions import build
            result=build(args.package,args.source_runtime,args.page,args.output,args.readings)
        elif args.command=='verify-native-image-regions':
            from .native_image_regions import verify
            result=verify(args.output)
        elif args.command=='targeted-native-reproof':
            from .targeted_reproof import create_reproof
            result=create_reproof(args.package,args.source_runtime,args.pages,args.output)
        elif args.command=='verify-native-quality-repair':
            from .proof_identity import verify_source_quality_repair
            result=verify_source_quality_repair(args.package,args.source_runtime)
        elif args.command == "pdf-extract":
            from .pdf_ingest import extract_pdf
            result = extract_pdf(args.input, args.output, title=args.title,
                                 source_url=args.source_url, tables=args.tables,
                                 ocr=args.ocr, ocr_language=args.ocr_language,
                                 ocr_device=args.ocr_device, native_consensus=args.native_consensus)
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
                rows = [json.loads(line) for line in jsonl_physical_lines(path.read_text(encoding="utf-8")) if line.strip(' \t\r')]
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError("evidence inputs must contain JSON objects")
                return rows
            result = validate_candidates(records(args.candidates), records(args.pages))
        elif args.command == "verify-package":
            from .evidence_package import verify_package
            result = verify_package(args.output, args.vectors, args.source_pdf)
            if args.require_ready and (not result["original_pdf_checked"] or result["page_text_readiness"] != "complete"):
                raise ValueError("page text is not ready for retrieval: check original PDF and resolve review_required_pages")
        elif args.command == "export-readable":
            from .readable_export import export_readable
            result = export_readable(args.package,args.output,args.docx_python,args.rules)
        elif args.command == 'profile-documents':
            from .document_profiles import profile_documents
            result=profile_documents(args.manifest,args.output)
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
                jsonl_physical_lines((args.package / "pages.jsonl").read_text())) if row["page"] == args.page), None)
            if page is None or not 1 <= args.max_pairs <= 16:
                raise ValueError("valid physical page and max-pairs 1..16 required")
            independent = next((row for row in (json.loads(line) for line in jsonl_physical_lines(args.independent.read_text()))
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
                jsonl_physical_lines((args.package / "pages.jsonl").read_text())) if row["page"] == args.page), None)
            if page is None:
                raise ValueError("source page missing")
            independent = next((row for row in (json.loads(line) for line in jsonl_physical_lines(args.independent.read_text()))
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
                    json.loads(line) for line in jsonl_physical_lines((args.package / "pages.jsonl").read_text()) if line.strip(' \t\r'))}
                rows = [json.loads(line) for line in jsonl_physical_lines(args.independent.read_text(encoding="utf-8")) if line.strip(' \t\r')]
                rows = [row for row in rows if row.get("source_pdf_sha256") == document["pdf_sha256"] and
                        (args.pages is None or row.get("page") in args.pages)]
                independent, metadata, stale = bound_independent(rows, page_versions, document["pdf_sha256"])
            result = assess_package(args.package, args.source_pdf, pages=args.pages,
                                    independent_by_page=independent, policy_path=args.policy,
                                    independent_metadata_by_page=metadata,
                                    derive_ocr_source_raster=args.source_raster)
            result["stale_independent_versions_ignored"] = stale
            if args.references:
                references = [json.loads(line) for line in jsonl_physical_lines(args.references.read_text(encoding="utf-8")) if line.strip(' \t\r')]
                result["calibration"] = evaluate_calibration(result["results"], references)
            write_quality_report(result, args.output)
        elif args.command == "audit-review":
            from .review_audit import audit_review_pages
            result = audit_review_pages(args.input, args.details)
        elif args.command == "audit-rendered-review":
            from .review_audit import audit_rendered_review_pages
            result = audit_rendered_review_pages(args.input, args.output, args.workers)
        elif args.command == 'workspace':
            from .workspace import serve_workspace
            serve_workspace(args.workspace,args.port,args.max_upload_mb,args.docx_python)
            return 0
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
        if args.command=='regulatory'and args.regulatory_operation=='evaluate'and result.get('failed'):return 3
        if args.command=='fields'and args.field_operation=='evaluate'and not result.get('passed'):return 3
        if args.command=='native-table-cell-revalidate'and result.get('status')!='validated':return 3
        if args.command=='native-table-region-reproof'and result.get('status')!='passed':return 3
        if args.command=='exact-white-blank-reproof'and result.get('status')!='passed':return 3
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
