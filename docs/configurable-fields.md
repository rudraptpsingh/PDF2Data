# Configurable extraction rules

Create new metadata fields for a document category by editing JSON. No parser
branch, model dependency or database column is required for each new field.
This feature is on Git `main`; it is not included in npm `0.2.0-rc.1`.

## Install and create a ruleset

Install the current source version in a virtual environment:

```bash
python -m pip install 'PDF2Data[pdf,mcp] @ git+https://github.com/rudraptpsingh/PDF2Data.git@main'
pdf2data fields init --output ./document-rules.json
pdf2data fields catalog --rules ./document-rules.json --search date --json
```

MCP requires Python 3.11+. Omit `mcp` for CLI-only use. Existing source checkouts
can run `./pdf2data` with their configured `PDF2DATA_PYTHON` runtime.

`init` copies a packaged example with reference, numeric date, percentage and
formula observations. It never overwrites an existing rules file. Adapt the
patterns and scopes to your documents; the example is not a universal legal
metadata classifier.

## Define fields

```json
{
  "schema_version": "pdf2data.extraction-rules.v1",
  "id": "contract-fields",
  "version": "1.0.0",
  "families": ["generic"],
  "fields": [
    {
      "id": "contract.reference",
      "label": "Contract number",
      "type": "reference",
      "pattern": "Contract No[.:]\\s*(?P<value>[A-Z0-9/-]+)",
      "flags": ["IGNORECASE"],
      "pages": [1]
    },
    {
      "id": "contract.signed_on",
      "type": "date",
      "pattern": "Signed:\\s*(?P<value>\\d{2}/\\d{2}/\\d{4})",
      "date_format": "%d/%m/%Y"
    },
    {
      "id": "contract.capacity",
      "type": "quantity",
      "pattern": "Capacity:\\s*(?P<value>\\d+(?:\\.\\d+)?)\\s*(?P<unit>MW)"
    },
    {"id": "contract.formulas", "type": "formula", "extractor": "formulas"}
  ]
}
```

The optional `families` list matches the package's **declared** document family.
It is a processing hint, not verified legal classification. Omit it to search
all families, or override it on an individual field. Existing categories include
`regulation`, `draft`, `explanatory_memorandum`, `judgment`,
`record_of_proceedings`, `contract`, `notice` and `generic`; use the family
actually recorded in your source package.

Field properties:

| Property | Meaning |
| --- | --- |
| `id`, `label`, `description` | Stable search/index key and optional human descriptions |
| `type` | `text`, `reference`, `date`, `quantity` or `formula` |
| `pattern` | Python regex with a required named `value` group; optional `unit` group |
| `flags` | Optional `IGNORECASE`, `MULTILINE`, `DOTALL` |
| `pages` | Optional one-based physical page selection |
| `scope` | `all`, `body_candidate`, `header_candidate` or `footer_candidate` |
| `date_format` | Explicit `strptime` format; failed dates remain literal candidates |
| `unit` | Configuration hint only; it does not substitute for a printed `unit` group |
| `extractor` | `regex` by default, or `formulas` to retain existing formula observations |

Use Unicode labels and alternations for different languages. Rules cannot repair
garbled native text or recover missing OCR. V1/v2 regexes span one selected
physical page. Opt-in [v3 multipart fields](multipart-fields.md) retain exact
cross-page native-parent spans; they do not prove legal scope, table ownership
or formula geometry.
Formula extraction supports explicit pages but does not support region scopes
until a formula component graph establishes them.

## Extract, search and pass results downstream

First create an immutable L1 package with `legal-l1`. Then:

```bash
pdf2data fields extract --package ./l1/source-id \
  --rules ./document-rules.json --output ./fields/source-id --json
pdf2data fields query --bundle ./fields/source-id --field contract.capacity --json
pdf2data fields query --bundle ./fields/source-a --bundle ./fields/source-b \
  --text 'ABC' --limit 50 --offset 0 --json
```

