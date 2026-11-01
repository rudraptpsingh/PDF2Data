# Opt-in named page-field captures

Python0.2.0rc5 adds `pdf2data.configured-fields.v2` alongside the default, unchanged v1 output. Use named captures to retain several source-anchored pieces of a matched clause. Names are configured hints: they do not approve actor ownership, resolve pronouns, execute a Boolean condition or prove applicability.

```json
{
  "schema_version": "pdf2data.extraction-rules.v1",
  "fields_schema_version": "pdf2data.configured-fields.v2",
  "id": "example.condition",
  "version": "1",
  "fields": [{
    "id": "condition.parts",
    "type": "text",
    "pattern": "(?P<value>(?P<actor>Applicant) chooses (?P<left>solar) (?P<connector>or) (?P<right>wind))",
    "capture_groups": ["actor", "left", "connector", "right"]
  }]
}
```

Run `pdf2data fields extract --package L1 --rules RULES --output NEW --json`, followed by `pdf2data fields verify --bundle NEW --json`. Verification is v2-only and reopens the frozen original/source package, rules and producer implementation, then re-derives the output in the bounded extractor worker. Rewriting output and its manifest hash does not authorize a changed capture or approval flag. The verifier accepts a1–600-second `--timeout`; it returns no partial validation on timeout. `fields query` also re-verifies v2 bundles before returning observations. V1 query behavior remains unchanged.

Each observation retains its original `value_literal`, `source_span`, unit/typed candidates and geometry. The additional `captures` object maps every requested name to:

- `value_literal`, exact physical-page/codepoint `source_span`, and `status: matched`; or
- explicit null literal/span and `status: not_matched` for an absent/empty optional group.

Every nonempty capture must lie completely within its observation's `value` group. Captures outside it, including lookaround captures, fail extraction instead of inventing ownership. Overlapping/nested captures are permitted and remain distinct source parts. Up to32 unique named groups per field are allowed; they must exist in the regex and cannot be `value`. Existing200-field,2,000-character pattern,50,000-observation,128MB output and worker-time budgets remain unchanged. These are page-local captures: cross-page conditions require separately preserved context; they are not silently stitched. Cell extraction rejects these page-specific rules rather than ignoring them.

V2 additionally pins the source package hash and four producer implementation files in its output/manifest. Old v2 bundles require their captured producer version for re-verification. Existing default-v1 observation identity and serialized output are preserved by a frozen0.2.0rc4 regression hash. No package/source bytes or quality approvals are rewritten, and no source text is normalized or translated.

The formal output schema is `schemas/configured-fields.v2.schema.json`. Rule schema additions remain in `schemas/extraction-rules.schema.json`. These structures describe extraction evidence and configured associations, not a legal predicate language.
