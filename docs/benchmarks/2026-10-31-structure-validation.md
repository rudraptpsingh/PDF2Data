# Source structure: implementation and measured results

31 October 2026. Local feature-branch work; no Cloudflare or database changes.

## Outcome

The shared document-structure export is implemented and connected to
`pdf-region-read` and `pdf-table-reconcile`. The **coverage-adjusted OCR benchmark
remains 71.75%** on the same 70 PDFs and 499 outcomes. No adjusted outcome improved
or regressed. The structure update changes representation, not recognition.

The separate ten-question development retrieval diagnostic improves from **5/10**
annotated evidence sets with flat text to **8/10** with search projections and
**10/10** with candidate structure expansion. This is lexical retrieval on one
reviewed legal document, not independent legal answer accuracy. Four questions
still have broader candidate closure groups that overflow the 6,000-character cap;
the local answer diagnostic refuses those incomplete candidate contexts.

## Review the original test PDFs and outputs

- [All 70 benchmark PDFs, individual scores, original/output maps and review flags](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/benchmarks/source-structure-final-review/index.html)
- [Full benchmark source map JSON](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/benchmarks/source-structure-final-review/map.json)
- [Regulatory memorandum: structured document and original-page comparison](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-final/pipeline/56bebe798f88ed13c0d7/structured/document.html)
- [Same document: canonical JSON](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-final/pipeline/56bebe798f88ed13c0d7/structured/data.json)
- [Source relationships](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-final/pipeline/56bebe798f88ed13c0d7/structured/relations.json)
- [Search projections](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-final/pipeline/56bebe798f88ed13c0d7/structured/search-projections.json)
- [Two-page proceedings: preserved cross-page reply/rejoinder context](/Users/rp/.codex/worktrees/pdf2data-structured-export/PDF2Data/out/structured-document-final/pipeline/0bb718f69b9e7ff5939a/structured/document.html)

The benchmark index links every original source PDF to its source-first page map
and structured export. Rows can be filtered by filename/category. Fractions in each
row are per-document rule counts, not category-macro scores. Maps establish
artifact correspondence; they are not manual quality approval of all 70 sources.

## What changed

- Added `region_document.py`, an adapter to the existing document-block/span
  contract. It reuses `render_blocks`, `full_document` and the shared HTML viewer.
- Every captured region remains in the canonical transcript and raw document.
  Exact text hashes/spans are checked, including whitespace between region readings.
- Contained key/value children are suppressed only in the readable/search
  projection when the parent/child text and geometry support the same reading.
  The date field in the memorandum now renders once; both child readings remain.
- Explicit quotation containers, nested list parents, numbered clause candidates,
  cross-page continuation, illustration/caption/table/scenario links and unresolved
  external citations are emitted as **unreviewed candidates with source spans**.
- The two page-5 illustration grids retain all **36 original cells**, including
  the second illustration's `Not effective` status. That status is not invented
  into a date. The regulatory `Table 1` reference remains external/unresolved.
- Dates retain literal text and candidate context; illustration/Suppose dates are
  distinguished from established legal dates. A quoted OM's printed date can also
  supply normalized month wording to its search context, without rewriting evidence.
- Search projections retain heading/quote/illustration paths, table header/value
  associations, source cell IDs and original spans. They are search aids, not new facts.
- Both normal region-reading and table-reconciliation commands automatically write
  `structured/` alongside their immutable raw artifacts. Resume checks source,
  implementation and saved export hashes before reusing that derived package.

## External benchmark: unchanged adjusted score

The dataset revision, selected source hashes, 429 public rules, 70 baseline checks
and pinned upstream evaluator are unchanged. Four original OCR-failed documents
remain failures; their 25 rule outcomes are retained in the denominator.

| Category | Prior pass / tests | Updated pass / tests |
| --- | ---: | ---: |
| arxiv_math | 40/49 | 40/49 |
| baseline | 60/70 | 60/70 |
| headers_footers | 31/32 | 31/32 |
| long_tiny_text | 51/54 | 51/54 |
| multi_column | 29/44 | 29/44 |
| old_scans | 13/58 | 13/58 |
| old_scans_math | 78/136 | 78/136 |
| table_tests | 39/56 | 39/56 |
| **Coverage-adjusted category macro** | **71.75%** | **71.75%** |

