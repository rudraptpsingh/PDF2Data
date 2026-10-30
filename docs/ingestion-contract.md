# Local PDF evidence contract

The extraction bundle is a set of observations for review. It does not assert
that a legal duty is current, a contact is the issuing authority, or a date is
a filing deadline.

Each representation keeps the original PDF SHA, page ID, selected text SHA,
extraction method, and available OCR line boxes/confidence. A candidate value
also carries its exact codepoint span in that selected page text. Native
extraction remains available when OCR is attempted. Table cells preserve blanks
and bounds; they are not filled down into inferred row relationships.

Downstream reviewed records should distinguish:

| Record | Required role / relationship | Evidence needed before promotion |
| --- | --- | --- |
| Institution and contact | issuer, masthead contact, addressee, counsel, petitioner, respondent, or unclassified | Exact page/span and role-bearing text. A `To` block or counsel's address is not issuer evidence. Verified issuer contacts require a masthead or official registry source. |
| Date | issue, publication, effective, hearing, submission deadline, observed, or unclassified | Exact value, precision, timezone when supplied, and role-bearing clause/listing. Keep an `as_of` and validity basis; never turn an archive year into a fictitious day. |
| Number | field, value, unit, subject/category, applicability and document version | Supporting clause/table row, original PDF/page and text hash. A nearby percentage is not enough to attach a number to a legal category. |
| Amendment | modifies, supersedes, clarifies, implements, or references | Explicit source relationship and affected provision/date. Title similarity is a candidate match, not proof of legal force. |

The current tool emits page text, native digital table cells, OCR observations,
and unverified numeric/date candidates. It does not yet extract and validate
institutional role records or legal amendment graphs automatically. Those
should be reviewed projections with their own evidence status, not a relabeling
of all regex matches as verified facts.

A model or OCR system may reconstruct merged cells and propose typed roles or
conditions, but those outputs remain candidates. Check every claimed verbatim
quote against a contiguous source span (record any normalization explicitly),
and review the layout association of values, row headers, column headers,
footnotes and applicability conditions before promotion. A plausible table or
correct aggregate count does not validate every quote or relationship. Model
output must never bypass these checks or silently replace the original evidence.

Embedding receipts have separate meanings: process completion, input record
coverage, tokenizer budget validation, vector shape/hash integrity, and actual
retrieval quality. None substitutes for the others. Query-time references must
resolve vector offsets against the same selected-text hash and original page.
