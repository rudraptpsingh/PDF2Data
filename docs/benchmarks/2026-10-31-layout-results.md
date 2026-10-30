# Local structured extraction: 21.53% to 71.75%

The new opt-in PDF2Data pipeline scores **71.75%** on the unchanged 70-PDF
benchmark, up from **21.53%** for automatic OCR with tables. The first 60%
project milestone is met. The proposed 80% general-extraction target remains
open. These are sampled predicate scores, not character or legal accuracy.

[Comparison](receipts/layout-guided/comparison.md) ·
[Machine-readable validation](receipts/layout-guided/validation.json) ·
[CLI guide](layout-guided-reading.md).

## Same inputs and denominator

All runs use the same source SHA-256 values and frozen suite
`a85780f812672738e228a0c93c09a611b41702d531ea9f34981737ae41fcb516`.
The pinned upstream evaluator checks 429 public rules plus 70 per-page
baselines: **499 outcomes**. No source, failed document, or difficult category
was dropped. This is 70/1,403 public PDFs; the full dataset was not evaluated.
No benchmark labels or expected answers enter recognition prompts.

| Configuration | Adjusted category macro | Documents with failed attempted readings |
| --- | ---: | ---: |
| Existing native + table output | 15.91% | 0 |
| Existing automatic OCR + tables | 21.53% | 0 |
| First layout-guided visual reading | 65.65% | 8 |
| Visual-only detector boxes | 71.75% | 4 |
| Visual-only + corroborated source tables | **71.75%** | **4** |

The macro averages seven public categories plus the baseline category. Four
failed documents contribute 25 failed outcomes in the final score. All 70
retain their processed/partial artifacts. A completed reading is not a proof
of complete source transcription.

| Category | Existing automatic OCR | Final structured pipeline |
| --- | ---: | ---: |
| Mathematical papers | 0/49 | **40/49** |
| Headers and footers | 8/32 | **31/32** |
| Long/tiny text | 7/54 | **51/54** |
| Multiple columns | 3/44 | **29/44** |
| Old scans | 6/58 | **13/58** |
| Scanned mathematics | 0/136 | **78/136** |
| Table relationships | 20/56 | **39/56** |
| Baseline checks | 57/70 | **60/70** |

This is a whole-pipeline improvement, combining recognition, region ordering,
table/formula handling and furniture projection. It does not isolate model
quality. Headers/footers remain in raw region data but are excluded from the
readable body; that policy accounts for part of the gain. Raw and coverage-
adjusted final scores are equal. Both remain in the receipts.

## What changed

1. **Layout-guided local reading.** Cached Heron detects original-page regions
   on PyTorch MPS. Pinned PaddleOCR-VL-1.5 reads text, tables and formulas on MLX
   Metal. Fixed task prompts receive source crops, never expected output.
2. **Uncontaminated detector boxes.** Native text-cell assignment/orphan creation
   was producing tiny artificial regions and shrinking visual boxes. The visual
   path disables both. Region count falls from 1,975 to 1,371. Source inspection
   confirms that a formerly clipped final headline letter is now inside its crop.
3. **Structured outputs.** Explicit HTML/OTSL cells retain blank positions and
   rectangular merges. Ragged, empty, truncated or inconsistent table output is
   rejected. Continuous Markdown/HTML and combined JSON retain source links,
   proposed block order, LaTeX formulas and unresolved visual items.
4. **Source-backed table reconciliation.** Native tables are considered only
   with original package/PDF identity, matching physical region, fresh native
   decoder agreement and ordered per-cell glyph conservation. Independent visual
   readings must support native body values/symbols. Amount punctuation and
   case-sensitive units are retained. Both candidate grids remain inspectable.
5. **Failure and reuse controls.** Token counts and explicit cap/empty/parse
   failures remain in receipts. Resume/reuse binds model weights, task prompts,
   runtime, original PDF, physical page and exact crop hash. Original page/crop
   pixels are re-rendered before reading. Changed model-cache metadata is rejected
   before inference/output creation.

Source-glyph checks alone are insufficient. Review found that native decoders
could agree on `_`, `~`, and `1Z` while visible source characters were dashes,
an arrow and boxed `27`. An initial native preference regressed two public
rules. The final visual-corroboration guard retains model readings in those
cases and removes both regressions. The final source-table step changes **zero**
public rule outcomes relative to the visual-only pass; it fixes the separate
legal control without claiming an additional public-score gain.

## Real regulatory control

The visual-only reader produced ten columns in two original nine-column
illustrations by splitting `RE Generator`. It retained `Not effective` under
the wrong header. One of seven legal-source probes therefore failed.

Corroborated source-table reconciliation restores both **2-by-9 grids**, retaining
all **36 original native cell values**. The second illustration keeps `Not
effective` under `GNA effectiveness date`, between `01.06.2027` and `30.07.2026`.
The distinct first-illustration dates, SCOD neighbours, optional rejoinder wording
and listing date are also retained.

Final controls: **7/7 source-checked legal probes + 8/8 baselines** on two PDFs,
eight pages. These are narrow regression controls, not an independent blind legal
accuracy benchmark. All output retains `source_review_required=true` and
`publication_eligible=false`. No company applicability or legal deadline is
inferred from this result.

Local full-document example:

`out/benchmarks/legal-probes-release/reconciled/56bebe798f88ed13c0d7/document.html`

The same folder contains `document.md`, `document.json`, `tables.json`, original
`model-tables.json`, `native-tables.json`, `native-layout.jsonl`, and selection
reasons in `table-decisions.json`.

## Validation and performance limits

**433 PDF2Data tests pass**, with one optional `docling_core` dependency skipped
in the base test environment. Actual layout execution uses its installed
Docling-core runtime. **120 legal-pipeline tests pass**. Negative tests cover
corrupt crop pixels despite changed hashes, malformed table merges, reversed
digits, native font-symbol errors, case-sensitive units and omitted failure
outcomes. A source/output reviewer independently verified score arithmetic,
all 499 outcomes, the known clipping cases and no non-table block mutations.

The first fresh layout run took **1,252.8 seconds (20m 53s)** for 70 PDFs, versus
**40.8 seconds** for existing automatic OCR. This is a significant quality/time
tradeoff. It used a single batched reader; the baseline used four extraction
workers. The visual-only pass took 1,015.7 seconds but reused **396 readings**,
so it is not a fresh-throughput comparison. Reconciliation reuses captured
readings and has a separately recorded processing time. Hardware and OS caches
were not isolated; the visual-only pass overlapped the legal controls.

Recorded detector forward inputs and parameters are on `mps:0`; the reader
records `Device(gpu, 0)`. Reader peak device allocation is approximately 2 GB.
This establishes local accelerator use, not a speedup over the existing path.

## Still open

- Five token-capped regions across four documents remain incomplete and counted.
- Old scans/handwriting are weak: 13/58. A manuscript leaf remains unboxed and
  another is retained as an unread picture. Lower formulas remain outside boxes
  in another source. Original images and coverage diagnostics remain available.
- Ink percentage is not semantic coverage: horizontal rules and binding gutters
  can produce large false alarms. No complete-transcription claim is made.
- Glyph decoding, table semantics, visual order and legal interpretation still
  require review. A native/model agreement can share an error.
- A full 1,403-PDF run, blind legal holdout and measured routing/latency benchmark
  remain necessary before unattended use. The default extraction path is unchanged.

Next work should recover missed source regions, add a targeted handwriting/old-
scan route and use validated native text selectively to control inference cost.
Keep the frozen benchmark and independent legal critical-value tests in place.
No Cloudflare resource, remote DB or deployment was changed.
