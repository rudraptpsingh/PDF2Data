# Native regulatory candidate validation — 1 November 2026

Producer-only validation of `pdf2data.regulatory-candidates.v1`. No model/GPU
requests, database writes, consumer integrations or source parser changes were
made in this increment. The configurable field-rule commands already on main
remain compatible.

## Executed checks

| Check | Result |
| --- | --- |
| Full Python suite, Python 3.12/macOS | 1,275 passed, 7 skipped |
| New native candidate tests | 40 passed |
| npm launcher tests | 5 passed |
| JSON Schema Draft 2020-12 | Schema checked; actual real-source bundle validates |
| Clean wheel | Built and installed without dependencies; both profile/field template commands work |
| Installed wheel with PDF extra, outside checkout | Real source extraction, verification and 57-case evaluation pass |
| Source-only development cases | 57 passed, 0 failed; not independent corpus accuracy |

New tests exercise multi-operation frames, earlier/replacement values, nested and
unbalanced quotes, cross-page exact anchors, all/any/exclusion candidates, rates
and scales, date/factor literals, heading/target disagreement, insertion anchors,
transitions, complete native-unit/table accounting, blank cells and unsupported
script gaps. Custom profile vocabulary is tested without parser edits.

Negative tests reject invalid captures/regexes/budgets, stale profiles/sources,
modified source spans, invented unit IDs and unsupported claims even after
artifact checksums are rewritten. Approval promotion fails in both the native
manifest and candidate payload. Worker timeout/record-limit tests fail without
publishing partial candidate bundles. Evaluation rejects malformed, empty,
duplicate or stale references and returns exit 3 for wrong expectations.

## Real source

Ten-page CERC Connectivity/GNA Fourth Amendment draft, original PDF SHA-256
`d4c691b36a175c2a8207310e65cd28da3033273a7bbadd8a026465fa4dd23e79`.
Its package/profile/implementation hashes and case results are captured in the
[machine-readable receipt](benchmarks/receipts/regulatory-candidates-gna4.json).
The [source-bound development labels](../tests/fixtures/gna4-regulatory-development.json)
are agent-authored expectations, not independent human gold.

The source text and rendered originals were reviewed for operation frames,
cross-page passages, the illustration table, periods/rates, exceptions,
transition qualifications and historical-publication notes. This review found:

| Observation | Actual output/limit |
| --- | --- |
| Retained preamble plus 27 numbered amendment items | All 28 native units accounted for; 43 native children retained by their parents |
| Multiple instructions in item 19.3 | Substitute and insert emitted separately; 42 operation candidates total |
| Other candidate groups | 34 values, 54 conditions, 1 transition, 1 commencement, 262 reference mentions |
| Condition trees | 52 unknown roots and 2 bounded all/any join candidates; semantic ownership remains unreviewed |
| Page 4 illustration | Entire 4×5 native grid retained, including blank last remarks cell; ownership/eligibility arithmetic remains unresolved |
| Item 13.1 | Earlier 18-month text and replacement 6-month text separately anchored |
| Item 14.6 | Inserted `PPA or` retained; `land or Financial closure` is not treated as deleted old text |
| Item 6.1 | Heading Regulation 5.2 and operation Regulation 5.1 disagreement flagged |
| Items 7.3, 9.1, 18.1 | Unbalanced source quotation does not become an invented complete fragment |
| Item 25.1 | Transition retains clearing-dues qualification and relative deadline window; actual deadline is null |
| Page 10 historical notes | Draft document role does not turn every historical edition/date into proposed law |

An [actual item 13.1 record](examples/regulatory-amendment-candidate.json) shows
the full native IDs, codepoint evidence, source hashes, roles and unresolved
dependencies. It is an output sample, not a replacement for its original bundle.

The native page-quality counts remain **9 auto accepted, 1 repair required**.
Content readiness, legal review and publication are not promoted. Existing
source selection can include a repeated footer inside a cross-page unit; this
stage preserves it exactly rather than silently cleaning evidence. It cannot
recover missing OCR or establish source completeness.

## Reproduce

With the exact original L1 package available, use the supported CLI:

```bash
pdf2data regulatory extract --package /path/to/pinned-gna-draft \
  --output ./gna4-candidates --json
pdf2data regulatory verify --bundle ./gna4-candidates --json
pdf2data regulatory evaluate --bundle ./gna4-candidates \
  --references tests/fixtures/gna4-regulatory-development.json \
  --output ./gna4-evaluation --json
```

The labels are pinned to this package/profile version. Different extraction or
profile bytes require fresh source-reviewed labels; editing hashes to hide a
source/expectation mismatch is not valid evaluation.

## Remaining gates

This is one development source plus focused synthetic negative cases. It does
not measure recall across unseen legal categories, Hindi semantic extraction,
complete Boolean eligibility, table/header/footnote ownership, external-version
resolution or computation correctness. The 57/57 result must not be advertised
as 100% legal extraction accuracy. Independent source annotations and held-out
documents are required before consumer semantic gates can pass. Map/Query/Impact
adaptation remains a subsequent owner-coordinated step.
