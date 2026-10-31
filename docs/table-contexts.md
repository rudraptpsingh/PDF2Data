# Cell evidence and table context

Readable exports now include a hash-bound `table-contexts.jsonl` sidecar, one
record per physical table. It is written through both `export-readable` and
`pdf-structure-export`. Existing source grids, versions and page text remain
unchanged. Re-export an older package into a **new** destination to obtain it.

```sh
pdf2data export-readable --package ./l1/example --output ./readable/example --json
```

Each record contains:

- Original PDF hash, physical page and selected-page text hash.
- Table/version IDs, table box and pointers into the exported `data.json`.
- Every cell's literal text, text hash, Unicode codepoint span within that
  literal, grid coordinates, merged spans and available source box. Blank
  cells remain blank. Grid-only readers are explicitly marked as lacking
  cell geometry.
- Exact selected-page offsets **only when the full literal occurs once**.
  Repeated or absent strings retain cell-local evidence and a warning. A
  missing unique page quote does not mean the raw cell value was lost.
- Candidate column headers, including explicit merged/multiple header rows,
  and references to preceding cells in the same physical row.
- A reference to an earlier physical header when the existing continuation
  pass found a table-continuation candidate. Values are never copied into
  another page's grid.
- Literal unit, period and footnote candidates in cells or nearby source
  lines. Proximity is recorded as proximity; contradictory dates are both
  retained. A nearby unit/date is never silently assigned to all values.

Header roles remain candidates. Numeric data rows do not become headers just
because they appear first. Unresolved headers, missing geometry and ambiguous
page quotes are visible separately from source/content approval.

## MCP reads

The existing `pdf2data_read` tool accepts `artifact="table_contexts"` and optional
`page`, `table_id`, and `cell_id`. A cell request returns the requested cell
with its local header/left-row context. References to an earlier page retain
that page and table ID, so the caller can retrieve the original header too.

```json
{
  "run_id": "<returned run ID>",
  "artifact": "table_contexts",
  "page": 5,
  "table_id": "<source table ID>",
  "cell_id": "<source cell ID>",
  "max_chars": 30000
}
```

The server verifies the sidecar hash and streams physical JSONL records rather
than loading the entire sidecar. Oversized responses request a narrower scope;
they do not silently cut values. Older exports return
`not_available_reexport_required`.

## Downstream use

Index the value together with its candidate headers, row context, period/unit
evidence and continuation references. Retrieve those dependencies with the
value. Do not turn a table cell into a verified financial metric solely from
this sidecar: metric identity, actor/SPV, direction, aggregation, conflicting
periods and applicability still require the consumer's validation.

Validation covers repeated literals, missing selected-page text, blank values,
merged/multirow headers, continuation references, numeric first rows, conflicting
nearby dates, Unicode and missing source geometry. CLI-backed MCP tests also
reject modified sidecars.
