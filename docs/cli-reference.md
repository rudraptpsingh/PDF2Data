# PDF2Data CLI reference

Generated from the actual command parser. All commands use `pdf2data`; `local-vector-batch` remains a compatibility alias.

## Run from this checkout

```bash
cd /Users/rp/.codex/worktrees/legal-l1-build/PDF2Data
export PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv/bin/python
./pdf2data --help
```

After installing the package, use `pdf2data` without `./`. For Qwen use the MLX-VLM environment shown below. No database or Cloudflare operation is performed by the examples in this document.

## Every exposed command

For source mapping, `pdf-document-map --document document.json --layout layout.json
--notes review-notes.json --output out/page-map --json` reads existing artifacts,
checks original PDF/raster identity and page/block bindings, and creates a separate
comparison. Notes are supplied review observations; the command does not infer
legal relationships or rerun OCR. Use a new output folder.

| Command | Purpose |
|---|---|
| `pdf2data benchmark-prepare` | freeze source PDFs and external labels by hash |
| `pdf2data benchmark-run` | run native or automatic OCR profiles through the PDF2Data CLI |
| `pdf2data benchmark-score` | execute pinned upstream olmOCR-bench rule classes |
| `pdf2data benchmark-compare` | compare reports from an identical frozen suite |
| `pdf2data pdf-layout-detect` | detect source-bound original-page regions offline |
| `pdf2data pdf-region-read` | read pinned local text/table/formula candidates from verified crops |
| `pdf2data pdf-table-reconcile` | reconcile source native tables with independently read values and symbols |
| `pdf2data pdf-document-map` | map existing page/block exports and supplied review notes to original source rasters |
| `pdf2data pdf-structure-export` | adapt region readings to shared blocks/spans, readable views, source relations and search projections |
| `pdf2data pdf-structure-evaluate` | compare fixed-character-budget lexical retrieval over raw and structured evidence |
| `pdf2data benchmark-structure-run` | derive shared-structure benchmark predictions without rerunning OCR |
| `pdf2data benchmark-reconcile-tables` | compare source-table reconciliation without rerunning model inference |
| `pdf2data doctor` | show installed backends and accelerator availability |
| `pdf2data embed` | embed documents from JSONL |
| `pdf2data verify` | check hashes, dimensions, and unique vector IDs |
| `pdf2data pdf-extract` | extract and review PDF pages, tables, and candidate values locally |
| `pdf2data legal-l1` | run immutable offline legal-source extraction from a hash-bound manifest |
| `pdf2data verify-legal-l1` | verify original, extraction, layout and exact legal structure |
| `pdf2data l1-qwen-prepare` | freeze unresolved L1 original pages for source-bound offline Qwen reading |
| `pdf2data l1-qwen-run` | run pinned cached Qwen offline with resumable per-page receipts |
| `pdf2data l1-qwen-validate` | compare source-bound readings without promoting unresolved content |
| `pdf2data l1-qwen-reproof` | reassess exact source-raster Qwen witnesses into a new immutable L1 version; never replace text |
| `pdf2data export-l1-data` | export ALL processed text/layout/tables and diagnostics, including flagged pages |
| `pdf2data pdf-repair` | repair selected original physical pages without reprocessing the PDF |
| `pdf2data pdf-native-repair` | atomically replace one page from a source-bound PDFium/pypdf/raster receipt |
| `pdf2data pdf-batch` | extract a bounded manifest of local PDFs with resumable page receipts |
| `pdf2data validate-evidence` | check structured candidate quotes and typed fields without verification |
| `pdf2data verify-package` | verify a PDF evidence package and optional vector anchors |
| `pdf2data render-package` | add linked Markdown/HTML and layout JSON to an existing verified PDF package without re-extracting |
| `pdf2data export-readable` | full document Markdown/HTML/JSON, relationships, source previews and optional Word from an immutable L1 package |
| `pdf2data import-paddle-layout` | attach PP-StructureV2/V3 page blocks as review-only candidates |
| `pdf2data review-package` | export only explicitly reviewed PDF evidence units |
| `pdf2data ocr-consensus` | dry-run independent RapidOCR audit of existing Vision pages |
| `pdf2data audit-review` | inventory review-required PDF pages by reason and source |
| `pdf2data audit-rendered-review` | inspect rendered originals for short or missing PDF pages |
| `pdf2data pdf-compare` | compare review pages with optional local Docling OCR/layout |
| `pdf2data pdf-quality` | score source-bound PDF extraction; auto-accept safe pages and list repair reasons |
| `pdf2data pdf-quality-batch` | parallel quality gates over cached PDF extraction, without new OCR |
| `pdf2data pdf-repair-queue` | resume source-bound staged page repairs without publishing unverified candidates |
| `pdf2data pdf-auto-proof` | resume source-bound footnote proof and guarded local commits |
| `pdf2data pdf-repair-status` | read repair queue status without starting work |
| `pdf2data pdf-footnote-proof` | source-bound crop proof for linked printed footnote markers |
| `pdf2data pdf-vision-merge` | opt-in local vision suggestions for source-bound OCR lines |
| `pdf2data pdf-vision-merge-batch` | resumable source-bound local OCR merge proposals for held pages |
| `pdf2data pdf-blank-commit` | CAS-mark exactly white source PDF pages blank from 180-DPI receipts |
| `pdf2data pdf-footnote-repair` | reprove linked markers and stage only a fully gated page |
| `pdf2data pdf-repair-commit` | atomically merge freshly verified staged page repairs; index publication is separate |
| `pdf2data pdf-review-queue` | prepare a source-bound OCR disagreement review queue for held pages |
| `pdf2data pdf-review-feedback` | summarize saved OCR disagreement review labels |
| `pdf2data pdf-review-route` | revalidate focused OCR feedback and queue source crop proof work |
| `pdf2data pdf-review-proposals` | attach source-crop observations as unverified review suggestions |
| `pdf2data pdf-review-filter` | skip already reviewed exact source and OCR differences |
| `pdf2data serve` | open the local PDF review and embedding webpage |
| `pdf2data workspace` | persistent local PDF intake, CLI processing, stage tracking, run history and output downloads |

