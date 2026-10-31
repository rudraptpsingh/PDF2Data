# Native regulatory candidates

An optional, local stage over an already verified L1 package. It adds structured
amendment, condition, value, transition, reference and commencement **candidates**
without OCR, models, database writes or a consumer graph dependency. The native
protocol is `pdf2data.regulatory-candidates.v1`; its schema is in
[`../schemas/regulatory-candidates.schema.json`](../schemas/regulatory-candidates.schema.json).

For arbitrary metadata fields and document families, use
[configurable fields](configurable-fields.md). Those rules support adding new
field IDs, searching bundles and field precision/recall evaluation. This stage
adds relationships between a fixed set of regulatory candidate groups; it does
not replace the field-rule protocol or claim universal semantic extraction.

## Install and run

This increment is on the source branch `codex/pdf2data-native-semantics`, not the
published npm release. Install that source in a virtual environment:

```bash
python -m pip install 'PDF2Data[pdf] @ git+https://github.com/rudraptpsingh/PDF2Data.git@codex/pdf2data-native-semantics'
pdf2data regulatory init --output ./regulatory-profile.json --json
pdf2data regulatory extract --package ./l1/source-id \
  --profile ./regulatory-profile.json --output ./candidates/source-id --json
pdf2data regulatory verify --bundle ./candidates/source-id --json
pdf2data regulatory read --bundle ./candidates/source-id --group amendments --json
pdf2data regulatory read --bundle ./candidates/source-id \
  --candidate-id regobs-REPLACE_WITH_RETURNED_ID --json
```

Use `legal-l1` first to produce the immutable source package. Each candidate
bundle contains `profile.json`, `candidates.json` and `manifest.json`. Output
directories must be new and outside their source package. Read/verify reopens
the original package and re-derives candidates with the captured implementation;
it rejects stale source/profile/code and edited associations even if an artifact
checksum was rewritten. Keep the original package available at its captured path.
There is no relocation or cross-implementation migration command in v1.

## Configure the profile

`init` copies the packaged English regulatory starter profile without overwriting
an existing file. Change its ID/version when adapting vocabulary or document
categories. Profile bytes are hashed and frozen with each output; changing rules
requires a new candidate bundle and new evaluation references.

| Profile setting | Behavior |
| --- | --- |
| `operation_pattern`, `operation_names` | Match amendment cues and map to insert/add/substitute/delete/unknown |
| `subitem_pattern` | Match numbered frames using named `label` capture |
| `quantity_pattern` | Capture literal `value`, printed `unit`, optional `scale` |
| `word_numbers`, `scale_candidates` | Explicit number/scale candidates; scale is retained separately |
| `condition_pattern`, `comparisons` | Observe condition introducers and adjacent/nearby comparison cues |
| `boolean_patterns` | Require named `left`, `operator`, `right` source captures; observed and/or becomes all/any candidate |
| `reference_pattern`, `parent_instrument_pattern` | Observe exact mentions and explicit parent alias declaration |
| `actor_pattern`, `event_pattern`, `transition_pattern` | Candidate subjects, triggers and transition frames |
| `transition_cohort_pattern`, `commencement_pattern` | Observe cohort text and proposed force/publication trigger |
| `factor_pattern`, `date_pattern`, `date_format` | Preserve factor basis and explicitly formatted printed dates |
| `draft_marker_pattern`, `title_pattern` | Separate document role/title evidence from an individual assertion's role |
| `language_scope`, `max_records` | Declare lexical scope and fail rather than silently clip excess output |

Patterns use Python regex. Required named captures are validated before
processing. A profile is limited to 256 KB; patterns to 4,000 characters;
Boolean/comparison patterns to 32/64; candidates to a configured maximum of
100,000. Extraction/re-verification runs in a subprocess with a default 30-second
budget (`--timeout 1..600`) and a 128 MB serialized result limit. A timed-out
worker emits no partial candidate bundle.

