# Bounded English word quantities

Quantity rules can explicitly opt in to `quantity_normalization`. Omitting it
keeps the existing numeric-literal behavior. This affects typed candidates only;
selected page text, regex matching, offsets, native parents, comparison/basis
context, quality holds and approval gates remain unchanged.

| Mode | Accepted value |
| --- | --- |
| `english_cardinal_0_100_v1` | English cardinal words from zero through one hundred |
| `english_cardinal_percent_0_100_v1` | The same words followed by a separately captured printed `percent`, `per cent` or `%` |

Single words, tens followed by one through nine, and `one hundred` are supported.
Case is ignored. A compound tens/unit numeral accepts whitespace or one ASCII
hyphen; `one hundred` accepts whitespace. This is a bounded grammar, not a lookup
of document answers. Larger numbers, fractions, ranges, mixed digits/words,
conjunctions, Unicode hyphens, corrupt glyphs and unknown languages remain null
with `unsupported_word_numeric_literal`. No OCR repair or model inference occurs.
V3 values crossing native source-span boundaries remain null with
`multipart_scalar_normalization_not_established`.

```json
{
  "id": "ownership",
  "type": "quantity",
  "pattern": "not less than (?P<value>[A-Za-z]+(?:[-\\s]+[A-Za-z]+)? (?P<unit>per cent|percent|%)) of the ownership",
  "unit": "percent",
  "quantity_normalization": "english_cardinal_percent_0_100_v1"
}
```

Use the normal [extraction rule envelope](configurable-fields.md). For native
parents, add the explicit [v3 scope and selectors](multipart-fields.md). Keep
patterns specific enough to retain the required comparison and basis context.
The example is a configuration pattern, not a universal legal classifier.

For `twenty-six per cent`, the candidate is
`{"numerator":"26","denominator":"1"}`. This is the printed percent magnitude;
it is **not** a dimensionless `26/100` ratio. The `unit` group must be the exact
suffix inside `value`; it retains its own source span. Word units need whitespace
before them; `%` can be adjacent. Missing, unrecognized or incorrectly bound unit
witnesses leave the candidate null.

If an existing rule selects only `twenty-six`, use
`english_cardinal_0_100_v1` to preserve that exact value span. A caller's
`"unit":"percent"` remains a `configured_unit_hint`; it does not create a
source-printed unit witness. Printed context remains in `match_source_span(s)`
and the retained native parent. Separately review that context when adopting a
typed value downstream.

Add `expected_typed_candidates` to [check rules](field-check-rules.md):

```json
{
  "id": "ownership",
  "field_id": "ownership",
  "count": {"min": 1, "max": 1},
  "expected_literals": ["twenty-six per cent"],
  "expected_typed_candidates": [{"numerator":"26","denominator":"1"}]
}
```

Candidates are compared in observation order. An explicit `null` tests a held
or unsupported value; it does not mean absence. Numeric assertions apply only to
quantity fields and require a positive count. Keep source/rule/package SHA pins
and literal/page assertions alongside numeric expectations. Checks verify
configured assertions, not completeness or legal/calculation acceptance.

Consumers must adopt a new immutable producer release, version the rules/checks,
re-extract into new field bundles and requalify those exact artifacts. Preserve
old bundles and failed reports. A source change, such as a Hindi illustration
note saying `9%` while a table or English note says `3%`, remains a conflict;
numeric normalization cannot settle it.

The [real-source receipt](validation/word-quantities-real-source-001.json)
qualifies seven existing word-valued observations from two retained PDFs. The
[engineering controls](../tests/test_word_quantities.py) separately test grammar,
nulls, mutation failures, printed-unit binding and unchanged source holds. This
bounded qualification is not full-pipeline accuracy or operative consolidation.