### workspace

```bash
pdf2data workspace --workspace <new-or-existing-workspace> --port 8771
```

Optional flags: `--max-upload-mb 150`, `--docx-python <python-with-python-docx>`. Access through `127.0.0.1`, not a public network interface. Mutations require the local page token. Jobs run sequentially through the same CLI and preserve raw command receipts. Quality flags are displayed separately from completed processing. Restart marks unfinished jobs interrupted; retry creates a fresh attempt. Existing `serve`/OCR review sessions are unaffected.

## Exact flags and runnable command templates

Angle-bracket arguments below are placeholders. Use real source paths and new output directories. Optional model-dependent commands require their documented dependencies; listing a command does not mean it was executed in this run.

### doctor

show installed backends and accelerator availability

```bash
pdf2data doctor
```

```text
usage: pdf2data doctor [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json
```

### embed

embed documents from JSONL

```bash
pdf2data embed --input <input> --output <output>
```

```text
usage: pdf2data embed [-h] --input INPUT --output OUTPUT [--model MODEL]
                      [--backend {auto,mlx,torch}]
                      [--device {auto,gpu,mps,cuda,cpu}]
                      [--pooling {cls,mean}] [--format {vectorize,generic}]
                      [--input-mode {documents,chunks,passages}]
                      [--batch-size BATCH_SIZE] [--chunk-chars CHUNK_CHARS]
                      [--overlap OVERLAP] [--min-chars MIN_CHARS]
                      [--workers WORKERS] [--max-tokens MAX_TOKENS]
                      [--overflow-policy {error,split}]
                      [--token-overlap TOKEN_OVERLAP]
                      [--normalize | --no-normalize] [--revision REVISION]
                      [--threads-per-worker THREADS_PER_WORKER] [--offline]
                      [--resume] [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT
  --output OUTPUT
  --model MODEL
  --backend {auto,mlx,torch}
  --device {auto,gpu,mps,cuda,cpu}
  --pooling {cls,mean}
  --format {vectorize,generic}
  --input-mode {documents,chunks,passages}
                        chunks requires chunk_index; passages also requires
                        absolute char_start and preserves each exact slice
  --batch-size BATCH_SIZE
  --chunk-chars CHUNK_CHARS
  --overlap OVERLAP
  --min-chars MIN_CHARS
  --workers WORKERS
  --max-tokens MAX_TOKENS
  --overflow-policy {error,split}
  --token-overlap TOKEN_OVERLAP
  --normalize, --no-normalize
  --revision REVISION   Hugging Face revision, Torch backend only
  --threads-per-worker THREADS_PER_WORKER
  --offline             use cached model files only
  --resume
  --json
```

### verify

check hashes, dimensions, and unique vector IDs

```bash
pdf2data verify --output <output>
```

```text
usage: pdf2data verify [-h] --output OUTPUT [--json]

options:
  -h, --help       show this help message and exit
  --output OUTPUT
  --json
```

### pdf-extract

extract and review PDF pages, tables, and candidate values locally

```bash
pdf2data pdf-extract --input <input> --output <output>
```