Raw macro rises to approximately 73.26% because seven absence checks pass on
empty/unread nonblank pages after serialization. **Those apparent gains are
excluded from the adjusted result. They are not OCR improvements.** The source-map
index and headline comparison use adjusted outcomes.

For historical context, the older automatic-OCR profile scored 21.53%. That earlier
recognition improvement was measured before this structure change and must not be
attributed to the new adapter. This is a 70-PDF subset, not the full 1,403-PDF suite
or a leaderboard submission. The official evaluator checkout was unmodified.

## Retrieval diagnostic

Questions/required source regions were frozen before the adapter was implemented;
their SHA-256 is recorded in the receipt. The queries were subsequently used during
development, so this is not a blind holdout. The extractor never receives those
questions, expected source IDs or previous manual relationship annotations.

All variants use the same BM25 implementation, exact provision-reference tokenizer,
function-word filter, two discovery seeds and 6,000-character selection cap. They
fill remaining budget with whole units; no source unit is clipped. Actual selected
context size differs slightly. This is not a fixed LLM-token-budget comparison.

| Variant | Annotated sets retrieved | Mean required-region recall | Mean context chars |
| --- | ---: | ---: | ---: |
| A: flat original region text | 5/10 | 70.83% | 5,430 |
| B: structured search projections | 8/10 | 95.83% | 5,500 |
| C: candidate structure expansion | 10/10 | 100.00% | 5,546 |

An earlier diagnostic iteration split provision references such as `1.5` into
separate numbers and lacked normalized quoted-date context. Its retained results
were 4/10 → 6/10 → 8/10. The tokenizer correction was applied equally to every final
variant, without changing the frozen questions or expected source regions.

Four C contexts still omit part of a broader candidate dependency group under the
cap: intermediary, termination, hypothetical and tenure. A match to every annotated
region is therefore not proof of complete legal dependency closure. Unresolved
external instruments, verified company facts and current law remain outside this
single-source experiment.

## Local model and answer-validation findings

The Powerlex `diagnose-structured-source` command rebinds the selected context to
the source export, reuses the existing cached Qwen3-VL-8B MLX runner and checks the
returned citations against exact transcript spans. It does not ingest into a DB.

Three live GPU cases were run: conditions, minimum requirements and contract
submission scenarios. The final run was rejected in all **3/3** cases:

- The conditions answer normalized a date into a numeric form not present in its
  cited text; the existing uncited-number/date guard rejected it.
- The minimum-requirements response swapped two source IDs while quoting the
  opposite passages; exact source binding rejected it.
- The submission response also contained a quote/source mismatch and was rejected.

An initial diagnostic exposed a separate validator bug: a blank `answer` with
nonempty valid citations could pass. That is fixed and regression-tested. The
generic prompt now requests a nonempty explanation and source-order context, but
prompt changes alone did not fix citation reliability. Earlier blank-answer passes
are superseded by this result. Raw requests/model outputs/failures remain local.

These are successful rejection checks, not successful legal answers. Model output
must remain review-only until source binding and claim-level validation pass.

## Validation and reproducibility

- **455 parser tests passed; 1 optional test skipped** because `docling_core` is not
  installed in the base test environment. Real CLI-export and formatting holdout
  fixtures were enabled. Docling remains installed in its separate detector runtime.
- **129 legal-pipeline tests passed**, including empty answers, tampered source
  artifacts, context overflow, incorrect projections and nonzero CLI failure status.
- **70/70 public and 2/2 legal source exports** completed with all inputs unchanged.
  This tests processing/integrity, not independent semantic fidelity for all sources.
- All seven custom legal source probes and eight baseline checks still pass.
- All 36 illustration cells are unchanged. The proceedings' numbered paragraph 2
  and its page-2 optional rejoinder now share a candidate clause with a continuation edge.
- The actual CLI chain was exercised: pinned local GPU reader → native source-table
  reconciliation → automatic shared structured export. It reused 86 saved readings,
  generated no new OCR readings and retained source-review gates.
- Original PDF, page raster, input document, model/reader and derived artifact hashes
  are retained. Continuous exports and the 70 page maps keep source links.

