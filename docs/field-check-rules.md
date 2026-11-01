# Test extraction with rules

Extraction rules describe what to find. Check rules describe what must be true
of the extracted fields. Both are JSON; adding a case or changing an expectation
requires no Python test code.

Available from PDF2Data `0.2.0rc8` (npm `0.2.0-rc.8`).

```sh
pdf2data fields check --bundle ./fields/document \
  --checks ./checks.json --output ./checks/run-1 --json
```

## Define checks

Replace the hash placeholders with pins from the verified `fields.json`.
Keep expectations separately authored from the original source.

```json
{
  "schema_version": "pdf2data.field-check-rules.v1",
  "id": "filing-checks",
  "version": "1.0.0",
  "target": {
    "rules_sha256": "COPY_THE_FROZEN_EXTRACTION_RULES_SHA256",
    "source_pdf_sha256": "COPY_THE_ORIGINAL_PDF_SHA256"
  },
  "checks": [{
    "id": "filing-duration",
    "field_id": "filing",
    "count": {"min": 1, "max": 1},
    "expected_literals": ["within 30 days of COD"],
    "expected_pages": [[2]],
    "required_captures": ["duration", "trigger"],
    "expected_captures": [{"duration": "30 days", "trigger": "COD"}]
  }]
}
```

The matching extraction rule must declare the same field and capture names.
Use the same extraction-rule hash across a document suite for reusable structural
checks. Pin `source_pdf_sha256` and optionally `source_package_sha256` for
document-specific expectations. Unknown fields/captures and stale pins fail.

| Assertion | Test |
| --- | --- |
| `count` | Required integer minimum and maximum; explicit zero-match negatives supported |
| `expected_literals` | Exact Unicode values, including duplicate occurrence counts; order-independent |
| `expected_pages` | Physical-page list per observation, in observation/span order |
| `expected_typed_candidates` | Exact rational quantity candidates or explicit nulls, in observation order; see [word quantities](word-quantities.md) |
| `expected_captures` | Exact capture values per observation; `null` means an explicitly unmatched capture |
| `required_captures` | Every observation has these nonempty, matched captures |
| `forbidden_fragments` | Literal strings that must not occur in this field's values |

Content assertions require `count.min >= 1`, preventing empty results from passing
without evidence. For a negative case, use only `count: {"min": 0, "max": 0}`.
This means no match under these rules, not proven absence from the PDF.
Fragment checks are field-specific: raw LaTeX or HTML may be legitimate source
text, so do not ban it globally. Browser rendering requires separate UI tests.

## Results and gates

The output directory contains frozen `checks.json` and `report.json`. Each
assertion reports expected, actual and pass/fail; uncovered fields are listed.
Exit codes: `0` all checks pass, `3` assertion mismatch, `2` invalid/stale inputs.
The command refuses existing output directories and writes outside source bundles.

Native bundle verification runs first and cannot be disabled by configuration.
Use a v2 or v3 field bundle produced by the matching captured implementation. Old
bundles require their original runtime; re-extract into a new directory when
adopting a new producer. Never edit a frozen bundle's implementation pins.

Rules permit fixed assertions, not scripts, expressions or executable plugins.
Limits: 2 MB configuration, 200 checks, 10,000 observations per checked field and
16 MB report. Unknown properties, duplicate keys/IDs and invalid bounds fail.
The [JSON Schema](../schemas/field-check-rules.schema.json) supports editor
validation; the runtime additionally validates bounds, identities and bindings.

Passing configured checks does not establish completeness, symbolic formula
correctness, legal effect or permission to calculate. For independently reviewed
source-span precision/recall, also run
[`fields evaluate`](configurable-fields.md#evaluate-against-reviewed-source-annotations).

The repository's [JSON test cases](../tests/fixtures/field-check-cases.json) exercise
positive, missing, duplicate, multilingual, capture-binding and page failures.
Python remains responsible for executing assertions and testing integrity/error
handling; those source gates cannot be weakened by user rules.