```text
usage: pdf2data pdf-extract [-h] --input INPUT --output OUTPUT [--title TITLE]
                            [--source-url SOURCE_URL] [--tables | --no-tables]
                            [--ocr {auto,off,vision,tesseract,rapidocr}]
                            [--ocr-device {auto,cpu,gpu}]
                            [--ocr-language OCR_LANGUAGE] [--native-consensus]
                            [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT
  --output OUTPUT
  --title TITLE
  --source-url SOURCE_URL
  --tables, --no-tables
  --ocr {auto,off,vision,tesseract,rapidocr}
  --ocr-device {auto,cpu,gpu}
                        Vision auto lets macOS schedule hardware; forced GPU
                        is unsupported
  --ocr-language OCR_LANGUAGE
                        OCR language hint, such as eng, hin or eng+hin
  --native-consensus    repair uniquely source-supported native lines using
                        independent PDF readers
  --json
```

### legal-l1

run immutable offline legal-source extraction from a hash-bound manifest

```bash
pdf2data legal-l1 --manifest <manifest> --output <output>
```

```text
usage: pdf2data legal-l1 [-h] --manifest MANIFEST --output OUTPUT
                         [--ocr {off,auto,vision,tesseract,rapidocr}]
                         [--ocr-language OCR_LANGUAGE] [--resume]
                         [--require-content-ready] [--json]

options:
  -h, --help            show this help message and exit
  --manifest MANIFEST
  --output OUTPUT
  --ocr {off,auto,vision,tesseract,rapidocr}
  --ocr-language OCR_LANGUAGE
  --resume
  --require-content-ready
                        exit 3 if any page still requires source review or
                        repair
  --json
```

### verify-legal-l1

verify original, extraction, layout and exact legal structure

```bash
pdf2data verify-legal-l1 --output <output>
```

```text
usage: pdf2data verify-legal-l1 [-h] --output OUTPUT [--json]

options:
  -h, --help       show this help message and exit
  --output OUTPUT
  --json
```

### l1-qwen-prepare

freeze unresolved L1 original pages for source-bound offline Qwen reading

```bash
pdf2data l1-qwen-prepare --packages <packages> --output <output>
```

```text
usage: pdf2data l1-qwen-prepare [-h] --packages PACKAGES --output OUTPUT
                                [--json]

options:
  -h, --help           show this help message and exit
  --packages PACKAGES
  --output OUTPUT
  --json
```

### l1-qwen-run

run pinned cached Qwen offline with resumable per-page receipts

```bash
pdf2data l1-qwen-run --cases <cases> --output <output>
```

```text
usage: pdf2data l1-qwen-run [-h] --cases CASES --output OUTPUT [--resume]
                            [--max-tokens MAX_TOKENS]
                            [--model-path MODEL_PATH] [--device {gpu,cpu}]
                            [--json]

options:
  -h, --help            show this help message and exit
  --cases CASES
  --output OUTPUT
  --resume
  --max-tokens MAX_TOKENS
  --model-path MODEL_PATH
  --device {gpu,cpu}    MLX Metal GPU by default; fail rather than silently
                        fall back
  --json
```

### l1-qwen-validate

compare source-bound readings without promoting unresolved content

```bash
pdf2data l1-qwen-validate --cases <cases> --readings <readings> --output <output>
```

```text
usage: pdf2data l1-qwen-validate [-h] --cases CASES --readings READINGS
                                 --output OUTPUT [--json]

options:
  -h, --help           show this help message and exit
  --cases CASES
  --readings READINGS
  --output OUTPUT
  --json
```

### l1-qwen-reproof

reassess exact source-raster Qwen witnesses into a new immutable L1 version; never replace text

```bash
pdf2data l1-qwen-reproof --cases <cases> --readings <readings> --packages <packages> --output <output>
```

```text
usage: pdf2data l1-qwen-reproof [-h] --cases CASES --readings READINGS
                                --packages PACKAGES --output OUTPUT [--json]

options:
  -h, --help           show this help message and exit
  --cases CASES
  --readings READINGS
  --packages PACKAGES
  --output OUTPUT
  --json
```

### export-l1-data

export ALL processed text/layout/tables and diagnostics, including flagged pages

```bash
pdf2data export-l1-data --packages <packages> --output <output>
```

```text
usage: pdf2data export-l1-data [-h] --packages PACKAGES --output OUTPUT
                               [--json]

options:
  -h, --help           show this help message and exit
  --packages PACKAGES
  --output OUTPUT
  --json
```

### pdf-repair

repair selected original physical pages without reprocessing the PDF

```bash
pdf2data pdf-repair --input <input> --package <package> --pages <pages>
```

