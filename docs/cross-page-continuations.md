# Cross-page source context

Status: implemented on `codex/pdf2data-cross-page-context`; not included in the
published `0.2.0-rc.1` package.

The readable export writes `continuations.json`, also embedded in
`document-model.json` and `data.json`. Candidate links are included in
`relations.json`; Markdown and HTML link continuing fragments to their original
source pages. Both native L1 and region-reader exports use the same logic.

This is document assembly, not another OCR pass. Original pages, text spans,
cell values, blank slots, grids and source hashes remain intact. A table
fragment is never silently filled with an assumed header or merged into a new
source table.

## Supported candidates

- Tables on adjacent pages: compatible column geometry, bottom/top placement,
  an established candidate header origin and no intervening body section.
  Later fragments reference the original header table/page/row. Repeated
  headers remain in the raw fragment; a chain retains the same candidate group.
- Lists: compatible parent and indentation, sequential letter/number/Roman
  markers or the same bullet style. A restart or changed parent stays separate.
- Split list items and paragraphs: an open sentence, compatible source parent,
  indentation and page-boundary geometry. Existing continuous paragraphs keep
  exact spans and visible links to every contributing physical page.

These are **unreviewed candidates**. Geometric similarity alone cannot establish
the same account period, transaction or legal clause. New/conflicting headers,
terminal totals, section content, missing pages and missing geometry prevent an
automatic link. Reasons are retained under `unresolved_boundaries`. Numeric
first rows and numeric year headings remain unresolved rather than being
asserted as headers. Text-only data rows remain a limitation of that guard.

Example record, shortened:

```json
{
  "kind": "table_continuation_candidate",
  "from_page": 2,
  "to_page": 3,
  "group_id": "table-group-…",
  "header_context": {
    "table_id": "source:p2:table1",
    "page": 2,
    "row": 0,
    "raw_cells": ["Entity", "Payable", "Receivable"],
    "association": "source_reference_only_not_copied_into_fragment"
  },
  "status": "unreviewed_candidate",
  "publication_eligible": false
}
```

## Table continuation case and signals

Each `table_continuation_candidate` link also carries:

| Field | Meaning |
| --- | --- |
| `continues_table_id` | The fragment on the previous page (same as `from_id`). |
| `origin_table_id` | First fragment of the chain; its row 0 is the header. |
| `case` | `header_repeated`, `no_header` or `row_split`. |
| `signals` | Evidence that held, from `same_column_count`, `matching_column_boundaries`, `repeated_header`, `page_foot_to_page_head`, `continuation_marker`, `no_intervening_body_content`. |
| `header_similarity` | 0–1 against the origin header, `null` when the later fragment has no header row. |
| `first_data_row` | 1 when the later fragment repeats the header, else 0. |
| `row_split` | `null`, or `{from_table_id, from_row, to_table_id, to_row, basis, association}` naming the two halves of one row. |
| `continuation_markers` | The "(continued)" blocks or first-row cells that were read as markers. |

A link is emitted only when the fragments are on adjacent pages, have the same
column count and at least one of: a repeated header (mean per-column
similarity ≥ 0.9, no column below 0.75); the earlier fragment ends in the
bottom 30% of its page and the later starts in the top 25%; a short
continuation marker between them or in the later fragment's first row. Body
text between the fragments, a terminal total row, a different header, differing
column boundaries or missing table boxes still prevent a link.

`row_split` is reported only in its complementary form: the earlier fragment's
last row has a label and blank cells, the later fragment's first data row has a
blank label, and no column has text in both. A sparse row can look like this,
so it stays a candidate. No cell is moved, merged or renumbered; a reader that
ignores these fields sees the same fragments as before.

The same facts appear on the later fragment's `table-contexts.jsonl` record as
`continuation` (`null` for a table that continues nothing).

Not recognised: a later fragment whose first row is all text and not a repeated
header (it reads as a new header), a row whose every cell wraps across the
break, chains whose first fragment has no header row, and gaps of more than one
page.

Full records also bind the original PDF hash, both page-text hashes, fragment
IDs, geometry and basis. Text/list records retain exact source spans. Model
validation regenerates the candidates, rejecting altered header references or
anchors. Source-table header behavior participates in extractor identity, so
old extraction results cannot silently resume under a changed policy.

## CLI and downstream use

Use the normal `legal-l1` → `export-readable` chain, or the existing
`pdf-structure-export` command for region readings. Outputs require a fresh
destination. Old packages remain immutable; old exports do not acquire new
links automatically.

MCP page reads include continuation context. `pdf2data_read` also accepts
`artifact: "continuations"`, optionally scoped by physical `page`. It verifies
the exported artifact hash, retains unresolved boundaries and requests narrower
scope when a complete result exceeds the limit. An older export without this
artifact returns `not_available_reexport_required` rather than assuming there
are no continuations.

A retrieval consumer should hydrate the linked fragments and header source
alongside the requested cell or paragraph. It must retain unresolved boundary
warnings and report incomplete context if the complete dependency set exceeds
its budget. It must not infer a payable/receivable direction from column
position alone, upgrade candidate status, or treat all geometrically compatible
tables as one accounting period. Graph consumers must explicitly support these
new relation types; existing search policies do not change automatically.

DOCX retains physical table fragments and candidate source structure. Automatic
creation of a single reviewed Word table across uncertain boundaries is not
implemented. Multi-row header recovery, text-only continuation rows, named
continued captions, footnotes that cross pages and mixed orientation need
further annotated cases. No universal continuation-recall score is claimed.

## Validation in this branch

The 31 focused continuation/header cases cover positive links, list restarts,
numeric/year headers, incompatible columns, new sections, totals, missing pages,
source preservation, multi-page header references and tampered model metadata.
Three MCP tests exercise real CLI processing and official-SDK stdio reads,
including continuation artifacts and changed-file rejection.

A local five-original / 45-page CLI replay covers account tables, a regulatory
memorandum, a calculation note and two procedural records. It finds a two-page
table continuation and a separate three-page chain, retains a split numbered
clause, and keeps a new numbered paragraph separate. Original account text and
all 32 table grids/version IDs are unchanged; two separate illustrations retain
36 cells. These are development/source regressions, not a blind estimate of
cross-page recall. No new recognition-score improvement is claimed.
