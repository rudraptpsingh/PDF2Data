# PDF2Data

[Website & setup](https://rudraptpsingh.github.io/PDF2Data/) · **Release candidate:** `0.2.0-rc.12` · [Releases](https://github.com/rudraptpsingh/PDF2Data/releases) · [Release plan](docs/release-plan.md) · [Optional pattern-analysis integrations](docs/optional-analysis-tools.md)

## Quick start

With Node 20+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
npm install -g @rudraptpsingh/pdf2data@next
pdf2data --version
pdf2data doctor
pdf2data workspace --workspace ./pdf2data-workspace
```

The release candidate is published on npm under the `next` tag.
[GitHub release downloads](https://github.com/rudraptpsingh/PDF2Data/releases/tag/v0.2.0-rc.12)
remain available for direct installation.

The launcher downloads the matching Python wheel and dependencies into an isolated
uv cache on first use. OCR and AI models are optional, explicit installs. The core
supports Python 3.10+; MCP requires 3.11+. The launcher selects Python 3.12.
See [npm usage and MCP configuration](npm/README.md). Python users can install the
wheel directly from the matching GitHub release; PyPI publication is not required.

The optional MCP server exposes `pdf2data_parse`, `pdf2data_status` and
`pdf2data_read` over the same CLI pipeline. An [agent skill](skills/pdf2data/SKILL.md)
and skill ZIP accompany the release. Start with `pdf2data-mcp --help` to prepare its
runtime before adding it to a stdio-capable host.

A local CLI for source-backed PDF extraction, structured evidence packages, quality checks, review, guarded repair and passage embeddings. It runs on Apple Silicon with MLX, Windows/Linux with PyTorch CUDA, or CPU. GPU encoding is batched in one process; CPU encoding can use a bounded number of worker processes. The base package has no ML dependencies and does not download a model merely by importing it.

The output is deterministic, checksum verified, and ready for a Vectorize import step. PDF2Data preserves original sources, text and layout provenance. Extraction and model agreement do not establish legal correctness. Index publication and database ingestion remain separate stages.

[Complete CLI reference, all commands and real outputs](docs/cli-reference.md).

[White background candidate discrimination](docs/white-fill-table-candidates.md)
keeps invisible paragraph rectangles from imposing an unsupported table gate,
while preserving source evidence and ordinary independent quality checks.

Use [cached-reading admission](docs/cached-reading-admission.md) to expose a verified
legacy `pdf-extract` reading to native fields without extraction or quality promotion.

Use [configurable field rules](docs/configurable-fields.md) to add metadata for
different document categories without changing parser code:

```bash
./pdf2data fields init --output ./document-rules.json
./pdf2data fields extract --package ./l1/source-id --rules ./document-rules.json --output ./fields/source-id
```

Rules support source-anchored metadata, dates, quantities and formula candidates,
search across document bundles, reviewed-reference evaluation, readable exports
and MCP reads. Add [JSON check rules](docs/field-check-rules.md) to test counts,
values, pages and captures without writing Python tests.
Opt-in [multipart fields](docs/multipart-fields.md) retain cross-page values and
captures within a native parent, with separate page anchors and explicit seams.
Use [semantic field profiles](docs/semantic-fields.md) to classify those source
parts through a configured AskMill runner. Model-generated values are rejected;
role candidates retain source holds and require separate semantic review.

The [feedback controller](docs/feedback-controller.md) inventories originals,
measures per-page errors, stages bounded repairs and rechecks the full document.
Machine checks and source review have separate progress counts.

### Open a complete converted document

For a visual workflow with local PDF upload, persistent run history and stage tracking:

```bash
./pdf2data workspace --workspace out/document-workspace --port 8771
# Add --docx-python /path/to/python-with-python-docx for Word outputs.
```

Open `http://127.0.0.1:8771/`. Choose a PDF, enter its title and document type, and start processing. Each run invokes the CLI for extraction, integrity verification and readable export. Completed runs expose Markdown, HTML, JSON, relations and a ZIP. Interrupted runs remain visible and can be retried as fresh attempts. Files persist under the workspace directory. This is a single-user localhost workspace; it does not deploy or write to Cloudflare, ingest into the legal database, or certify legal review.

After `legal-l1`, export a readable folder without reprocessing or altering the immutable package:

```bash
./pdf2data export-readable --package out/legal/source-id --output out/readable-source --json
```

Open `out/readable-source/document.html`. It starts with the continuous document, joining complete paragraph candidates across PDF pages. Source comparison shows each original page above its selected text. The folder contains `document.md`, `data.json`, `relations.json`, `quality.json`, `pages.md`, `source.pdf` and page images. Add `--docx-python /path/to/python-with-python-docx` to also create `document.docx`.

Markdown is the human reading; JSON retains exact spans and relationships. Paragraph boundaries, table grids, roles and references remain candidates. Relationship edges describe provenance and exact containment, not verified legal applicability. This export adds a command and therefore changes the code profile of future extractions; existing captured packages remain unchanged and are verified against their captured hashes.

The streamed [table-context sidecar](docs/table-contexts.md)
with cell-local evidence, candidate headers, periods, units and continuation
references preserves table-level context for downstream readers.
The [document-parts artifact](docs/document-parts.md) also retains printed page
labels, explicit annexure/language markers and header identity conflicts.
The optional [DeepSeek-OCR accuracy pilot](docs/benchmarks/deepseek-ocr-local-pilot.md)
reports measured failures and keeps the existing reader as the default.

The Python namespace `local_vector_batch` and the `local-vector-batch` entry point remain compatible. The active local checkout is `PDF2Data`; an old-path symlink keeps existing consumers running.

## Install

Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
pip install -e '.[mlx,test]'   # Apple Silicon GPU
# or: pip install -e '.[torch,test]'  # Windows CUDA / Linux CUDA / CPU
pdf2data doctor --json
```

## Local PDF webpage

Install PDF support alongside the desired encoder, then start the loopback-only server:

```bash
pip install -e '.[pdf,mlx]'       # Mac GPU; use pdf,torch on Windows/Linux
pdf2data serve
# Open http://127.0.0.1:8765
```

Drop a PDF, review its page text, table cells and candidate dates/numbers, choose
the model/device/batch settings, then generate and download a bundle. The bundle
contains the original PDF, SHA-256, page text, table bounding boxes/cells, candidate
values, embedding input and verified vector batches. New embedding attempts keep
separate run directories. Downloads are streamed from a disk ZIP, and processing
uses the existing bounded encoder pipeline. Download before stopping the server;
its workspace is temporary. `--port` and `--max-upload-mb` configure the server.
The page selector can inspect every extracted page. Advanced encoding controls
cover pooling, overlap, token budget, CPU workers and threads; GPU encoding uses
one batched process. Invalid combinations fail before a model job starts.

PDF extraction and inference run locally. Model files may download on first use.
Cached-only mode is supported by Torch; MLX rejects that option rather than
silently allowing a registry download. No cloud model API or upload is called.

OCR runs only for pages lacking usable native text: macOS Vision when Xcode
command line tools are available, otherwise an installed Tesseract executable.
Use the extraction options to choose `auto`, `off`, `vision`, or `tesseract`.
`--ocr-device auto` lets macOS schedule Vision on its supported compute hardware;
Vision does not expose the actual accelerator used, so the receipt records
`system_managed_unreported`. `--ocr-device cpu` requests Vision CPU or runs
Tesseract on CPU; Vision records `cpu_requested_unverified`.
`--ocr-device gpu` fails rather than claiming unobservable GPU use. Native PDF
text parsing remains parallel CPU work; the GPU option for **embeddings** is
independent and reports the selected MLX/Torch device.
Vision OCR calls share one machine-wide queue across PDF workers to avoid
competing accelerator requests. Per-page OCR receipts include queue wait and
execution time, and batch receipts sum both. Native extraction still uses the
configured parallel workers.
Oversized pages are rendered under a 12-million-pixel budget; the receipt
records the actual DPI and pixel count. Downsampling never makes an OCR page
verified or removes its source-review requirement.
Tesseract language packs must be installed separately (`eng+hin`, for example).
OCR exports line boxes and confidence. Missing text, failed extraction and low
OCR confidence prevent the webpage from starting embeddings. Short nonempty
pages are retained. Scanned tables still need layout review: OCR text and a high
confidence score do not prove that rows, merged cells, formulas or numbers are
correct. Numeric matches are explicitly unverified candidates, not legal facts.
For scanned tables with visible rectangular rules, Vision or Tesseract word boxes are mapped
into printed row and column cells. The resulting `tables.json` entries retain
the source PDF and page text hashes, physical page, render DPI and PDF-point
bounds, with `needs_row_column_review` status. Broken grids, rotated pages and
ambiguous word boxes may yield no table or unassigned words; no cells are
filled from neighboring values. `report.json` counts these as
`ocr_table_candidates` without promoting any value as a verified fact.
For native-text PDFs, a second parser checks each page for missing letters and
digits. It ignores line-break and hyphen differences; a genuine content gap
blocks embedding and preserves the alternate extraction for review. This check
does not prove reading order or table relationships.
Unresolved PDF glyph IDs such as `(cid:3)` and `/g7008` are excluded from the
*comparison counters* because their digits are parser IDs, not legal numbers.
The raw page text retains them, and bilingual pages with unresolved glyphs are
sent to OCR or source review instead of being accepted as clean text.
If pdfplumber reports zero pages but pypdf can traverse the page tree, the
extractor rebuilds that tree in a temporary file. The original PDF bytes,
SHA-256, URL, and physical page positions remain the evidence identity.
An independent parser coverage mismatch now also requests OCR; corrected OCR
text remains marked for visual review, including every numeric claim.
Long native text containing undecodable CID/replacement glyphs is also flagged
for OCR. Whenever OCR is attempted, the native extraction and OCR lines remain
in the export; the page's selected method and text hash identify which text was
embedded. The macOS Vision executable is compiled once into a source/compiler
keyed local cache, then reused across PDF jobs.

The same extraction is available to agents and scripts:

```bash
pdf2data pdf-extract --input document.pdf --output extracted \
  --source-url https://example.org/document.pdf --ocr auto --json
# Inspect report.json: complete_for_embedding must be true.
pdf2data verify-package --output extracted --source-pdf document.pdf --json
pdf2data embed --input extracted/embedding-input.jsonl --output vectors \
  --backend mlx --device gpu --min-chars 1 --json
pdf2data verify --output vectors --json
pdf2data verify-package --output extracted --vectors vectors --json
```

For many **already downloaded** PDFs, provide JSONL rows with `document_id`,
`path`, `url`, and the PDF's `sha256` download receipt. An optional `title`
preserves a human-readable source label; otherwise the stable document ID is
used. The bounded local batch
command extracts each PDF in a separate process, verifies its package against
the source bytes, and writes a stable receipt under `batch-reports/`. Failed or unreadable PDFs
remain explicit in the report. It does not crawl URLs or upload to a database.

```bash
pdf2data pdf-batch --input source-receipts.jsonl --output extracted-pdfs \
  --workers 6 --limit 500 --ocr auto --ocr-device auto --json
pdf2data pdf-batch --input source-receipts.jsonl --output extracted-pdfs \
  --workers 6 --limit 500 --resume --json
```

For a mixed corpus, a native-first pass lets text PDFs become searchable while
scanned PDFs are queued explicitly for OCR. A later repair pass verifies the
existing package and source hash, retries only unreadable packages, and swaps
in a new package only if text coverage improves. The OCR pass re-reads that
PDF's native pages; it never downloads the source again and never rewrites a
healthy package.

After a parser quality rule changes, `pdf-batch --resume --refresh` rebuilds
only the manifest's selected packages from the original byte-verified PDFs and
atomically swaps each verified result. Use a small targeted manifest; this
can deliberately mark formerly accepted pages for review.

On macOS/Linux, `--document-timeout 120` returns an explicit `retry_required`
receipt for a pathological PDF rather than delaying an entire batch. Retry
that source separately with a higher timeout. Windows uses the default zero
(no per-document signal timeout).

```bash
pdf2data pdf-batch --input source-receipts.jsonl --output extracted-pdfs \
  --workers 8 --ocr off --document-timeout 120 --resume --json
pdf2data pdf-batch --input source-receipts.jsonl --output extracted-pdfs \
  --workers 6 --ocr auto --ocr-device auto --resume --repair-ocr --json
```

The batch receipt separates pages ready for text retrieval, blank pages, and
pages requiring OCR/source review. A PDF can be complete for embedding while
its tables or OCR still need review before using numbers as legal facts.

`complete_for_embedding` confirms that searchable text was extracted without a
known coverage failure. It does not certify OCR diagrams, tables, or legal
facts. `requires_source_review` is true when such material needs a visual check;
the Cloudflare importer must keep those pages and candidate facts unpublished
as verified evidence until review is recorded.

Every page has a stable ID derived from the original PDF hash. `page` is the
1-based physical PDF page (the number used by a PDF viewer's `#page=` link),
which can differ from a page number printed in the document. Vector metadata
includes `parent_document_id`, `page`, original PDF SHA and a supplied source URL.
The vector's character offsets refer to that page's exported text. Page IDs and
model configuration must stay aligned with the search application's query encoder.

The extraction bundle also contains `structure-candidates.jsonl`. It proposes
issuer mastheads, contacts, addressees, clause headings, condition cues, and
table cells. Each resolved cue carries a page text hash and exact codepoint
span. Operators, conjunctions, subjects and time scopes are literal source
phrases, not interpreted formulas or legal obligations. Table cells retain
row/column and page geometry; cells with no unique span in selected text are
marked `ambiguous_or_not_in_selected_text`. A first table row is only a
*candidate* header, and blank/merged cells are never filled down. All records
remain `review_required`; OCR addresses and complex table relations need
visual source review before structured use or citation.

`evidence-package.json` is a portable receipt for these existing files. It
stores their SHA-256 hashes, the original PDF hash and URL, page-level unit
identity, candidate-only fact and relation status, and the embedding-input
linkage. `verify-package` checks file hashes, page/quote offsets and vector
anchors against their owning page. The manifest deliberately leaves instrument
version, clause hierarchy, table relationships and amendments unresolved;
passing validation does not certify factual or legal accuracy. Keep the
original PDF separately to recheck its hash and visually review complex pages.
Each extraction also writes `presentation/index.html`, per-page Markdown/HTML,
and `presentation/layout.jsonl`. The layout preserves text and table candidate
blocks under physical PDF page IDs and hashes; every page links to the original
PDF page. Table grids remain labelled as candidates until their cells are
reviewed. For an older package, `pdf2data render-package --output
extracted --json` generates these views from its verified artifacts without
reparsing the PDF or changing the embedding input. The presentation manifest
and rendered files are hash checked by `verify-package`.
An optional PP-StructureV2/V3 run can be attached with `import-paddle-layout
--package extracted --source-pdf document.pdf --manifest paddle/manifest.json`.
It records ordered layout blocks and bounding boxes alongside the selected
text, produces a separate candidate HTML page, and checks the source PDF and
page hashes. Paddle output remains review-only: a layout model can miss or
merge numeric table cells, so the importer never replaces selected text,
verified facts, or vectors automatically.
`verify-package --json` reports candidate counts by kind and status for a
small dashboard without treating these observations as verified facts.
It also reports ordered physical page coverage, held page numbers and text
readiness separately from integrity. A retrieval import can use
`verify-package --output extracted --source-pdf document.pdf --require-ready`
to fail when any physical page text is still held. Structured candidates
remain unverified even when text readiness passes.

### Independent OCR audit

Install `local-vector-batch[pdf,ocr-consensus]`, then provide a JSONL manifest
with `package` and `source_pdf` paths. `ocr-consensus --input manifest.jsonl
--output audit.jsonl --limit 50 --json` compares existing Vision OCR pages
against local RapidOCR. It requires near-identical text, the exact ordered
numeric tokens, high confidence from both engines, and no table or layout
warning. The audit records hashes, provenance, measurements, and reasons for
review. It never changes source packages or promotes OCR to verified evidence.
It supports bounded parallel workers and resume:

```bash
pdf2data ocr-consensus --input manifest.jsonl --output audit.jsonl \
  --limit 5000 --workers 6 --dpi 180 --resume --json
```

Each process loads one reader and uses one inference thread. PDFium keeps the
original PDF open while rendering its selected physical pages. A companion
`audit.jsonl.progress.json` records completed pages, newly processed pages per
second, and resumed pages. Resume accepts only matching source PDF, selected
page text, OCR version and DPI receipts; it does not mark an agreement as a
source review.

### Layout-aware comparison with Docling

Install `local-vector-batch[pdf-layout]` in a separate environment to keep the
larger layout models out of the normal embedding runtime. Then compare a
difficult page with the existing immutable extraction package:

```bash
pdf2data pdf-compare --package extracted/document \
  --source-pdf document.pdf --output comparisons/document-page3 \
  --pages 3 --language iso:hi --device mps --json
```

The result includes exact table cells and boxes, all OCR text, Markdown,
ordered numeric differences, timings, and the original source/page hashes.
All candidate results remain `review_required`. The Markdown export can hide
text placed under a picture by the layout model, so the comparison also
preserves every text element separately. `mps` requests Apple GPU acceleration
for supported models; Docling's TableFormer may use CPU. Use an empty output
directory for each comparison to preserve previous receipts.

For direct OCR extraction, `--ocr-language eng+hin` passes Hindi and English
hints to Apple Vision. `--ocr rapidocr` explicitly selects the optional local
RapidOCR backend. In `auto`, a visible page with zero Vision lines can fall back
to RapidOCR; the receipt preserves the empty primary result and the alternate
text and still requires source review. Manual source inspection remains
necessary for legal claims and tables.

Repair a known physical page while preserving all other page artifacts:

```bash
pdf2data pdf-repair --input document.pdf --package extracted/document \
  --pages 234 --ocr rapidocr --json
```

The tool verifies the original source hash, reuses untargeted page text,
embeddings inputs and structured candidates, and verifies those rows are
unchanged before atomically replacing the package. Repaired OCR remains held
for source review. This avoids rerunning hundreds of pages for a single OCR
failure.

### Reviewed structure export

After checking the **original PDF visually**, a reviewer can supply a separate
`review-decisions.json` and run:

```bash
pdf2data review-package --output extracted \
  --decisions review-decisions.json --source-pdf document.pdf --json
```

This writes `reviewed-evidence.json` with only selected clause or table-row
units. Each unit has an exact page quote and codepoint span, page text hash,
original PDF hash, reviewer receipt, heading path, conditions, and literal fact
values. Table units additionally retain the table bounding box, selected row,
nonblank cells, and their matching header cells; the tool rejects inherited
blank cells. `parent_text` combines the reviewed caption, headers, and row for
search while `exact_quote` remains a literal slice of page text. Review decisions
must explicitly set display rights (`cite_only` or `full`). The export records
whether the *whole* PDF extraction is complete. A reviewed unit does not by
itself establish a current legal version or certify the reviewer's interpretation.
Consumers should independently check hashes and never promote candidate files.

For CUDA, install the appropriate official PyTorch build for your CUDA version first, then install `.[torch]`. The selected Torch build determines CUDA availability. On a Mac, the Torch backend also supports MPS, but MLX is the default when installed. The default model is `BAAI/bge-base-en-v1.5` with CLS pooling and 768 dimensions. MLX currently supports its `bge-base` registry mapping; use Torch for arbitrary Hugging Face encoder models, mean pooling, or a pinned model revision. Ensure your retrieval query encoder uses the same model, pooling, and normalization.

## Input and output

Input is one JSON object per line. `id` and `text` are required. `metadata` is optional and may contain scalar string, number, or boolean values. Keep metadata small for your target vector database.

```json
{"id":"reg-2024-1","text":"The actual extracted document text...","metadata":{"title":"Regulation title","url":"https://example.org/reg.pdf","authority":"CERC"}}
```

Run a small chunk first:

```bash
pdf2data embed --input examples/documents.jsonl --output /tmp/vector-demo --backend mlx --device gpu --batch-size 8 --chunk-chars 1500 --overlap 150 --json
pdf2data verify --output /tmp/vector-demo --json
```

If the application already stores document text in numbered chunks (for example, D1 `docket_text_chunks`), use `--input-mode chunks`. Each row must carry its own nonnegative `chunk_index`; repeat the document `id` across its chunks. The output's `char_start` and `char_length` then refer to that chunk's exact original text, and `chunk_index` is included in both metadata and the vector ID. Do not pre-trim the input text unless the stored chunk has been changed in the same way.

```json
{"id":"reg-2024-1","chunk_index":0,"text":"The exact stored text of chunk zero...","metadata":{"shard":"corpus","source_family":"cerc"}}
{"id":"reg-2024-1","chunk_index":1,"text":"The exact stored text of chunk one...","metadata":{"shard":"corpus","source_family":"cerc"}}
```

```bash
pdf2data embed --input /tmp/chunks.jsonl --input-mode chunks --output /tmp/chunk-vectors --backend mlx --device gpu --chunk-chars 1500 --overlap 300 --json
pdf2data verify --output /tmp/chunk-vectors --json
```

The document and chunk modes use different ID namespaces; changing mode, model, or window settings requires a coordinated reindex. Cosine indexes can compare normalized output with unnormalized vectors from the same encoder, but dot-product indexes cannot. Check a small set of passage and query vectors against the existing index before bulk insertion.

For a caller that already selected tokenizer-safe windows, `--input-mode
passages` preserves each exact slice and its absolute codepoint offset inside
the owning chunk. It requires `chunk_index` and `char_start` on each input row;
it never trims or rechunks that text. This mode has a separate stable ID
namespace and is useful when adding missing windows to an existing index.

```json
{"id":"reg-2024-1","chunk_index":2,"char_start":2400,"text":"The exact selected slice, including its whitespace...","metadata":{"shard":"corpus"}}
```

```bash
pdf2data embed --input selected-passages.jsonl --input-mode passages \
  --output passage-vectors --backend mlx --device gpu --json
```

Use `--backend torch --device cpu --workers 4 --threads-per-worker 1` for bounded CPU parallelism. One batched encoder process is used for GPU modes to avoid competing GPU contexts. Tune batch size to GPU memory; reduce it if the model runs out of memory. `--offline` is available with Torch when the model is already cached. The MLX backend uses its local registry/cache behavior.

Output contains `manifest.json` and numbered `batch-*.ndjson` files. Each Vectorize row is:

```json
{"id":"stable-sha256-prefix","values":[0.01,0.02],"metadata":{"document_id":"reg-2024-1","char_start":0,"char_length":1500,"offset_encoding":"codepoint","url":"https://example.org/reg.pdf"}}
```

Use `--format generic` for rows with an `embedding` field instead of `values`. Character offsets are Unicode codepoints in the original input text; the source application can show the passage by slicing that original text at the stored offsets. Keep the original text and its URL separately. Do not treat metadata URLs or the synthetic example files as verified legal citations.

## Resume and provenance

If a run stops, execute the same command with `--resume`. The CLI checks the input SHA-256, model and processing options, every existing batch checksum, vector dimensions, and duplicate IDs. It skips completed batches and writes new batches atomically. It will refuse a changed input or settings; use a new output directory for a new corpus or model. An empty output directory is required for a fresh run.

The manifest includes input hash, model, backend, pooling, device, normalization, dimensions, batch checksums, and completion state. Running `verify` does not need a model or accelerator. An agent can call the CLI with `--json` and parse one JSON object on stdout. Errors are JSON on stderr and exit code 2.

`complete` means the run finished. `input_coverage_complete` separately reports
whether every input record produced a passage; `input_audit` lists records
omitted by a minimum-length setting. Built-in encoders check every untruncated
token sequence before inference and refuse an overflow instead of silently
cutting off text. The manifest records the effective tokenizer model, revision
when available, special-token-inclusive limit, and `token_budget_validated`.
Old runs and custom encoders without that receipt are not retroactively marked
validated. Character windows cannot guarantee a token budget, especially for
numeric tables. CLI default `--overflow-policy error` preserves existing IDs
and settings. Use `--overflow-policy split --token-overlap 64` to subdivide only
oversized windows using the encoder's real tokenizer offsets. The PDF webpage
uses this policy automatically. Every child keeps its exact source slice,
absolute codepoint offsets, page/chunk parent, and stable split ID. Safe windows
and already-safe `passages` retain their original IDs. Overlap is bounded to a
quarter of the content token budget; slices are re-tokenized and checked before
inference, and uncovered non-whitespace characters cause an error.

Split manifests record the tokenizer definition SHA-256, effective limit,
resolved revision when available, split algorithm version and overlap, plus
source/overflow/output window counts. Resume rejects changed splitter receipts.
This token coverage receipt does not establish semantic or extraction quality.
The webpage requires both input and token coverage receipts before calling an
embedding result complete.

## Candidate evidence checks

Check typed extraction/model candidates against the original selected page text
without calling a model or promoting facts:

```bash
pdf2data validate-evidence --pages extracted/pages.jsonl --candidates typed-candidates.jsonl --json
```

A candidate has `kind` (`number`, `date`, or `contact`) and `evidence`, an array
of `{ "page": 1, "quote": "Capacity 100 MW" }`. Numerical candidates also have
`value_text` and `unit`; dates have `value_text` and `date_role`; contacts have
`contact_role` and an `evidence_basis` when claiming issuer identity. Evidence
can supply `char_start`, `char_length`, and `page_text_sha256`. Offsets are
codepoints. Repeated quotes require offsets. Whitespace normalization must be
explicit (`normalization: "whitespace"`); paraphrases never count as quotes.

The report checks page hashes, contiguous quotes, exact supplied spans, units
adjacent to numerical values and role enums. Table-cell evidence needs row /
column / header review; separate unit cells do not establish association. Date
roles, legal applicability and institutional identity remain subject to review.
Every result retains `verified: false`, including candidates whose evidence
checks pass. See [the evidence contract](docs/ingestion-contract.md).

## Automated extraction quality gate

Quality checks are separate from source-visual reviews. Reuse existing independent
OCR receipts instead of running OCR again:

```bash
pdf2data pdf-quality \
  --package parsed/document \
  --source-pdf originals/document.pdf \
  --independent ocr-consensus.jsonl \
  --output quality/document.json \
  --require-accepted --json
```

The report checks source and text hashes, language support, independent word order,
numbers, logical operators, source coverage, and layout separately. A high word
score cannot override a failed number or table check. `--require-accepted` writes
the report and exits **3** when pages remain held; input errors exit **2**. It does
not upload or mark pages as human reviewed. Unresolved visual regions remain
explicit; only complete, all-pass machine decisions qualify for publication.

Use `--policy custom-policy.json` to inspect/version threshold changes. Add
`--references source-reviewed-labels.jsonl` to measure false accepts and false
holds. References bind to the original extraction's PDF/page/text hashes;
calibration and holdout sets cannot share a PDF. These measurements describe
labelled page acceptance, not corpus-wide OCR or legal-answer accuracy.

For a whole queue, use one manifest row per source PDF with `package`,
`source_pdf`, and optional `pages` (physical page numbers):

```bash
pdf2data pdf-quality-batch --input review-queue.jsonl \
  --independent ocr-consensus.jsonl --output quality --workers 12 --json
```

This parallel pass reuses cached OCR. It verifies the PDF and extraction hashes,
writes one atomic receipt per PDF, and updates `summary.json` as workers finish.
The summary includes separate repair routes; a missing text extraction is never
treated as proof of a blank page. Numeric/table and language issues have different
repair actions. Tune worker count to measured throughput and available memory.

### Resumable page repair queue

Use the existing source PDF, extracted package, and quality report to attempt
only held physical pages. A manifest row contains `package`, `source_pdf`,
`quality_report`, and `pages`; optionally add `review_sidecar`,
`native_receipts`, `column_order_receipts`, or `independent_receipts` as
page-number-to-file maps.
`region_receipts` accepts one receipt path or a list of receipt paths for a
physical page. Multiple patches are checked against the same original source
PDF and page text, rejected if any patch overlaps an existing or proposed
line, and assembled once before the whole-page quality gate. A partial patch
cannot make a page ready while any other source ink remains unresolved.
For source ink outside recorded OCR line boxes, `region_receipts` maps a page
to a source-bound crop receipt. `region_ocr.propose_region_repairs` can produce
these locally with the English PP-OCRv5 recognizer and a pinned local MLX
vision model; it loads the model once per process. Both readers must agree on
the missing token, and the whole repaired page must still pass every quality
component. Short structural labels with a lower PP-OCRv5 score additionally
require a source-bound macOS Vision subcrop read; two readers must agree on the
exact literal case. The separate `region_ocr_policy.json` records these
thresholds without changing the whole-page quality policy. A per-call region
budget defers remaining regions to a source/page/text/model-bound cursor in
the output directory. Rerun the same call until `deferred_regions` is zero,
then pass all candidate receipts for that page together to the repair queue.
Stamps, signatures, ambiguous layouts, and unsupported scripts
remain held as visual evidence. This path never substitutes a model guess for
the source PDF.

```bash
pdf2data pdf-repair-queue --input held-pages.jsonl \
  --output repaired-staging --workers 4 --limit 20 --json
pdf2data pdf-repair-status --output repaired-staging --json
```

Each assessed attempt retains `candidates/<job-key>/quality.json`, including
held candidates and their failed components. The queue result reports actual
`elapsed_seconds`, `jobs_run`, `new_ready_pages`, and
`attempted_pages_per_second`; a cache-only resume has zero attempts and zero
attempt rate. An exception before assessment has no quality receipt and keeps
its error reason in the SQLite job ledger.

To release ready pages, the project adapter can use the compare-and-swap
commit. It rechecks the original PDF and current page versions, merges only
accepted physical pages, and leaves remote publication to the adapter:

```bash
pdf2data pdf-repair-commit --package extracted/PDF_SHA \
  --source-pdf source.pdf --results repaired-staging/results.json \
  --independent source-bound-ocr.jsonl --quality-output quality/PDF_SHA.json \
  --review-sidecar reviews/PDF_SHA.jsonl --json
```

The `--results` file is the JSON output of `pdf-repair-queue`. Commit requires
an independent source-bound OCR receipt for each accepted page and rejects a
new human review decision or stale page hash. Query indexing happens after the
project publisher verifies the committed package.

For a bounded higher-resolution reread with an already cached, separate
recognizer, pass `--config repair.json` containing `{"ocr_engine":"vision",
"ocr_dpi":300,"independent_cache":"/absolute/path/to/ocr-consensus.jsonl"}`.
The render budget may reduce the actual DPI on oversized pages; the page
receipt records what ran. The cache is matched by original PDF SHA, physical
page, and selected text SHA. Results distinguish `attempt_counts` from the
latest `current_page_counts`, so retries do not inflate page coverage.

Jobs are keyed by original PDF, page, text, policy, route, and configuration,
then recorded in SQLite. Re-running the same queue skips finished attempts.
Candidate packages stay in `repaired-staging/candidates`; original packages and
any remote index stay unchanged. `ready_for_publication` requires source
verification and all eight extraction checks. A project-specific publisher
must separately verify and import that candidate.
Never replace an entire watched PDF package with a one-page staged candidate:
other accepted page corrections may have arrived since the candidate was made.
The publisher must compare the original page text SHA and merge only that
page's evidence before rebuilding the package and vectors.

The native and column-order routes require existing source-bound consensus
receipts; the latter preserves every original OCR line. The OCR
route uses the correct local English or Devanagari RapidOCR model and compares
against a distinct reader. For an old Vision page that might itself be wrong,
provide a third-reader receipt through `independent_receipts`. It must bind the
original PDF SHA, physical page, original text SHA, model and revision, text
SHA, full-page rendered PNG SHA and DPI; the tool re-renders the source PDF to
verify the image. Without that proof, disagreements return `awaiting_evidence`.
Table relationships, uncertain layouts, and possible blank pages also return
`awaiting_evidence` until source-bound evidence is supplied. No rule treats a
short OCR agreement as proof of a legal number or table cell.
Selected RapidOCR pages use a verification-only route with a different
source-bound reader; the dispatcher never validates RapidOCR against itself.

## Programmatic use

### Indian official document formats

Global hints in `src/local_vector_batch/document_profiles.json` are consumed by
the normal structural extractor, including letter addressee boundaries. They
recognize letter/reference aliases, Gazette/regulation headings, order events,
tender fields and amendment notices. Every proposed field keeps its literal
page span and configuration hash. Hearing, decision and commencement dates stay
separate; `as per NIT` and `[Insert ...]` remain unresolved rather than values.
These are review-only candidates, not exclusive routing rules or verified legal
facts. Restart workers after editing aliases. Existing packages are not silently
rewritten. See [actual corpus format notes](docs/actual-format-notes.md).
The same global configuration preserves literal monetary scale and denominator
spans (`INR 10 lakh per MW per Project`) without converting or calculating them.
Its `ocr_date_review` setting flags possible letter/digit confusion in numeric
day, month and year positions, including dates with named months. Each flag
keeps the literal page and character spans and is only a source-review cue;
the tool never rewrites a date or treats a plausible digit as verified. The
mapping and enabled state can be changed in the global configuration. Reparse
or repair a page to generate new cues; existing packages are not modified.

### Python API

For obscured names or text, `local_vector_batch.corroboration` can suggest related
source readings for manual review. `load_source_page(package, original_pdf,
page_number)` checks the package and original PDF hashes. Supply the resulting
pages and their exact evidence spans to `suggest_related_readings`, along
with a category and a literal shared alphanumeric document or matter reference.
The target context and reference may be on separate pages of the same PDF, as
may the related reading and reference. The result retains each source and physical
page, including repeated names in separate documents; it never changes the
source text or marks a person, relationship, or answer as verified. A category
or a matching name alone does not establish a link. The returned candidates
must be checked against both original pages and the person's role.

```python
from pathlib import Path
from local_vector_batch import Config, run, verify

manifest = run(Config(Path("documents.jsonl"), Path("vectors"), backend="torch", device="cpu"))
print(verify(Path("vectors")))
```

`run(config, encoder_factory=...)` accepts an in-process custom encoder for an agent or test. Its `encode(list[str])` method returns one float vector per input string, and it exposes a `device` string. The custom hook requires `workers=1`.

## Cloudflare Vectorize handoff

The default NDJSON has Vectorize-compatible `id`, `values`, and `metadata` fields. Create an index with the manifest's `dimensions` and cosine distance, then use the current Cloudflare Vectorize bulk import mechanism or SDK to upload batches. Upload only after `verify` reports `complete: true`. The CLI deliberately keeps upload separate so API credentials and deployment choices never enter the local embedding process. Vectorize metadata/index constraints vary by account and should be checked before importing large corpora.

## Scope

- Fixed codepoint windows keep offsets reproducible. PDF input preserves page parents; hierarchical legal clause parsing and reviewed amendment relationships are not yet general features.
- Text must fit the effective tokenizer limit (`--max-tokens`, default 512, bounded by the model). Both encoders reject overflow at inference; optional tokenizer splitting resolves it beforehand without silent truncation. MLX registry revisions cannot be pinned through this backend, so use Torch with `--revision` when an exact model revision is required.
- This tool does not perform retrieval, ranking, answer-citation validation, or deduplication across documents. Its quality evaluation covers extraction decisions; a downstream app must verify passage relevance and source authority.
- Do not include private text unless the selected model and local cache policies permit it. No cloud embedding API is called by this tool.

## Development

```bash
pip install -e '.[test]'
pytest -q
```

Backend references: [Apple MLX](https://github.com/ml-explore/mlx), [PyTorch CUDA/MPS install](https://pytorch.org/get-started/locally/), [MLX embedding models](https://pypi.org/project/mlx-embedding-models/).

MIT licensed. Contributions welcome after the initial public release.

### Review an OCR disagreement locally

`serve` can also open a source-page review queue, while retaining the PDF upload
and OCR workflow. Prepare a trusted local JSONL manifest with one object per
page: `source_pdf` (path), `source_pdf_sha256`, physical `page` (one-based),
`title`, `prompt` (the specific difference), `selected_text` (UTF-8 file path),
`selected_text_sha256`, `independent_json` (JSON file with `text` and optional
`engine`), and `independent_text_sha256` (SHA-256 of the UTF-8 text value).
Then run:

```bash
pdf2data serve --port 8765 --review-manifest /path/to/review.jsonl
```

Open `http://127.0.0.1:8765/review`. The page image is rendered directly from
the checked PDF; changed OCR lines are highlighted. A decision addresses the
specific prompt, not the whole page. Use Alt + left/right outside input fields
to navigate and Ctrl/Command + Enter to save. Suggestions are unverified;
choosing a different reading requires a reason. Feedback is appended beside the manifest
as `ocr-review-feedback.jsonl`, or to `--review-decisions PATH` if specified.
It is bound to source, page, both OCR text hashes and the prompt hash. A saved
review does **not** promote a page, change a PDF package, or publish an index;
existing source-review and quality gates remain responsible for those steps.
The manifest and paths are local operator inputs; do not use untrusted queues.

For an existing held-page batch with independent OCR receipts, create a new
queue without manually writing review rows:

```bash
pdf2data pdf-review-queue --input held-pages.jsonl \
  --independent ocr-consensus.jsonl --output review-queue --limit 100 --json
pdf2data serve --port 8765 --review-manifest review-queue/review-manifest.jsonl
```

The input rows are the same `package`, `source_pdf`, `pages`, and optional
`quality_report` records used by `pdf-repair-queue`. The generator checks the
current PDF/page/text identities and independent OCR receipt, skips stale or
ambiguous matches, and reports skip counts. It never overwrites an existing
queue. If a reviewer needs a specific visual detail, an operator may add an
optional `focus` object to a queue row: `boxes` are up to three normalized
`[left, top, right, bottom]` coordinates in the original physical page, and
`selected_excerpt`/`independent_excerpt` must occur literally in the respective
OCR texts. The view validates these values, renders the crop from the original
PDF, and binds feedback to the exact focus definition. Without focus metadata,
reviewers can zoom the full page and compare highlighted OCR tokens.

The queue generator proposes one high-value difference per page, prioritizing
dates, possible footnote markers, numbers, legal operators, and names or
identifiers. These are suggestions to inspect, not automatically classified
facts. Where the selected OCR has a unique line with normalized page geometry,
the review shows an original-page crop; otherwise use full-page zoom. Pass
`--reviewed PATH` (repeatable) to skip only the same source, page, OCR text,
question and focus hashes. A new difference on the same page remains eligible.
Use `pdf2data pdf-review-feedback --input feedback.jsonl --json`
with repeatable `--input` to summarize review labels. These labels do not approve
pages or measure overall OCR accuracy. Hindi clause number line wraps can be
tidied in the comparison view; uncheck the display toggle to see raw OCR. The
stored text and all source hashes remain unchanged in either view.

Review notes mentioning Roman clause markers or mathematical spacing are used
only to prioritize separate source questions for the same exact page and OCR
versions. The equation whitespace question is offered only when all non-space
characters, including operators, already agree. `pdf-review-feedback` reports
these note patterns separately from typed review labels. A reviewer must still
check the original image; neither a note nor a label changes OCR output.

For a per-page diff review, add `--all-differences --per-page 64` to the queue
command. This includes text and line-wrap changes as well as numeric and legal
signals. The review lists the queued differences for the current physical page;
if a limit omits detected items, the page displays that count. A decision is
scoped to one difference and never approves the page.

An operator can attach a separately recorded source-crop observation to every
item in a small review batch using `pdf-review-proposals --input QUEUE.jsonl
--observations OBSERVATIONS.jsonl --output NEW_QUEUE.jsonl --json`. Each
observation must carry the exact source PDF, physical page, two OCR, prompt,
focus and rendered-crop hashes, plus a scoped choice and explanation. The
command re-renders the crop from the original PDF and rejects mismatches. The
interface marks the resulting suggestion as unverified; it checks the crop again
when saving. A different choice requires a reason. Suggestions and decisions
are feedback only and do not modify source text, release a page, or publish an
index. Where no source-backed observation is available, no choice is suggested.

Before opening another batch, use `pdf-review-filter --input NEXT.jsonl
--reviewed FEEDBACK.jsonl --output FILTERED.jsonl --json` (repeat `--reviewed`
for multiple feedback files). It skips an exact source PDF, physical page,
two OCR text hashes, and focused-difference hash that a reviewer already
handled; other differences on the same page remain eligible. Footnote marker
feedback covers the marker only: preserve and review the associated full note
separately.

### Benchmark extraction

Use the source-hashed, pinned olmOCR-bench adapter to compare native extraction and automatic OCR with text-only and table-preserving outputs. See the [benchmark guide](docs/benchmarks/README.md) and [measured results](docs/benchmarks/2026-10-31-results.md). Benchmark scores measure sampled output properties, not legal correctness.

The [local layout-guided reader](docs/benchmarks/layout-guided-reading.md) adds opt-in GPU text/table/formula reading and source-table reconciliation. On the frozen 70-PDF sample it raises the adjusted benchmark macro from 21.53% to 71.75%; [results and limitations](docs/benchmarks/2026-10-31-layout-results.md) include the substantial processing-time tradeoff and remaining review requirements.

[Source-bound failed-crop recovery](docs/benchmarks/2026-10-31-recognition-recovery.md)
adds whitespace/gutter retries and an optional guarded local text fallback. The
same sample reaches 71.93%, with one additional baseline pass and no content-rule
gains. Original source maps, raw attempts and unresolved table fragments remain
available for review.

Cell-local configured extraction is available through `pdf2data cell-fields`.
See [configured cell fields](docs/configured-cell-fields.md) for explicit columns,
source-cell citations and independent bundle re-derivation.