```text
usage: pdf2data pdf-repair [-h] --input INPUT --package PACKAGE --pages PAGES
                           [--ocr {auto,vision,tesseract,rapidocr}]
                           [--ocr-language OCR_LANGUAGE]
                           [--tables | --no-tables] [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         original source PDF
  --package PACKAGE     existing verified extraction package
  --pages PAGES
  --ocr {auto,vision,tesseract,rapidocr}
  --ocr-language OCR_LANGUAGE
  --tables, --no-tables
  --json
```

### pdf-native-repair

atomically replace one page from a source-bound PDFium/pypdf/raster receipt

```bash
pdf2data pdf-native-repair --package <package> --receipt <receipt>
```

```text
usage: pdf2data pdf-native-repair [-h] --package PACKAGE --receipt RECEIPT
                                  [--json]

options:
  -h, --help         show this help message and exit
  --package PACKAGE
  --receipt RECEIPT  one JSON source-bound machine receipt
  --json
```

### pdf-batch

extract a bounded manifest of local PDFs with resumable page receipts

```bash
pdf2data pdf-batch --input <input> --output <output>
```

```text
usage: pdf2data pdf-batch [-h] --input INPUT --output OUTPUT
                          [--workers WORKERS] [--limit LIMIT]
                          [--document-timeout DOCUMENT_TIMEOUT] [--resume]
                          [--repair-ocr] [--refresh]
                          [--ocr {auto,off,vision,tesseract,rapidocr}]
                          [--ocr-language OCR_LANGUAGE]
                          [--ocr-device {auto,cpu,gpu}]
                          [--tables | --no-tables] [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         JSONL rows with document_id, path, url and optional
                        sha256
  --output OUTPUT
  --workers WORKERS     parallel PDF processes, 1–16
  --limit LIMIT         maximum manifest rows in this run
  --document-timeout DOCUMENT_TIMEOUT
                        per-PDF extraction seconds; 0 disables, macOS/Linux
                        only
  --resume              verify and reuse existing complete outputs
  --repair-ocr          with --resume and OCR active, retry unreadable pages
                        and replace only improved packages
  --refresh             with --resume, rebuild existing packages after a
                        parser change using a verified atomic swap
  --ocr {auto,off,vision,tesseract,rapidocr}
  --ocr-language OCR_LANGUAGE
                        OCR language hint, such as eng, hin or eng+hin
  --ocr-device {auto,cpu,gpu}
  --tables, --no-tables
  --json
```

### validate-evidence

check structured candidate quotes and typed fields without verification

```bash
pdf2data validate-evidence --pages <pages> --candidates <candidates>
```

```text
usage: pdf2data validate-evidence [-h] --pages PAGES --candidates CANDIDATES
                                  [--json]

options:
  -h, --help            show this help message and exit
  --pages PAGES         extracted pages.jsonl
  --candidates CANDIDATES
                        typed candidates JSONL with evidence quotes
  --json
```

### verify-package

verify a PDF evidence package and optional vector anchors

```bash
pdf2data verify-package --output <output>
```

```text
usage: pdf2data verify-package [-h] --output OUTPUT [--vectors VECTORS]
                               [--source-pdf SOURCE_PDF] [--require-ready]
                               [--json]

options:
  -h, --help            show this help message and exit
  --output OUTPUT       pdf-extract output directory
  --vectors VECTORS     optional completed embed output directory
  --source-pdf SOURCE_PDF
                        optional original PDF for byte hash verification
  --require-ready       fail unless the original PDF is checked and every page
                        text is ready or blank
  --json
```

### export-readable

```bash
pdf2data export-readable --package <l1-package-directory> --output <new-readable-directory> --json
# Optional Word conversion through an explicitly selected runtime:
pdf2data export-readable --package <l1-package-directory> --output <new-readable-directory> --docx-python <python-with-python-docx> --json
```

Produces one continuous `document.md`, `document.html`, optional `document.docx`, `data.json`, `relations.json`, `quality.json`, exact `pages.md`, `source.pdf`, rendered page images and an artifact hash manifest. Whole paragraph candidates are joined across page breaks, with exact page links. Table candidates retain cell ownership. Fact/role observations retain source spans and containing-unit links. No new legal interpretation or model call occurs. Open the HTML file; full document is the default view, source comparison is secondary. Source packages are never modified; existing destinations are rejected.

### render-package

add linked Markdown/HTML and layout JSON to an existing verified PDF package without re-extracting

```bash
pdf2data render-package --output <output>
```

```text
usage: pdf2data render-package [-h] --output OUTPUT [--json]

options:
  -h, --help       show this help message and exit
  --output OUTPUT  pdf-extract output directory
  --json
```

