# Cross-page fields

Fields v3 matches an explicit regex within one retained native source unit.
Each value and capture keeps its ordered physical-page spans. A native unit is
a candidate parent, not proof that a legal condition is complete.

Available from Python `0.2.0rc9` / npm `0.2.0-rc.9`. V1 and v2 rules keep their
page-local interpretation. Opt in explicitly; consumers must support the v3 wire.

```json
{
  "schema_version": "pdf2data.extraction-rules.v1",
  "fields_schema_version": "pdf2data.configured-fields.v3",
  "id": "project-timing",
  "version": "1",
  "fields": [{
    "id": "cod.trigger",
    "type": "text",
    "scope": "native_unit_candidate",
    "pattern": "(?P<value>COD within (?P<duration>two months) from the date of[\\s\\S]{0,100}?(?P<trigger>effectiveness of GNA))",
    "capture_groups": ["duration", "trigger"],
    "pages": [1, 2]
  }]
}
```

Use the actual source wording. Optional `native_unit_ids` and `native_unit_labels`
select exact parents; unknown selectors fail. They never merge separate parents.
The page filter applies to every part of the complete match; it cannot skip an
unselected middle page. V3 supports regex only, including literal formula fields.

```sh
pdf2data fields extract --package ./source --rules ./multipart-rules.json \
  --output ./fields-v3 --json
pdf2data fields verify --bundle ./fields-v3 --json
pdf2data fields query --bundle ./fields-v3 --field cod.trigger --json
pdf2data fields check --bundle ./fields-v3 --checks ./checks.json \
  --output ./checks-v3 --json
pdf2data fields evaluate --bundle ./fields-v3 --references ./reviewed.json \
  --output ./evaluation-v3 --json
```

## Output contract

| Field | Meaning |
| --- | --- |
| `source_spans` | Exact ordered value parts: page, page hash, Unicode offsets and quote |
| `match_source_spans` | Full regex match context, independently mapped |
| `captures` | Named capture spans; unmatched captures are explicitly null |
| `native_parent` | Unit identity, pointer, all parent spans, dependencies and quality observations |
| `source_seams`, `match_seams` | Join locations and hashes/lengths of gaps outside the native parent's spans |
| `geometry_parts` | Source geometry per part; missing geometry remains null |
| Source/rules/producer pins | Exact package, PDF, configuration and implementation identities |

There is no flat `source_span` alias. A consumer cannot cite a cross-page value
as if it appeared on its first page. MCP's field page filter selects records
touching that page and retains all their parts, with an explicit scope label.
The current source-image `field-witness` command remains v2-only.

The indexing projection concatenates native spans without inserting separators
or normalizing characters. It can retain source furniture or expose joined words
when the native spans contain no boundary whitespace. Omitted native-parent gaps
remain visible in seam metadata; this extractor does not classify or repair them.
Reopen original spans for display rather than presenting the projection as a
reconstructed authoritative sentence.

## Validation and limits

Verification re-derives fields from the pinned source and rules in a bounded
worker. Rehashed edits to parents, values, captures, seams or approval flags fail.
Existing bundles require their captured producer implementation; new adoption
uses fresh output directories and does not modify old jobs or source packages.

V3 evaluation references pin `source_pdf_sha256`, `source_package_sha256` and
`rules_sha256`, with `reviewer` and `reference_scope`. Each field's `expected`
list contains `native_unit_id`, `source_spans` and `value_literal`. Spans use the
same source-addressed shape as the output. Optional `captures`, `typed_candidate`
and `unit_literal` test those values too. Annotate independently from the source.
Explicit empty expected lists remain negative cases, not absence proofs.

Budgets: 10,000 native units, 500,000 characters / 1,000 parts per unit, 10 million
projected characters, 50,000 observations, 128 MB output and a configurable
1–600 second worker timeout. Exceeding a budget fails without partial output.

Multipart scalar normalization remains unknown. A formula field preserves its
literal; it does not establish operators, grouping, units or permission to run a
calculation. Page-quality holds, native-parent uncertainty and unreviewed legal
conditions remain in the output. A zero-match result is not proven absence.

The [development cases](../tests/fixtures/multipart-source-cases.json) cover the
October COD/GNA condition, July state-deviation wording and July procedure
continuation. They are three source-text checks, not a corpus accuracy score or
legal acceptance. Original source and review states remain unchanged.