No fresh OCR throughput improvement is claimed. The structure-only batch takes a
few seconds on already recognized data; it is not comparable to fresh OCR/model
inference time. Browser automation previously rejected local file previews; visual
browser QA of the new HTML is not claimed. BuildView MCP was unavailable on port
3334, so no dashboard update was recorded.

## Commands

Use new output directories. Existing benchmark/source outputs remain immutable.

```bash
./pdf2data pdf-structure-export \
  --document /path/to/region-output/document.json \
  --layout /path/to/layout/layout.json --output out/structured --json

./pdf2data pdf-structure-evaluate \
  --raw /path/to/region-output/document.json --structured out/structured \
  --cases docs/benchmarks/source-maps/structure-retrieval-cases.json \
  --max-chars 6000 --output out/retrieval-comparison --json

./pdf2data benchmark-structure-run --suite out/benchmarks/public-70/suite.json \
  --run out/benchmarks/public-70-structured-release \
  --structured out/structured-document-final/public-70 \
  --output out/benchmark-structure-new --json

./pdf2data benchmark-score --suite out/benchmarks/public-70/suite.json \
  --run out/benchmark-structure-new --upstream out/benchmarks/vendor/olmocr \
  --eval-python out/benchmarks/eval-venv/bin/python --mode tables \
  --output out/new-score --json
```

See `scripts/validate_region_structure_batch.py` and
`scripts/map_benchmark_outputs.py` for the exact batch/map CLI drivers. Sanitized
score outcomes, source-map rows, runtime/model failures and validation receipts are
tracked in `docs/benchmarks/receipts/document-structure/`; full originals and exports
remain under ignored `out/`.

## Remaining work

### Why OCR remains at 71.75% and how to move it

The new export reused recognized text; it did not replace the weak readings. Of
499 adjusted outcomes, 341 pass and 158 fail. Failure types are 67 mathematical,
35 reading-order, 25 present-text, 17 table, 10 baseline and four absence checks.
These are failed test types, not individually confirmed root-cause diagnoses.

There are four original failed documents with five capped regions: old scans
`42.pdf`, `67.pdf`, `84.pdf`, and table source
`ff19f6fe9ce187ea79f84df52004534e6ff7_pg4.pdf`. Their 25 rule outcomes currently
remain failed as extraction errors. A higher budget alone may not fix a repeating
reader; use source-bound region/line splitting where long crops or repetition are
the cause, with explicit overlap/deduplication and immutable raw responses.

Prioritize the next recognition iteration as follows:

1. **Bounded retry of those five failed regions.** Preserve all successful cached
   readings; rerender and prove the retry crops against original pixels. Track
   original/retry token limits and source/model hashes separately. Do not rerun
   every successful page or claim a cached retry as fresh OCR throughput.
2. **Old-scan and handwriting recovery.** Route residual glyph-like regions and
   both manuscript leaves to explicit reading. Test deskew/contrast/leaf splitting;
   keep rules and binding gutters out of semantic coverage estimates. Thirty-six
   visual-review regions across the sample remain untranscribed/review-required.
3. **Formula and multi-column recovery.** Re-read missing/incorrect formula crops
   with surrounding source context; compare column/line ordering against images.
   No expected benchmark expression may enter a reader prompt.
4. **Remaining table cases.** Validate row/column alignment, merged headers, cells
   and notes from original geometry and independent visual readings; retain a
   conflict rather than copy a neighbour or silently fill a blank.

An illustrative path above 80%, under the current eight-category macro, would be
old scans 13→35 of 58, old mathematical scans 78→100 of 136, and multi-column
29→35 of 44. If every other count stayed fixed, that would yield approximately
80.22%. These are planning targets, not measured gains or a prediction that those
improvements are attainable with one model. Re-score the unchanged 499 outcomes
after recognition actually changes, and report regressions and unresolved pages.

Improve old-scan/handwriting recognition, recover missed visual regions and retry
token-capped crops to move OCR quality beyond 71.75%. Separately validate automatic
relationships on unseen legal sources, replace overly broad candidate closure with
reviewed dependencies and improve claim/citation generation. Then rerun retrieval
with the production-compatible hybrid policy and independently reviewed answers.
Source approval, currentness and company applicability remain unresolved.