### import-paddle-layout

attach PP-StructureV2/V3 page blocks as review-only candidates

```bash
pdf2data import-paddle-layout --package <package> --source-pdf <source-pdf> --manifest <manifest>
```

```text
usage: pdf2data import-paddle-layout [-h] --package PACKAGE --source-pdf
                                     SOURCE_PDF --manifest MANIFEST [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --manifest MANIFEST
  --json
```

### review-package

export only explicitly reviewed PDF evidence units

```bash
pdf2data review-package --output <output> --decisions <decisions> --source-pdf <source-pdf>
```

```text
usage: pdf2data review-package [-h] --output OUTPUT --decisions DECISIONS
                               --source-pdf SOURCE_PDF [--json]

options:
  -h, --help            show this help message and exit
  --output OUTPUT       pdf-extract output directory
  --decisions DECISIONS
                        review decisions JSON
  --source-pdf SOURCE_PDF
                        original PDF for byte hash verification
  --json
```

### ocr-consensus

dry-run independent RapidOCR audit of existing Vision pages

```bash
pdf2data ocr-consensus --input <input> --output <output>
```

```text
usage: pdf2data ocr-consensus [-h] --input INPUT --output OUTPUT
                              [--limit LIMIT] [--dpi DPI] [--workers WORKERS]
                              [--page-batch-size PAGE_BATCH_SIZE] [--resume]
                              [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         JSONL rows: package and source_pdf paths
  --output OUTPUT       page-level audit JSONL
  --limit LIMIT
  --dpi DPI
  --workers WORKERS     parallel OCR processes, 1–16
  --page-batch-size PAGE_BATCH_SIZE
                        pages per queued job, 1–64; models stay loaded per
                        process
  --resume              reuse exact source/page/DPI/engine-matched comparison
                        receipts
  --json
```

### audit-review

inventory review-required PDF pages by reason and source

```bash
pdf2data audit-review --input <input>
```

```text
usage: pdf2data audit-review [-h] --input INPUT [--details DETAILS] [--json]

options:
  -h, --help         show this help message and exit
  --input INPUT      pdf-batch extraction directory
  --details DETAILS  optional per-page JSONL work queue
  --json
```

### audit-rendered-review

inspect rendered originals for short or missing PDF pages

```bash
pdf2data audit-rendered-review --input <input> --output <output>
```

```text
usage: pdf2data audit-rendered-review [-h] --input INPUT --output OUTPUT
                                      [--workers WORKERS] [--json]

options:
  -h, --help         show this help message and exit
  --input INPUT      JSONL page rows with source PDF paths
  --output OUTPUT    render evidence JSONL
  --workers WORKERS
  --json
```

### pdf-compare

compare review pages with optional local Docling OCR/layout

```bash
pdf2data pdf-compare --package <package> --source-pdf <source-pdf> --output <output>
```

```text
usage: pdf2data pdf-compare [-h] --package PACKAGE --source-pdf SOURCE_PDF
                            --output OUTPUT [--pages PAGES] [--limit LIMIT]
                            [--language LANGUAGE]
                            [--device {auto,cpu,mps,cuda}] [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --output OUTPUT
  --pages PAGES
  --limit LIMIT
  --language LANGUAGE   BCP-47 language, e.g. iso:en or iso:hi
  --device {auto,cpu,mps,cuda}
  --json
```

### pdf-quality

score source-bound PDF extraction; auto-accept safe pages and list repair reasons

```bash
pdf2data pdf-quality --package <package> --source-pdf <source-pdf> --output <output>
```

```text
usage: pdf2data pdf-quality [-h] --package PACKAGE --source-pdf SOURCE_PDF
                            --output OUTPUT [--pages PAGES]
                            [--independent INDEPENDENT] [--policy POLICY]
                            [--references REFERENCES]
                            [--source-raster | --no-source-raster]
                            [--require-accepted] [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --output OUTPUT       machine quality receipt outside the extraction package
  --pages PAGES
  --independent INDEPENDENT
                        existing source-bound ocr-consensus JSONL; does not
                        run OCR again
  --policy POLICY       override the packaged quality policy
  --references REFERENCES
                        source-reviewed calibration/holdout reference JSONL
  --source-raster, --no-source-raster
  --require-accepted    exit 3 after writing the report if any assessed page
                        needs repair or a source is blocked
  --json
```

### pdf-quality-batch

parallel quality gates over cached PDF extraction, without new OCR

```bash
pdf2data pdf-quality-batch --input <input> --output <output>
```

