# PDF2Data feedback controller

The controller inventories every original, measures the extraction, selects a
bounded repair, then measures the full document again. It uses the existing
`pdf-extract`, `pdf-quality`, quality-route rules and `pdf-repair-queue` tools.
It adds orchestration and a durable ledger, rather than another parser or model.

## Control model

```text
Original PDF D ──> extraction F ──> candidate y(t) ──> source checks
                         ↑                              │
                         │                              ↓
                   repair action u(t) <── policy <── residual e(t)
                                                        ↑
                                         source-bound reviewer feedback
```

```text
y(t)      = F(D, parser_version, reader_configuration, u(t))
e(t, k)   = loss_k(reference_k(D), y(t))
u(t + 1)  = policy(e(t), source evidence, available repair routes, budget)
```

The reference is different for each check. Original pixels/glyph geometry
support coverage; independent readers support disagreement detection; a
reviewer's source-bound transcription supports a literal-value comparison.
**When a trustworthy reference is missing, the error is unknown, not zero.**
Independent reader agreement is not ground truth.

The recorded loss vector is:

```text
[failed required checks, source-review errors, unknown evidence checks]
```

Each component also retains its actual state. Quantitative observations include
`1 - word_recall`, `1 - word_precision`, `1 - word_order`, unaccounted source-ink
pixels, and exact ordered number/operator differences. Number order changes are
distinguished from lost/added values by checking the token multiset. Cell
feedback returns the expected literal, observed literal, original source box
and a mismatch/unknown result. These comparisons are diagnostics, not an
overall percentage accuracy score.

Do not use a weighted average that lets good paragraphs hide a wrong amount,
negation, formula operand or table association. The current controller is a
discrete policy over measured errors, not a PID controller: OCR/layout repair
actions are categorical and the required reference is often partly unknown.

## Commands

An inventory is JSONL, one original per row. `package` is optional; if omitted,
the controller invokes `pdf-extract` in its own new attempt directory. Existing
L1 packages are resolved to their immutable extraction subdirectory.

```json
{"source_pdf":"/absolute/source.pdf","source_pdf_sha256":"<64 hex characters>","package":"/absolute/l1-package"}
```

```sh
pdf2data pdf-control init --input ./inventory.jsonl --output ./control --json
pdf2data pdf-control run --output ./control --max-documents 10 --max-rounds 2 \
  --ocr off --json
pdf2data pdf-control status --output ./control --json
pdf2data pdf-control tasks --output ./control --limit 25 --offset 0 --json
pdf2data pdf-control feedback --output ./control --references ./feedback.jsonl --json
```

`--ocr off` is the default. An explicit supported local OCR option can be used
for originals without an extraction. No cloud service is called. The supported
staged repair queue still requires the correct source-bound receipts; merely
selecting an OCR engine does not manufacture those receipts or table proof.

An inventory may also supply the existing repair queue's `native_receipts`,
`column_order_receipts`, `region_receipts`, `independent_receipts` and
`review_sidecar`. Paths are resolved against the inventory; their content hashes
participate in the next-attempt fingerprint. Re-inventorying can register new
proof without replacing the immutable source package. Byte-identical originals
share a document job while retaining every source-file alias/declaration.

For archive sources explicitly marked `source_receipt_verified: false`, the
controller records `source_binding_required` and excludes normal processing
until a source receipt is supplied. It does not silently drop the source.

## Feedback contract

Whole-page feedback uses the existing calibration contract plus reviewer/scope:

```json
{
  "source_pdf_sha256": "<original PDF hash>",
  "page": 5,
  "page_text_sha256": "<reviewed extraction text hash>",
  "scope": "whole_page",
  "acceptable": false,
  "split": "calibration",
  "reviewer": "<reviewer identity>",
  "visual_evidence_kind": "original_pdf",
  "visual_evidence_sha256": "<same original PDF hash>"
}
```

Cell feedback instead supplies `scope: "cell"`, `source_bbox: [x0,y0,x1,y1]`
in original PDF points and `reference_text: "Not effective"`, with the same
source, page, extraction-version, reviewer and original-PDF evidence fields.
The box must identify exactly one existing source cell. Subsequent candidates
may be compared to that literal only at the unchanged, unique original box.
Missing/ambiguous boxes remain unknown. A confirmed cell **never approves its
whole page**.

The controller checks source bytes and the selected extraction version at
feedback intake. The reviewer asserts that they visually checked the indicated
source; a valid JSON record does not itself prove that a human looked at it.
Positive labels do not bypass machine coverage checks or create legal approval.
Changed feedback requires `supersedes_feedback_sha256` and `review_reason`;
the prior record remains in `feedback_history`.

Calibration feedback can change the work queue. Holdout labels are rejected
from feedback intake: use the existing `pdf-quality --references` evaluation
path to evaluate a frozen policy. A holdout used to fix the system must be
retired from subsequent independent performance claims.

## Repair, recheck and stopping rules

1. Validate original bytes and every physical page's extraction version.
2. Run fresh source-aware quality checks and record the residual vector.
3. Select an existing repair route for an affected page. Repairs are staged
   in a separate directory; originals and watched packages are not modified.
4. Recheck **all physical pages** of the candidate. This retains cross-page
   context and detects damage outside the repaired page.
5. Retain a candidate only if at least one check improves and no check worsens.
   Losing page coverage, a known review, a confirmed cell, or turning a known
   failure into unknown is a regression. Otherwise keep the prior extraction.
6. Stop that document when checks pass, the action budget expires, or no
   executable route has the required evidence. Continue the remaining corpus.

No-change attempts are skipped until source/package, code, feedback, proof
receipts or configuration changes. Partial parser attempts retain their logs
and retry in a new directory. A failed document does not make the remaining
inventory wait. Each subprocess has a 600-second bound; one run is also bounded
by document count and round count. A large single-document quality pass may
require a future page-window scheduler; the current controller does not claim
constant memory or an unlimited processing budget.

States include `queued`, `processing`, `extraction_checks_passed`,
`needs_source_review`, `repair_budget_exhausted`, `processing_failed`,
`source_blocked` and `source_binding_required`. Machine-checked pages,
machine-passed pages, reviewed pages/cells and unlabelled pages are reported
separately. `needs_source_review` is an active evidence task, not a global stop.
New feedback/proof enters `feedback_pending`/`evidence_pending` and is scheduled
before the unprocessed backlog, so a correction does not wait behind the corpus.

## Durable outputs

- `controller.sqlite`: source inventory/aliases, retained versions, attempt
  history, active feedback and feedback history.
- Progress counters are aggregated from compact numeric rows in SQLite; status
  does not load every document's page/cell diagnostics. Detailed results remain
  in hash-bound attempt files.
- `attempts/`: exact CLI commands/stdout/stderr, quality receipts, residuals,
  candidate measurements, regression decisions and repair-queue state.
- `status.json`: latest aggregate progress; `pdf-control status` can read
  committed progress while the writer runs.
- `events.jsonl`: document-completion events with source hashes and timestamps.

`pdf-control tasks` returns source/page/version links, actual token/cell
differences and the next repair actions. Its result snapshots are hash-checked.
Use `--include-unlabelled` to also list machine-passed pages without a whole-page
source review. `--limit`/`--offset` provide bounded navigation without loading the
whole corpus into memory.

The controller does not change a database/index publication pointer, approve
company facts, establish effective law or certify legal conclusions. Those
consumers must verify the selected source version and their own semantic gates.
