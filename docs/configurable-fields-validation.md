# Configurable fields: validation, 31 October 2026

This change adds configurable field detection and evaluation; it does not change
the OCR engine or establish a new general PDF parsing accuracy score.

## Executed checks

- Full repository suite: **1,229 passed, 7 skipped** on the local Python 3.12 runtime.
- Built the wheel and installed it without dependencies in a clean Python 3.13
  environment. `fields init` and `fields catalog` worked using the packaged rules.
- Exercised extraction, queries, exact-reference evaluation, negative references,
  stale rule hashes, changed output files, normalization mismatches and the
  nonzero evaluation exit code.
- Confirmed new field IDs work by editing JSON rather than parser code.
- Checked Unicode source offsets, invalid dates, exact numeric normalization,
  unit hints, family selection and unresolved formula observations.
- Tested a pathological regex: the worker was terminated by its time budget and
  exported no partial results.
- Verified that configured workspace exports expose fields through MCP, retain
  source/quality state, and reject modified export artifacts.

## Real source check

A six-page CERC explanatory memorandum was processed from its existing immutable
L1 source package. The readable export retained all six pages, two source tables,
one existing quality flag and the configured field bundle.

The first example reference regex incorrectly matched `No` inside words such as
`notification`. An original-page review exposed the false positives. The shipped
template now requires an identifier-label boundary and uses physical page 1 for
document reference/date candidates. The date rule requires a colon, separating
the header date from ordinary prose mentioning earlier dated instruments.

The corrected template produced one document-reference candidate, one labelled
numeric date and 12 percentage occurrences. Two independently inspected header
fields were annotated with source-page hashes and exact offsets. Evaluation
returned **2 TP, 0 FP, 0 FN**, with the date normalization also checked. The other
two field definitions remained unlabelled and were excluded from accuracy metrics.
This is a development check on one source, not an independent holdout benchmark.

## Limits

Rules observe the available extraction; they cannot restore missing OCR, prove
table relationships or reconstruct two-dimensional formulas. Formula candidates
remain unverified, and configuration cannot enable calculations or approve legal
applicability. Different document categories need representative reviewed cases,
including negatives, before claiming field precision/recall for that category.

The existing 33-page BESS notification also allowed separate field extraction.
Its full readable export fails an existing retained-table-span validation in both
the baseline checkout and this feature checkout. That pre-existing export issue
is separate from rule extraction and is not claimed fixed by this change.
