# Sharing native source exports — I3, 1 November 2026

Completed producer-only export of the five exact archived originals requested
through central Query. No other owner repository was changed. No model/GPU,
database, deployment, consumer integration or waiver calculation was run.

Run root: `out/sharing-native-i3-2026-11-01/` in this worktree. The complete
`export-manifest.json` pins the original hashes, newly extracted immutable L1
packages, native `pdf2data.processed.v1` packet, query records and readable
sidecars. The original legacy packages were not modified or relabelled as native
packages. Their prior hashes remain recorded separately.

| Exact archived file | PDF SHA-256 prefix | Pages | Tables | Repair-required pages |
| --- | --- | ---: | ---: | --- |
| DN-SIST-5rd_Amend.pdf | 00bb0b39fbca29ee | 7 | 1 | 4 |
| Supplementary-Draft-Notification2026.pdf | 2efc99923a7df73c | 3 | 1 | 2 |
| PN-supplementary-notification-sharing-5th.pdf | 6d66b3d1005a050c | 1 | 0 | None |
| EM_SIST-5rdAmend.pdf | 8534d3cd8f2206e6 | 22 | 2 | 2, 4, 8, 10, 18 |
| EM-supplementry-sharing-5th.pdf | 17b7dd0fe13e4e4 | 6 | 2 | 5 |

All **39 physical pages, 6 detected tables, 115 physical cells, 43 native units
and 126 native children** are exported. Eight quality holds remain; no pages are
withheld. Every native dependency slot is preserved, including unresolved empty
lists; emptiness does not prove semantic dependency completeness.

## Commands and evidence

Processing used the tool CLI, with a manifest containing expected original byte
hashes, physical page counts and declared document families:

```bash
pdf2data legal-l1 --manifest input-manifest.json --output packages --ocr off --json
pdf2data verify-legal-l1 --output packages/SOURCE_ID --json
pdf2data export-l1-data --packages packages --output processed.json --json
pdf2data export-query-data --packages packages --output query-records.jsonl --json
pdf2data export-readable --package packages/SOURCE_ID --output readable/SOURCE_ID --json
```

The native packages were individually re-verified. Read-only output auditing
checks original/package hashes, complete raw page/layout/table/structure/quality
preservation, every native unit/child/region Unicode codepoint span, child-parent
handles, every readable artifact checksum, table-context/cell readback and query
payload hashes. Query output contains 93 records. Readable tables retain all raw
fields and add only their existing candidate display-placement annotation.

Each readable folder includes original PDF/page rasters, full MD/HTML, document
model/parts, continuations, formulas and table-context sidecars. Header/footer and
footnote associations remain candidates or unresolved; their presence does not
prove detection completeness. Condition text remains in full source-parent
units. This run does not claim independently reviewed condition trees.

Own component tests for L1, processed/query/readable exports, document parts and
table contexts: **68 passed**. `native-export-audit.json` records the real-data
checks; all its assertions passed. No source/parser code was changed.

## October variant and scope

Original rasters for the three-page supplementary draft and every detected table
page were inspected. The exact supplementary draft retains `31.03.2027`, `SCOD
as per PPA or PSA`, `30.04.2027`, `extended beyond original tenure`, `thirty days`
and the already-COD one-month effectiveness route. Each audit occurrence has its
physical page, page-text hash, exact offset and native owner handle. Its operative
parent retains all three physical pages.

The two October EM illustrations remain different table IDs and values: the
first GNA effectiveness cell is `31.12.2027`; the second is `Not effective`.
No report681 replacement, July/October composition or unsupported
non-hypothetical `sharing_delayed` rule was introduced. Full condition/exception
interpretation, later treatment, client applicability and calculation permission
remain unreviewed. Native exports close the missing-producer-artifact dependency;
they do not close those legal or consumer gates.