Each output contains `fields.json`, a frozen `rules.json`, and a checksum
manifest. Observations retain the original PDF hash, physical page, selected-page
hash, Unicode character offsets, exact quote, available geometry, field/ruleset
IDs, ruleset version/hash, page-quality state and validation issues. Dates have
an optional ISO candidate; quantities use exact rational strings. Raw values are
never replaced. Missing matches are `not_detected_not_proven_absent`.

Search verifies bundle hashes and returns explicit pagination. A repeated
`--bundle` searches multiple documents while retaining each observation's source
and ruleset identity. The current multi-bundle command requires narrowing a query
when one input bundle has more than 1,000 matching observations.

For downstream SQL/RAG, index `field_id`, `rule_set_id`, `rule_version`,
`source_pdf_sha256`, physical page, literal/typed value and source span. New field
IDs fit the same observation table or JSON payload. Filter by provenance and
review state before using values; a field match does not establish applicability.

## Include rules in the UI and MCP pipeline

```bash
pdf2data export-readable --package ./l1/source-id --rules ./document-rules.json \
  --output ./readable/source-id --json
PDF2DATA_RULES=/absolute/path/document-rules.json \
  pdf2data workspace --workspace ./workspace
```

Readable outputs include `configured-fields/` in their checksum manifest and ZIP.
`data.json` links its field manifest. Set the same `PDF2DATA_RULES` environment
variable when starting the MCP server; newly parsed documents then expose
`pdf2data_read(run_id, artifact="fields", page=1)`. Oversized MCP reads request
narrower scope without silently clipping content. Re-export older documents to
apply a new ruleset; previous outputs remain snapshots.

## Evaluate against reviewed source annotations

For configurable counts, values, pages and capture assertions, use
[`fields check` with JSON check rules](field-check-rules.md). Source-reviewed
precision/recall evaluation remains a separate check below.

```bash
pdf2data fields evaluate --bundle ./fields/source-id \
  --references ./reviewed-fields.json --output ./evaluation/run-1 --json
```

Reference format:

```json
{
  "source_pdf_sha256": "COPY_THE_ORIGINAL_PDF_HASH",
  "rules_sha256": "COPY_THE_FROZEN_RULESET_HASH",
  "reviewer": "Source reviewer name",
  "reference_scope": "human_source_review",
  "fields": [{
    "field_id": "contract.reference",
    "expected": [{
      "page": 1,
      "page_text_sha256": "COPY_THE_SELECTED_PAGE_HASH",
      "char_start": 42,
      "exact_quote": "ABC/2026"
    }]
  }]
}
```

Review the original PDF independently when making labels. Do not generate gold
by copying every prediction. Use `development_fixture` for synthetic test data.
Include `expected: []` for reviewed negative fields. Annotate **all** occurrences
of each labelled field: omitted predictions count as false positives. Unlabelled
fields are reported separately and do not contribute to precision/recall.

Evaluation rechecks source/rule identities and exact reference spans, then reports
TP, FP, FN, precision, recall, missing values and extra values. Exit `3` means an
evaluation mismatch; exit `2` means invalid/stale inputs. This evaluates labelled
field detection and exact boundaries, not symbolic formula correctness or legal
truth. Formula components, conditions, units and client applicability need their
own checks before computation or drafting.

Add optional `typed_candidate` and `unit_literal` to an expected source span to
check date normalization, exact numeric values and printed units. A normalization
or unit mismatch fails evaluation even when literal detection precision/recall is
perfect. Without these annotations, the report measures literal detection only.

Rules contain data and regexes, never Python expressions or executable plugins.
Regex evaluation runs in a bounded subprocess (30 seconds by default; configurable
with `--timeout`). A timeout exports no partial fields. Limits are 200 fields,
2,000 characters per regex, 50,000 observations and a 128 MB result. Narrow rules
rather than accepting clipped data.
