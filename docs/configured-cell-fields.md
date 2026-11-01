# Configured cell fields

`pdf2data.configured-cell-fields.v1` extracts literal values inside existing native
source cells. It keeps a draft quotation, a stakeholder request and a rationale in
their respective columns even when selected page text interleaves them.
It does not infer which columns have those meanings: the caller declares a
column filter and can retain an unverified `source_column_role_hint` in the catalog.

Install `PDF2Data[pdf]` for source-package verification.

```sh
pdf2data cell-fields extract --package ./l1/source \
  --rules ./cell-rules.json --output ./cell-fields --json
pdf2data cell-fields verify --bundle ./cell-fields --json
```

Use the existing `pdf2data.extraction-rules.v1` envelope. Each field must declare
nonempty zero-based `cell_columns`; optional `cell_rows` and `table_ids` narrow
the selection. Physical `pages` and declared `families` retain their existing
meaning. Regex patterns require a named `value` group and can capture `unit`.
Formula extraction and page-region scopes are rejected for this cell protocol.

```json
{
  "schema_version": "pdf2data.extraction-rules.v1",
  "id": "stakeholder-request-fee",
  "version": "1",
  "fields": [{
    "id": "requested_fee",
    "type": "quantity",
    "pages": [3],
    "cell_columns": [3],
    "source_column_role_hint": "stakeholder_request",
    "pattern": "fee of Rs\\s*(?P<value>[\\d,]+)\\s*/(?P<unit>MW)"
  }]
}
```

The output includes exact PDF/package/rules hashes, frozen field catalog,
field detection states and observations. Each observation has a native table/cell
JSON pointer, table/version/geometry identity, physical page and page-text hash,
row/column and spans, source bbox/frame, whole-cell text hash and cell-local value,
match and optional unit spans. The original native quality outcome remains.
`typed_candidate` can hold exact rational number components or an ISO date; it is
not calculation approval. Scaling, currency meaning and metric ownership are not
inferred from nearby text.

There is deliberately no top-level page `source_span`: a cell-local offset must
not be mistaken for a selected-page offset. Null, empty and non-string cells
produce no text observations; they stay unchanged in the original package.
Physical `cell_spans` are preferred, with an explicit geometry-unavailable fallback
for a grid-only source. Merged cells retain their spans and are selected by their
anchor column; no virtual covered-cell values are generated. Blank references
are never filled down. Continuation, issuer, instrument, applicability and column
role hints remain unverified.

Extraction verifies the existing L1 package, snapshots source/rule/code hashes,
evaluates in a bounded worker, and emits into a new directory outside the source.
Timeout 1–600 seconds, 50,000-observation and 128 MB limits fail without partial
output. Re-verification reopens the captured source package, checks artifacts,
source and implementation, then re-derives every observation and association.
A changed output with rewritten checksums still fails source re-derivation.
Relocation and cross-implementation migration are not supplied in v1. The host
must authorize access to the bundle and the source path it captures.

No PDF is reparsed, no model/network/database call is made and no source quality,
legal eligibility or publication approval is upgraded.

## Explicit v2 dimensions

Set `cell_fields_schema_version` to `pdf2data.configured-cell-fields.v2` in the
rules root to opt in. The default v1 compute output remains byte-for-byte stable
for the regression fixture; the existing v1 schema is retained separately.
V2 observations add `currency_literal`, `currency_cell_span`, `scale_literal`
and `scale_cell_span` from optional named regex groups `currency` and `scale`.
Missing/empty captures stay null. V2 also embeds the producer implementation
file hashes in the output, so consumers retain the code identity with the fields.

For `Rs. 3.5 Cr/MW`, capture `Rs`, `3.5`, `Cr` and `/MW` separately. The slash
belongs to the printed rate unit. Source spans remain local to the exact native
cell and captured match. No ISO currency alias, scale multiplier, currency
conversion or financial arithmetic is inferred: `currency_code_candidate` and
`scale_multiplier_candidate` are null and `dimensional_normalization_allowed`
is false. A separate v2 schema validates these boundaries.

A new implementation pin is required for re-verification; retain the original
installed version for an older v1/v2 bundle. This release does not migrate or
approve existing source bundles.
