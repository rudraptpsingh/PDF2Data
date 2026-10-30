# Changes

## Unreleased

- Added resumable source-bound page repair queues, explicit worker-crash retry
  states, model/policy-version attempt keys, and current-page status separate
  from historical attempt counts.
- Replaced fixed OCR worker groups with configurable small page jobs and
  persistent recognizers per process; completion counts unique source/page/text
  receipts and validates full requested coverage.
- Added atomic multi-page repair commits with fresh source quality checks,
  original-version checks, source-review precedence and untouched-page checks.
  Committing local evidence does not declare a remote search index ready.
- Added optional local PDF extraction and a loopback-only drop/review/configure/
  embed/export webpage, including every-page review and bounded advanced
  encoder controls.
- Added local macOS Vision / installed Tesseract OCR, cached Vision compilation,
  garbled native-text detection, retained native/OCR evidence, and explicit
  unreadable/low-confidence blockers.
- Preserved source/page/text hashes, table bounding boxes and blank cells, and
  exact spans for unverified value candidates.
- Added exact pre-windowed `passages` input with owning-chunk offsets and a new
  vector-ID namespace.
- Replaced silent tokenizer truncation with overflow errors in both encoders;
  added effective tokenizer receipts and input-record coverage audits.
- Added local typed-candidate evidence validation / CLI for page hashes, exact
  or explicit whitespace-normalized quote spans, units and role enums. Table
  relationships and semantic roles require review; no facts are auto-verified.
- Added optional tokenizer-offset overflow splitting (default in PDF web jobs),
  bounded token overlap, tokenizer hashes and stable child IDs; safe windows and
  legacy error-policy fingerprints remain unchanged.
- Fixed the module entry point for spawned CPU workers; validated real two-worker
  CPU and Mac GPU runs. Added tests for offset fidelity, coverage gaps, garbled
  text recovery, low-confidence OCR, token overflow and invalid web controls.

Remaining: scanned table row/column reconstruction, institution/date role
validation, clause hierarchy, Windows/Tesseract runtime validation, and broader
extraction/retrieval quality benchmarks. OCR confidence and file integrity do
not establish factual accuracy.