```text
usage: pdf2data pdf-quality-batch [-h] --input INPUT --output OUTPUT
                                  [--independent INDEPENDENT]
                                  [--workers WORKERS] [--policy POLICY]
                                  [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         JSONL package/source_pdf/pages manifest
  --output OUTPUT
  --independent INDEPENDENT
                        source-bound cached ocr-consensus JSONL
  --workers WORKERS
  --policy POLICY
  --json
```

### pdf-repair-queue

resume source-bound staged page repairs without publishing unverified candidates

```bash
pdf2data pdf-repair-queue --input <input> --output <output>
```

```text
usage: pdf2data pdf-repair-queue [-h] --input INPUT --output OUTPUT
                                 [--workers WORKERS] [--limit LIMIT]
                                 [--config CONFIG] [--json]

options:
  -h, --help         show this help message and exit
  --input INPUT      JSONL package/source_pdf/quality_report/pages manifest
  --output OUTPUT    isolated candidates and durable repair ledger
  --workers WORKERS
  --limit LIMIT
  --config CONFIG    JSON repair configuration; changes invalidate cached
                     attempts
  --json
```

### pdf-auto-proof

resume source-bound footnote proof and guarded local commits

```bash
pdf2data pdf-auto-proof --input <input> --independent <independent> --output <output>
```

```text
usage: pdf2data pdf-auto-proof [-h] --input INPUT --independent INDEPENDENT
                               --output OUTPUT [--limit LIMIT]
                               [--workers WORKERS] [--retry-unresolved]
                               [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT
  --independent INDEPENDENT
  --output OUTPUT
  --limit LIMIT
  --workers WORKERS
  --retry-unresolved    retry prior held jobs; committed and stale jobs remain
                        excluded
  --json
```

### pdf-repair-status

read repair queue status without starting work

```bash
pdf2data pdf-repair-status --output <output>
```

```text
usage: pdf2data pdf-repair-status [-h] --output OUTPUT [--json]

options:
  -h, --help       show this help message and exit
  --output OUTPUT
  --json
```

### pdf-footnote-proof

source-bound crop proof for linked printed footnote markers

```bash
pdf2data pdf-footnote-proof --package <package> --source-pdf <source-pdf> --independent <independent> --page <page> --output <output>
```

```text
usage: pdf2data pdf-footnote-proof [-h] --package PACKAGE --source-pdf
                                   SOURCE_PDF --independent INDEPENDENT --page
                                   PAGE --output OUTPUT
                                   [--max-pairs MAX_PAIRS] [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --independent INDEPENDENT
                        source-bound independent OCR JSONL
  --page PAGE
  --output OUTPUT
  --max-pairs MAX_PAIRS
  --json
```

### pdf-vision-merge

opt-in local vision suggestions for source-bound OCR lines

```bash
pdf2data pdf-vision-merge --package <package> --source-pdf <source-pdf> --independent <independent> --page <page> --line <line> --output <output>
```

```text
usage: pdf2data pdf-vision-merge [-h] --package PACKAGE --source-pdf
                                 SOURCE_PDF --independent INDEPENDENT --page
                                 PAGE --line LINE --output OUTPUT [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --independent INDEPENDENT
                        source-bound independent OCR JSONL
  --page PAGE
  --line LINE           selected OCR line index; repeat at most eight times
  --output OUTPUT
  --json
```

### pdf-vision-merge-batch

resumable source-bound local OCR merge proposals for held pages

```bash
pdf2data pdf-vision-merge-batch --manifest <manifest> --independent <independent> --base <base> --output <output>
```

```text
usage: pdf2data pdf-vision-merge-batch [-h] --manifest MANIFEST --independent
                                       INDEPENDENT --base BASE --output OUTPUT
                                       [--limit LIMIT]
                                       [--lines-per-page LINES_PER_PAGE]
                                       [--json]

options:
  -h, --help            show this help message and exit
  --manifest MANIFEST
  --independent INDEPENDENT
  --base BASE           base directory for manifest-relative package paths
  --output OUTPUT
  --limit LIMIT
  --lines-per-page LINES_PER_PAGE
  --json
```

### pdf-blank-commit

CAS-mark exactly white source PDF pages blank from 180-DPI receipts

```bash
pdf2data pdf-blank-commit --manifest <manifest>
```

```text
usage: pdf2data pdf-blank-commit [-h] --manifest MANIFEST [--json]

options:
  -h, --help           show this help message and exit
  --manifest MANIFEST
  --json
```

### pdf-footnote-repair

reprove linked markers and stage only a fully gated page

```bash
pdf2data pdf-footnote-repair --package <package> --receipt <receipt> --output <output>
```