The starter vocabulary is English and energy-regulatory in scope, not a tested
classifier for every jurisdiction or document type. An English-only profile
observing Devanagari retains exact text and marks unit groups as review gaps.
Custom patterns can add vocabulary without code changes, for example:

```json
{
  "quantity_pattern": "(?P<value>\\d+)\\s+(?P<unit>business days)",
  "condition_pattern": "contingent upon"
}
```

These are edits to a complete initialized profile, not a standalone profile.
Unknown units stay quantity candidates; adding a word does not add dimensional
validation or calendar arithmetic. Quote parsing, frame association, currency
cues and the six output groups are implementation behavior in v1. A profile
cannot introduce executable formulas, change the wire schema, prove applicability
or enable legal answers/calculations/publication.

## Evidence and semantics

Every record retains the original PDF/package/profile hashes, native unit ID and
pointer, native child IDs, required parent unit IDs, physical page(s), exact
Unicode codepoint offsets and page-text hashes. Joined native spans retain all
original characters, including source extraction artifacts. These are offsets in
selected page text, not PDF byte offsets or JavaScript UTF-16 indices.

- An amendment frame can emit multiple operations. Closed literal quotes are
  retained separately as old/new fragments; an insertion location is not deleted
  text. Missing or unbalanced quotes remain null with limitations.
- Earlier and replacement quantities are distinguished only by exact fragment
  containment. Rates preserve printed currency, scale and unit separately.
  `Rs. 5 lakh/MW` yields literal `5`, exact rational `5/1`, scale `100000/1`,
  printed `Rs.` and `/MW`; it is not an approved INR calculation.
- Conditions expose source-supported all/any joins and exclusion candidates.
  Unknown Boolean/subject scope remains an `unknown` tree with source components.
  A numeric mention or a successful regex is not a complete eligibility rule.
- References remain unresolved. `dependency_requirements` are candidate readback
  needs; they do not overwrite native dependency lists or establish a version.
- Draft role is separate from assertion role. A historical date mentioned in a
  draft is not automatically a proposed effective date. Proposed commencement is
  not evidence that publication occurred; actual dates/deadlines remain null.
- Every native unit and table is accounted for. `not_detected` does not prove
  absence. Table cells, blanks and existing contexts are retained; page
  co-occurrence is not ownership of a header, footnote or operative rule.

Native quality/readiness is copied unchanged. Record counts are coverage of
observations, not a semantic completeness percentage. Source-quality approval,
legal-answer eligibility, calculation and publication remain disabled. Consumers
must preserve these flags and the original producer artifacts/handles.

## Evaluate a profile

Use separately authored expectations, not values copied from predictions.
References pin `source_pdf_sha256`, `source_package_sha256` and `profile_sha256`.
Declare `reference_scope` (`agent_development` or `human_source_review`) and a
reviewer. Each case selects native records or the whole bundle:

```json
{
  "id": "replacement-window",
  "selector": {"group": "amendments", "subitem_label": "13.1"},
  "expected_count": 1,
  "checks": [
    {"pointer": "/0/old_fragment/literal", "contains": "18 months"},
    {"pointer": "/0/new_fragment/literal", "contains": "6 months"}
  ]
}
```

Use `scope: "bundle"` for explicit JSON-pointer checks of coverage, table blanks,
parent references or quality state. Selectors support group, subitem, native unit
ID/label and operation. Each check requires exactly one `equals` or nonempty
`contains`. Count-only cases can assert zero expected detections as negatives.

```bash
pdf2data regulatory evaluate --bundle ./candidates/source-id \
  --references ./reviewed-cases.json --output ./evaluation/source-id --json
```

Exit 0 means all declared cases passed, 3 means expectation failures, and 2 means
invalid/stale input. Reports record case failures and reference provenance. They
do not claim corpus precision/recall or independent review from development
fixtures. Require representative held-out documents, association/exception labels
and human review before making a category accuracy claim.

See [the real-source validation receipt](regulatory-candidates-validation.md).
Map/Query/Impact integration and automatic semantic approval are deferred.
