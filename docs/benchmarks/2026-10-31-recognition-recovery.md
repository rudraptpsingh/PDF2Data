# Recognition recovery: changes and evaluation

31 October 2026. Local feature branch `codex/pdf2data-benchmarks`.

## Result

On the same frozen **70 PDFs / 499 outcomes**, the coverage-adjusted category
macro changes from **71.75% to 71.93%**, a **0.18 percentage-point** increase.
There is one additional baseline pass and **no additional content-rule passes**.
This is a small recovery improvement, not a breakthrough in transcription accuracy.
The four documents with hard reading failures fall to two; those two documents'
ten outcomes remain failures in the denominator. No previous passing outcome
regresses. This is a development subset, not the full 1,403-PDF benchmark or a
leaderboard result. The source images were inspected during development.

| Measure | Previous | Guarded recovery |
| --- | ---: | ---: |
| Category macro, coverage adjusted | 71.7481% | 71.9267% |
| Passing outcomes | 341/499 | 342/499 |
| Baseline checks | 60/70 | 61/70 |
| Content rules | 281/429 | 281/429 |
| Documents with hard reading failures | 4 | 2 |
| Rule outcomes blocked by extraction failure | 25 | 10 |

Removing a generation loop does not make its replacement correct. The two
handwritten documents now finish as candidates, but their readings still contain
obvious errors and do not earn content-rule gains. All exports remain unverified.

## Implementation

- `pdf-layout-recover` partitions failed source regions at measured whitespace.
  Column gutters are tested separately from row bands so sloping adjacent columns
  do not appear to be one solid block. Short failed crops can split at a safe gap.
- Every fragment is bound to its original PDF, physical page, source rectangle
  and crop hash. Original pixels are re-rendered and compared by the reader. The
  parent region remains in the recovery receipt. Core rectangles cover the parent
  exactly; context padding can overlap. Partitioning does not synthesize characters;
  recognition remains fallible.
- Recovery uses no benchmark answers, expected text or document-specific IDs.
  Limits reject overly fragmented regions rather than cutting arbitrary strips.
- Table slices retain `table_fragment_of` and `fragment_unstitched` in structured
  blocks/search projections. They are not a reconstructed full table. Malformed
  headers/cells still fail strict table parsing; text retries cannot replace grids.
- `pdf-region-read --text-fallback vision` optionally retries failed **text** using
  the existing local macOS Vision recognizer. It retains the failed Paddle response,
  retry lines, confidence, coordinates, source-ink diagnostics and engine identity.
  Low-confidence/invalid lines reject the entire retry rather than disappearing.
- The retry also checks visible ink outside recognized boxes. The conservative
  allowance is max(32 pixels, 1% of source dark pixels), with two-pixel box padding.
  Rules, leaders or scan noise can cause false rejections. This is an uncalibrated
  coverage warning, not semantic verification or a universal accuracy threshold.
- Cached Paddle readings require the same source/task/model/runtime/device
  contract. Vision candidates additionally require the same OS, executable,
  wrapper and confidence policy. `benchmark-run --reuse-layout` makes reuse an
  explicit, hash-recorded CLI option; source batches are checked before reading.

## Real failure discovered by the retry

`old_scans/67.pdf`, region `r7`, contains two columns of country/count entries.
The full-width profile has no useful row gaps because the text slopes. Gutter
recovery separates the columns without altering pixels. The right column reads
entries such as `Sweden.....5`, `Canada.....59` and `Contrabands.....1,546`.
The left-column Paddle reading still loops on counts.

An initial confidence-only Vision retry reported confidence 1.0 for its lines but
omitted several visible counts and placed some counts before their labels. That
experimental run is **not promoted**. The guarded retry reports:

```json
{
  "status": "reading_failed",
  "fallback_attempt": {
    "accepted": false,
    "failure_reason": "fallback_uncovered_source_ink",
    "source_coverage": {
      "source_dark_pixels": 32572,
      "uncovered_dark_pixels": 15115,
      "max_uncovered_pixels": 325,
      "within_limit": false,
      "semantic_coverage_verified": false
    }
  }
}
```

Some uncovered pixels are dotted leaders; the diagnostic does not label every
pixel as missing text. Direct source inspection independently confirmed omitted
numbers. Both raw readings remain available. The page stays incomplete.