```text
usage: pdf2data pdf-footnote-repair [-h] --package PACKAGE --receipt RECEIPT
                                    --output OUTPUT
                                    [--docling-output DOCLING_OUTPUT] [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE     disposable staging package
  --receipt RECEIPT
  --output OUTPUT       source crop and quality proof directory
  --docling-output DOCLING_OUTPUT
                        source-bound one-page Docling table comparison, when a
                        table is present
  --json
```

### pdf-repair-commit

atomically merge freshly verified staged page repairs; index publication is separate

```bash
pdf2data pdf-repair-commit --package <package> --source-pdf <source-pdf> --results <results> --independent <independent> --quality-output <quality-output>
```

```text
usage: pdf2data pdf-repair-commit [-h] --package PACKAGE --source-pdf
                                  SOURCE_PDF --results RESULTS --independent
                                  INDEPENDENT --quality-output QUALITY_OUTPUT
                                  [--review-sidecar REVIEW_SIDECAR] [--json]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --source-pdf SOURCE_PDF
  --results RESULTS     repair queue result JSON
  --independent INDEPENDENT
  --quality-output QUALITY_OUTPUT
  --review-sidecar REVIEW_SIDECAR
  --json
```

### pdf-review-queue

prepare a source-bound OCR disagreement review queue for held pages

```bash
pdf2data pdf-review-queue --input <input> --independent <independent> --output <output>
```

```text
usage: pdf2data pdf-review-queue [-h] --input INPUT --independent INDEPENDENT
                                 --output OUTPUT [--limit LIMIT]
                                 [--reviewed REVIEWED] [--all-differences]
                                 [--per-page PER_PAGE] [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         held PDF manifest with package, source_pdf, pages and
                        optional quality_report
  --independent INDEPENDENT
                        source-bound OCR consensus JSONL
  --output OUTPUT       new local queue directory
  --limit LIMIT
  --reviewed REVIEWED   feedback JSONL to skip already reviewed exact
                        differences; repeatable
  --all-differences     include text and line-wrap differences, grouped per
                        page
  --per-page PER_PAGE   maximum differences per page when using --all-
                        differences
  --json
```

### pdf-review-feedback

summarize saved OCR disagreement review labels

```bash
pdf2data pdf-review-feedback --input <input>
```

```text
usage: pdf2data pdf-review-feedback [-h] --input INPUT [--json]

options:
  -h, --help     show this help message and exit
  --input INPUT  feedback JSONL; repeatable
  --json
```

### pdf-review-route

revalidate focused OCR feedback and queue source crop proof work

```bash
pdf2data pdf-review-route --manifest <manifest> --feedback <feedback> --packages <packages> --output <output>
```

```text
usage: pdf2data pdf-review-route [-h] --manifest MANIFEST --feedback FEEDBACK
                                 --packages PACKAGES --output OUTPUT [--json]

options:
  -h, --help           show this help message and exit
  --manifest MANIFEST
  --feedback FEEDBACK
  --packages PACKAGES
  --output OUTPUT
  --json
```

### pdf-review-proposals

attach source-crop observations as unverified review suggestions

```bash
pdf2data pdf-review-proposals --input <input> --observations <observations> --output <output>
```

```text
usage: pdf2data pdf-review-proposals [-h] --input INPUT --observations
                                     OBSERVATIONS --output OUTPUT [--json]

options:
  -h, --help            show this help message and exit
  --input INPUT         review manifest JSONL
  --observations OBSERVATIONS
                        source-bound visual observations JSONL
  --output OUTPUT       new proposal manifest JSONL
  --json
```

### pdf-review-filter

skip already reviewed exact source and OCR differences

```bash
pdf2data pdf-review-filter --input <input> --reviewed <reviewed> --output <output>
```

```text
usage: pdf2data pdf-review-filter [-h] --input INPUT --reviewed REVIEWED
                                  --output OUTPUT [--json]

options:
  -h, --help           show this help message and exit
  --input INPUT
  --reviewed REVIEWED  saved review feedback JSONL; repeatable
  --output OUTPUT      new filtered review manifest
  --json
```

### serve

open the local PDF review and embedding webpage

```bash
pdf2data serve
```

```text
usage: pdf2data serve [-h] [--port PORT] [--max-upload-mb MAX_UPLOAD_MB]
                      [--review-manifest REVIEW_MANIFEST]
                      [--review-decisions REVIEW_DECISIONS]

options:
  -h, --help            show this help message and exit
  --port PORT
  --max-upload-mb MAX_UPLOAD_MB
  --review-manifest REVIEW_MANIFEST
                        optional trusted local OCR review queue JSONL
  --review-decisions REVIEW_DECISIONS
                        local feedback JSONL (defaults beside the manifest)
```

