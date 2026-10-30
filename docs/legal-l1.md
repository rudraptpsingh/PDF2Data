# Offline legal extraction layer (L1)

Implementation starts from committed parser `be9d685`, on `codex/legal-l1-build`. `legal_l1.py` wraps the existing PDF/OCR/table/evidence/quality modules. It does not query a database, index documents, infer legal effect, assess client eligibility or draft an answer.

## Run

Install the existing `[pdf,test]` extras, then:

```sh
PYTHONPATH=src python -m local_vector_batch legal-l1 \
  --manifest source-manifest.json --output out/legal-sources --ocr off --json
PYTHONPATH=src python -m local_vector_batch verify-legal-l1 \
  --output out/legal-sources/example-order --json
```

Use `--resume` to verify and reuse an identical immutable package. Changed source declarations, configuration or parser code require a new output directory. `--ocr auto` delegates to existing local OCR; `off` is the deterministic native-only default. OCR failure, blank/unreadable content and unresolved visual regions stay held.

By default, success means package integrity and complete selected-text accounting, **not content approval**. Use `--require-content-ready` to return exit 3 if any document contains a page that the existing quality policy has not accepted. Missing declared source-family members or failed documents also return exit 3. Exit 2 is a malformed request. No exit code grants legal review or publication approval.

Input schema: `schemas/legal-l1-source-manifest.schema.json`. Source IDs must be unique and safe; SHA256 is mandatory. A local source must match that hash before processing and again after capture. URLs are declarations recorded from upstream acquisition receipts, not fetched or independently authenticated by this command. Expected page counts are checked against the original. Source role/date/legal status remain unresolved.

```json
{
  "schema_version": "powerlex.legal-l1.v1",
  "sources": [{
    "id": "example-order", "path": "/absolute/original.pdf",
    "sha256": "<64 lowercase hexadecimal characters>",
    "family": "interim_order", "expected_pages": 16,
    "url": "https://issuer.example/order.pdf"
  }],
  "families": [{"id": "matter-context", "required_source_ids": ["example-order", "later-order"]}]
}
```

The deliberately missing later order is exposed; the package does not assert matter currentness. Optional `families` describes declared dependency inventory only. An empty inventory cannot establish that all legally necessary documents have been discovered.

## Outputs at each step

| Step | Artifact | Meaning and validation |
| --- | --- | --- |
| Acquisition | `original.pdf`, `source-receipt.json` | Captured original bytes, expected/actual hashes, source declaration, page count and code/dependency/config fingerprints |
| Existing extraction | `extraction/` | Original evidence-package contract: selected pages, raw tables, date/number/role/condition candidates, page inputs and exact hashes |
| Raw/layout capture | `layout.jsonl` | Uncapped native text, pypdf raw text, pypdf layout text, glyph stream, native character boxes and explicit raw-to-selected edit ranges |
| Candidate structure | `structure.json` | Declared-family regions, separate margin candidates, cross-page paragraph candidates, exact source spans and children; never reviewed legal meaning |
| Visual/text quality | `quality.json` | Existing source-raster, independent words/numbers/operators, script and table policy outcomes for every page |
| Repair routing | `repair-queue.jsonl` | Every held page, source/text hashes, failed/missing evidence and existing policy repair routes; awaiting proof, not completed repair |
| Unit eligibility | `readiness.json` | All pages of a unit must pass source quality; boundary/legal review remain separate; no L1 unit is eligible for a legal assertion |
| Structural checks | `validation.json` | Whole selected-text partition, complete body partition, exact anchors, unique versioned parents/children and explicit review exclusions |
| Immutable package | `l1-package.json` | Checksummed sidecars and request identity, linked to the independently verified existing extraction package |
| Batch execution | `run-report.json` | Per-document integrity/readiness/failure and declared family coverage; latest run report, not an immutable history |

The pypdf **layout mode is selected for every native page before comparison**, because raw PDF storage order can put a footer before the body. Both raw pypdf representations remain in the package. A differing layout representation is not selected just because it produces a higher score. No quality thresholds are loosened.

Glyph-to-selected equal blocks are observations, not proof of correct reading order. Reordered/unmapped text is explicit; native glyph boxes never claim to be OCR geometry. Complex scripts, figures, merged table cells and formulas still require their existing independent source-proof routes. A full-page text hash or two engines agreeing is insufficient to establish visual fidelity.

## Structure rules

All selected characters belong to exactly one page region. Headers/footers are retained separately and excluded from candidate paragraph bodies; they are not discarded. A repeated long top line can be a header candidate. A printed `Page x of y`/RoP footer can be a footer candidate. Bare footer numbers are separated only when every page of a multi-page source ends with the matching physical page number. The classification remains reviewable.

Numbered starts form lexical wrappers across page boundaries. Clause 1 and short units survive. Numbering inside captions or quotations is ambiguous and stays candidate-only. Repeated printed labels get distinct **content/version-derived IDs**, including source/page hashes and exact spans; corrected extraction from the same PDF therefore changes parent and child IDs. Child slices reconstruct every body character with no minimum-length filter. Child width is a character slicing parameter, not an embedding-token guarantee; C07 must apply the existing tokenizer budget later.

Fourteen profiles identify candidate navigation regions for regulations/drafts/notices/EMs, judgments/interim orders/RoPs/approvals, pleadings, listings, contracts, tenders, procedures and financial evidence. The real-source pilot exercises seven declared families. Generic profile tests are not a validated legal handler or holdout for the other families.

## Consumers and failure handling

Validate `l1-package.json` and the original before reading a package. Consumers must check **`readiness.json` plus separate legal review and publication gates**. Do not import the nested legacy `embedding-input.jsonl` based only on its original extraction report's embedding-ready flag: that flag predates the stricter L1 source-quality ledger.

A failed extraction is quarantined as an explicit batch failure and never atomically publishes a partial package. Replay checks all hashes/joins. A changed source, stale parent, clipped span, missing region, altered raw geometry, inconsistent quality count or promoted candidate is rejected. Repair writes a new extraction version; it cannot silently rewrite an immutable package or carry an old review forward.

Offline L1 development uses only an owned filesystem workspace and supplied originals. It does not satisfy C00's remote D1/Vectorize/private-state isolation gates. Those remain mandatory before integrated imports, retrieval, client assessments or exports. The Cloudflare and UI baselines are checked out for later integration but are unchanged by this parser feature.

## Reproduce the real-source example

```sh
PYTHONPATH=src python scripts/run_legal_l1_example.py \
  --source-root /Users/rp/.codex/worktrees/artifact-review/Powerlex/docs/research \
  --output out/legal-l1-v1
```

The archived download receipts pin nine public PDFs/98 pages. The script verifies their original SHA256s and page counts, runs the actual extractor/quality gate, reopens every resulting package, and evaluates 15 source-based assertions. It writes the full execution receipt plus separate real regulation and interim-order output examples. `--resume` reruns the assertions against verified existing immutable artifacts.

Tests include corruption, clipping, duplicate labels, missing family members, wrong hashes/page counts, replay, interrupted processing, Unicode retention, blank content, candidate promotion and cross-page conditions. The optional `docling_core` coordinate test remains skipped when that separate dependency is unavailable. A source-quality hold is retained as a hold; the example's integrity/structural checks do not override it.
