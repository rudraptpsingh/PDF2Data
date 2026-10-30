# Source-bound readable exports

`export-readable` now writes `document-blocks.json` and the captured
`layout.jsonl` alongside `data.json`, `relations.json`, Markdown, HTML and
optional Word. Raw page text and source packages remain unchanged.

Each ordered block carries a deterministic ID, kind, exact page character spans,
page text hashes, source bounding box where resolved, candidate parent unit IDs,
and candidate typography/list metadata. The relation graph includes block order,
source spans and table links. Structure is evidence for display; it does not
verify legal hierarchy, applicability, deadlines or eligibility.

The same block sequence drives Markdown, HTML and Word. A table replaces flattened
text only when its complete non-whitespace character inventory matches captured
glyphs inside its source bounding box and one unique selected-text interval.
Otherwise raw text remains, and the table is explicitly unresolved. This check
proves character conservation and position, not correct legal interpretation or
cell reading order. Retained cell spans/header roles remain candidates; invalid
merges, overlapping spans and merges hiding populated cells are rejected.

Terminal page-number footers may be excluded from continuous body text only when
exact native glyph lines place them wholly in the bottom seven percent of the
source page. They remain in raw pages and block spans. This prevents footer text
being joined to the next page's legal paragraph.

## Validation

Block validation checks full page hashes, unique IDs, exact quotes and a complete,
gap-free, non-overlapping partition of every source character. Export validation
also checks graph endpoints and Word paragraph/table-cell readback. Legal
publication eligibility remains false.

Run the normal suite with `PYTHONPATH=src python -m pytest -q`. Real-source tests
are opt-in:

```sh
PDF2DATA_REAL_EXPORTS=out/structured-review/review-v4 \
  PYTHONPATH=src python -m pytest -q
```

That directory must contain CLI-produced `sample`, `october-em` and `rop`
readable exports. Use `pdf2data legal-l1` to create packages and then
`pdf2data export-readable --package PACKAGE --output OUTPUT`; do not populate
fixtures with a separate PDF reader.

The 30 October 2026 local run passed **379 tests, one optional dependency skip**.
Three PDFs were freshly processed through the CLI (2, 6 and 2 pages). Checks cover:

- Generic source: original heading order; table once, between Outputs and Review.
- October explanatory memorandum: two distinct inline illustrations; all 36
  cells, including `31.12.2027` versus `Not effective`; intervening `(a)`–`(c)`
  conditions; all six footers separated; page-five footer no longer joins `(a)`.
- Record of proceedings: optional `if any` direction remains connected across
  pages to `within two weeks thereafter`; source footer remains in raw audit.
- Synthetic negative cases: ambiguous table mapping retains raw text; invalid
  merges fail; bold body text remains body; wrapped `(h-i)` remains one reference;
  no source character or legal-publication gate is silently removed.

Rendered Word QA inspected the generic document and all five October output
pages. Both tables are inline; wide-table font is reduced and rows kept intact
so dates and `Not effective` remain readable. Page wrapping differs from source.

## Remaining limits

This is a logical conversion, not pixel-identical reproduction. Heading/list
depth and header roles are candidates, list markers are retained literal text,
and Word does not invent automatic legal numbering. Native geometry may be
unavailable for scanned/OCR text; such tables remain unresolved. Some long Word
headers still wrap within narrow cells. Source coverage cannot establish that
OCR spelling or table semantics are correct; review and downstream legal gates
are still required.

## Measured before/after comparison

[Machine-readable receipt](source-block-fidelity-receipt.json) compares fresh CLI
processing of the same three PDF hashes at `c8328d1` against `11de3b8`.
The baseline CLI was frozen with `git archive` in an ignored output directory;
no existing checkout was changed.

| Check | Before | After |
| --- | --- | --- |
| Tables placed inline between correct source sections | 0/3 | 3/3 |
| Explicit flattened-table duplicate probes | 2 | 0 |
| October structured table values | 36/36 | 36/36, identical |
| Page-five footer glued to next condition | Present | Absent |
| RoP optional cross-page rejoinder condition | Preserved | Preserved |
| Raw selected text | 18,672 characters | Identical page hashes |

Duplicate probes are `Format Purpose` outside the source table and the extra
`Not effective` occurrence. They are bounded probes, not a complete duplicate
detector. Already-correct cell values and cross-page conditions are retained
controls, not new extraction gains. Source blocks add a complete source-text
partition audit; they do not improve OCR spelling in these native-text cases.

Reproduce the artifact comparison:

```sh
python scripts/measure_export_fidelity.py \
  --before out/fidelity-before/readable \
  --after out/structured-review/review-v4 \
  --output docs/source-block-fidelity-receipt.json
```

An independent generic holdout uses nested `(a)/(i)/(ii)/(b)` markers and a
two-column merged header above voltage data. It was generated after the format
fix with `scripts/generate_formatting_holdout.py` (requires ReportLab), processed
only with `pdf2data legal-l1`, then exported through `pdf2data export-readable`.
Its CLI result retains list depths `0,1,1,0`, source-backed `colspan=2`, one inline
table between the original captions, and all source text. This is one simple
geometric holdout, not evidence for arbitrary tables or legal hierarchy.

Enable its regression with
`PDF2DATA_FORMATTING_HOLDOUT=out/formatting-holdout-v2/readable` alongside the
three-source environment above. Final combined run: **380 passed, one optional
dependency skip**. All additions after `11de3b8` are measurements, fixtures and
documentation; parser behavior is unchanged.