## Real local execution outputs

These are outputs captured from this worktree, not proposed schemas. The validation snapshot may be partial while the model run is active.

### gpu-doctor.json

```json
{"tool": "PDF2Data", "platform": "macOS-27.0.1-arm64-arm-64bit", "machine": "arm64", "mlx_installed": false, "mlx_core_installed": true, "torch_installed": false, "pdf_installed": false, "rapidocr_installed": true, "docling_installed": false, "vision_ocr_available": true, "tesseract_available": false, "mlx_default_device": "Device(gpu, 0)", "mlx_metal_available": true}
```

### verify-l1.json

```json
{"integrity": "passed", "source_id": "sharing-october-em", "source_pdf_sha256": "17b7dd0fe13e4e4f1ca5d4793c7485d66bb5e1b527f9b846a44564577f4d49ff", "pages": 6, "structure": {"physical_pages": 6, "selected_characters": 15601, "regions": 6, "candidate_units": 8, "exact_children": 22, "selected_text_partition": "passed", "body_unit_partition": "passed", "exact_anchors": "passed", "legal_review": "not_run", "verified_claims": 0}, "page_text_readiness": "complete", "quality_counts": {"auto_accepted": 4, "repair_required": 2, "source_blocked": 0}, "content_ready": false, "publication_eligible": false, "legal_review": "not_run"}
```

### qwen-validation-progress.json

```json
{"command": "l1-qwen-validate", "cases": 32, "counts": {"source_difference_requires_repair": 25, "literal_agreement_requires_layout_proof": 7}, "output": "out/pdf2data-qwen-repair/validation.json", "source_quality_changed": false}
```

### qwen-reproof.json

```json
{"command": "l1-qwen-reproof", "documents": 9, "pages": 98, "newly_accepted": 0, "auto_accepted": 77, "repair_required": 21, "source_text_modified": false, "publication_eligible": false, "output": "/Users/rp/.codex/worktrees/legal-l1-build/PDF2Data/out/pdf2data-native-and-qwen-reproved"}
```

## Current offline Qwen repair run

```bash
export PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv/bin/python
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
./pdf2data l1-qwen-prepare --packages out/legal-l1-locally-validated/documents --output out/pdf2data-qwen-repair
PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv-mlx-vlm/bin/python ./pdf2data l1-qwen-run --device gpu --cases out/pdf2data-qwen-repair/cases.json --output out/pdf2data-qwen-repair/readings.json --resume --json
./pdf2data l1-qwen-validate --cases out/pdf2data-qwen-repair/cases.json --readings out/pdf2data-qwen-repair/readings.json --output out/pdf2data-qwen-repair/validation.json --json
./pdf2data l1-qwen-reproof --cases out/pdf2data-qwen-repair/cases.json --readings out/pdf2data-qwen-repair/readings.json --packages out/legal-l1-locally-validated/documents --output out/pdf2data-qwen-reproved --json
```

The preparation command was already run; use a new workspace for another preparation. Resume only applies to the same cases, model, prompt and token contract. Reproof requires complete readings and a new output directory. It rerenders the original inputs, retains source text, uses only literal-agreement readings as additional witnesses, and reruns the original quality gates. It cannot clear unproven table structure or missing source ink merely because a model agrees. Publication/legal approval remains separate.

## Output contracts for the next stage

- `legal-l1`: captured original PDF, source receipt, extraction pages/tables, raw layout/glyphs, structured exact-span candidates, quality, readiness and repair queue.
- `l1-qwen-prepare`: 32 original-page rasters and hash-bound cases for this pilot.
- `l1-qwen-run`: pinned model/runtime, input hashes, literal readings, timings and token-cap state; resumable per-page checkpoints.
- `l1-qwen-validate`: missing/incomplete readings, exact and critical-token comparisons, source differences; no promotion.
- `l1-qwen-reproof`: new immutable package versions and a receipt reporting actual accepted/repair-required counts.
- DB consumers must check source quality and separate legal/publication gates. Text extraction alone does not establish applicability, legal effect or currentness.

## External OCR benchmarks

The four `benchmark-*` commands and their reproducible examples are documented in [benchmark guide](benchmarks/README.md). They write local JSON receipts and Markdown/HTML comparisons; they do not contact Cloudflare.

## Layout-guided visual reading

`pdf-layout-detect`, `pdf-region-read` and the `benchmark-run --profile layout-vlm` profile are documented in [local layout-guided reading](benchmarks/layout-guided-reading.md). Outputs remain review candidates; the original packages are preserved.
