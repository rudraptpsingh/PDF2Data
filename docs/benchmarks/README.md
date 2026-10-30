# Reproducible PDF2Data benchmarks

The local adapter executes the **unmodified olmOCR-bench rule classes** against
PDF2Data output. It does not run the olmOCR recognition model. It compares
PDF2Data profiles and preserves every selected source in the denominator.

[Measured results and remaining gaps](2026-10-31-results.md).

## Pinned inputs

- [AllenAI olmOCR evaluator](https://github.com/allenai/olmocr), Apache-2.0,
  commit `f7cfe4c22098b154c76b6ec950d1c0a464eecf8d`.
- [olmOCR-bench dataset](https://huggingface.co/datasets/allenai/olmOCR-bench),
  revision `54a96a6fb6a2bd3b297e59869491db4d3625b711`, ODC-BY;
  original document rights remain with their owners.
- The dataset contains 1,403 source PDFs. The reported run samples ten PDFs from
  each of seven categories: **70 PDFs**, chosen by seeded filename SHA-256 before
  looking at output. All annotations for each selected PDF are retained.
- Category seed: `pdf2data-v1`. Selection never uses rule text, pass/fail or model
  output. Cache metadata proves the revision and downloaded bytes.

## Setup

Run from the PDF2Data repository. Use an installed PDF2Data Python environment
with the native/OCR dependencies. Apple Vision is available on macOS; other
backends must be installed and benchmarked separately.

```bash
export PDF2DATA_PYTHON=/path/to/pdf2data-python
python3 -m venv out/benchmarks/eval-venv
out/benchmarks/eval-venv/bin/python -m pip install -r requirements/benchmark-eval.txt
out/benchmarks/eval-venv/bin/python -m playwright install chromium
mkdir -p out/benchmarks/vendor
git clone https://github.com/allenai/olmocr out/benchmarks/vendor/olmocr
git -C out/benchmarks/vendor/olmocr checkout f7cfe4c22098b154c76b6ec950d1c0a464eecf8d
"$PDF2DATA_PYTHON" -m pip install huggingface-hub==0.36.2
HF_HUB_DISABLE_XET=1 "$PDF2DATA_PYTHON" scripts/fetch_olmocr_bench.py \
  --output out/benchmarks/olmocr-data --per-category 10
```

Downloads are serial/resumable. HTTP 429 receives at most three one-minute
backoffs before failing visibly. Use `--full` instead of `--per-category 10` to
fetch the complete source pool; omit `--per-category` in preparation for a full
suite. A download failure is not permission to silently change the sample.

## CLI workflow

Use new output directories. The scorer requires a clean pinned evaluator
checkout and starts a separate isolated equation-render cache for every score.

```bash
./pdf2data benchmark-prepare \
  --dataset-root out/benchmarks/olmocr-data/bench_data \
  --per-category 10 --output out/benchmarks/public-70 --json

./pdf2data benchmark-run --suite out/benchmarks/public-70/suite.json \
  --profile native --workers 4 --output out/benchmarks/native --json
./pdf2data benchmark-run --suite out/benchmarks/public-70/suite.json \
  --profile auto --workers 4 --output out/benchmarks/auto --json

./pdf2data benchmark-score --suite out/benchmarks/public-70/suite.json \
  --run out/benchmarks/native --upstream out/benchmarks/vendor/olmocr \
  --eval-python out/benchmarks/eval-venv/bin/python \
  --mode tables --output out/benchmarks/score-native --json
./pdf2data benchmark-score --suite out/benchmarks/public-70/suite.json \
  --run out/benchmarks/auto --upstream out/benchmarks/vendor/olmocr \
  --eval-python out/benchmarks/eval-venv/bin/python \
  --mode tables --output out/benchmarks/score-auto --json
./pdf2data benchmark-score --suite out/benchmarks/public-70/suite.json \
  --run out/benchmarks/auto --upstream out/benchmarks/vendor/olmocr \
  --eval-python out/benchmarks/eval-venv/bin/python \
  --mode text --output out/benchmarks/score-auto-text --json

./pdf2data benchmark-compare --score out/benchmarks/score-native \
  --score out/benchmarks/score-auto --score out/benchmarks/score-auto-text \
  --output out/benchmarks/comparison --json
```

`--resume` is available only for `benchmark-run` with identical source, code,
Python/dependencies, weights and configuration. It verifies prediction hashes
and reports reused errors. To retry an error after changing code, use a new run.
A text-only ablation reuses the automatic extraction and is **not a second parse**.

## Outputs and validation

| Stage | Output | Validation |
| --- | --- | --- |
| Prepare | `suite.json` | Pinned download bytes, source SHA-256, page counts, duplicate rules, path boundaries, all seven public annotation categories and source inventory |
| Extract | `packages/`, `run.json`, `text/`, `tables/` | Every invocation uses `pdf2data pdf-extract`; original/source and page-text hashes, page coverage, command/error receipts, runtime/code fingerprints |
| Score | `report.json`, `results.jsonl`, logs | Pinned unmodified upstream rules, Chromium/math-render preflight, prediction integrity, failed/missing page outcomes retained, no dropped errors |
| Compare | `comparison.json`, `.md`, `.html` | Same frozen suite, valid evaluator runs, report/run hashes and implementation identities retained |
| Downstream | Legal pipeline import tests | Unicode text, hashes and exact spans survive local database intake and retrieval |

Predictions contain only selected text from the tested page and, in table mode,
that page's additive HTML table candidates. File titles, links, fact summaries,
other pages and annotation labels never enter predictions. Table candidates
retain merged-cell spans; unresolved placement is not claimed as inline fidelity.

Raw official-rule rates and **coverage-adjusted** rates are separate. An empty
extraction of a nonblank source cannot earn adjusted credit through absence
rules. A baseline is added for every page unless the public labels already
provide one (their exceptions are retained). Macro here averages the seven
sampled categories **plus the baseline category**; it is not a leaderboard score,
character accuracy or probability of legal correctness. Reports also contain
per-category numerator/denominator counts. Renderer, annotation and prediction
integrity errors invalidate comparisons; extraction failures count as failures.

For custom source-verified engineering probes, prepare with `--custom` and a
folder containing `pdfs/` plus JSONL rules in the upstream schema. These suites
are explicitly `custom_local`, have no public dataset revision, and cannot claim
independent legal accuracy. Keep separate human legal review/currentness gates.

## Scope still open

The measured run covers 70/1,403 PDFs, one invocation per profile, no controlled
OS cache and no hardware isolation. Full-suite evaluation, OmniDocBench/TEDS,
independent legal holdouts, multilingual source review and a comparison against
alternative recognition models remain future work. No external OCR engine
performance is inferred from this adapter. No remote DB or Cloudflare action is
part of the workflow.