The other remaining failed PDF is the wide ASCII-style census table in
`tables/ff19f6fe9ce187ea79f84df52004534e6ff7_pg4.pdf`. Eleven source slices recover
several candidate rows, but two header slices fail and the column/header relation
is not established. The whole document remains an extraction failure for scoring.

## Reproduce through the CLI

Use the documented isolated Python environments. Replace paths as appropriate.

```bash
PDF2DATA_PYTHON=/path/to/base-python ./pdf2data pdf-layout-recover \
  --layout out/benchmarks/public-70-layout-vlm-v2/layout/layout.json \
  --read out/benchmarks/public-70-layout-vlm-v2/read \
  --output out/new-recovery/layout --json

PDF2DATA_PYTHON=/path/to/base-python ./pdf2data benchmark-run \
  --suite out/benchmarks/public-70/suite.json --profile layout-vlm --workers 1 \
  --reuse-layout out/new-recovery/layout/layout.json \
  --reuse-readings out/benchmarks/public-70-layout-vlm-v2/read \
  --text-fallback vision --parser-python /path/to/mlx-vlm-python \
  --layout-python /path/to/docling-python --output out/new-recovery/run --json

PDF2DATA_PYTHON=/path/to/base-python ./pdf2data benchmark-reconcile-tables \
  --suite out/benchmarks/public-70/suite.json --run out/new-recovery/run \
  --native-run out/benchmarks/public-70-native-release \
  --output out/new-recovery/source-tables --json

PDF2DATA_PYTHON=/path/to/base-python ./pdf2data benchmark-score \
  --suite out/benchmarks/public-70/suite.json --run out/new-recovery/source-tables \
  --upstream out/benchmarks/vendor/olmocr \
  --eval-python out/benchmarks/eval-venv/bin/python --mode tables \
  --output out/new-recovery/score --json
```

Use fresh outputs. The measured release reused successful readings from successive
experimental runs; the exact command/config receipts identify those parents.
Incremental runtime is not a fresh 70-document throughput measurement. Paddle ran
on `Device(gpu, 0)`; Vision was system-managed, with actual accelerator unspecified.
No Cloudflare or remote DB was changed. No replacement model was downloaded.

## Validation and review

- **467 parser tests pass**, one optional `docling_core` test skips in the base
  environment. The separate detector environment has Docling installed.
- **129 legal consumer tests pass**. Two legal exports were rebuilt from cached
  source-bound readings: eight pages, all 36 illustration cells, original raw text
  and candidate relationships remain unchanged. This does not establish new legal
  accuracy or improve the previously rejected local-model answers.
- The official evaluator checkout/revision, source hashes and all 499 outcomes
  stay fixed. Failed documents are retained; output hashes are checked before score.
- Source maps are generated from saved outputs. No browser visual QA is claimed.
- BuildView MCP is unavailable in this session; progress is recorded in these
  checked-in receipts rather than reported as a live BuildView update.

Review [the 70 original PDFs and mapped outputs](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/benchmarks/recovery-release-review/index.html),
[the final score](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/benchmarks/scores/recovery-guarded-release/report.json),
and [the comparison](receipts/recognition-recovery/comparison.md).
The rebuilt [regulatory export](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-recovery/october-em/document.html)
retains the two distinct illustration grids and `Not effective` status.

## What will move accuracy beyond 72%

The remaining low categories are old scans (13/58), old scan mathematics (78/136),
multi-column order (29/44) and tables (39/56). More relationship metadata alone
does not fix their recognition.

1. Add line-aware recovery for sloping ledgers with explicit label/value geometry.
   Validate every recovered value against its original row; preserve disagreements
   and missing cells. Never join nearby numbers solely by text similarity.
2. Recover text/formulas omitted by the detector, including text misclassified as
   pictures. Use uncovered ink to propose review regions, not to assert text;
   distinguish scan borders and rules before selecting another recognition path.
3. Compare a stronger pinned local reader on handwriting/formulas and a separate
   unseen source set. Keep raw alternatives and evaluate character/word accuracy,
   formula rules, table headers/spans and reading order independently. Calibrate
   coverage/confidence thresholds on those sources before changing defaults.
4. Re-run retrieval/answer checks after better readings exist. Require complete
   operative conditions/exceptions, exact source-bound claims, verified company
   facts and explicit unknowns before impact assessment or DOCX drafting. A higher
   OCR score or correct citation is not proof that an answer is complete/correct.

80% remains a target requiring measured content-rule gains, not a promised score.
