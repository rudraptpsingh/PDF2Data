# Source-bound native proof repairs

These CPU-only commands preserve original PDF and selected-text bytes. A resolved review question is not a whole-page quality pass or legal approval. Repaired source-quality L1 versions retain the original extraction configuration and separately bind the later proof tool, parent manifest and proof implementation.

```sh
pdf2data native-logical-repair --manifest review-manifest.jsonl \
  --source-runtime /absolute/original/frozen/runtime --output new-logical-repair --json
pdf2data native-table-cell-revalidate --package original-l1 \
  --source-runtime /absolute/original/frozen/runtime --feedback table-cell-feedback.jsonl \
  --dossier source-raster-dossier.json --page 5 --output new-cell-revalidation --json
pdf2data native-table-region-reproof --package original-l1 \
  --source-runtime /absolute/original/frozen/runtime --feedback table-cell-feedback.jsonl \
  --dossier source-raster-dossier.json --page 5 --output new-region-reproof --json
pdf2data verify-native-quality-repair --package new-region-reproof/packages/source-id \
  --source-runtime /absolute/original/frozen/runtime --json
pdf2data targeted-native-reproof --package original-l1 \
  --source-runtime /absolute/original/frozen/runtime --pages 1 2 --output new-sidecar --json
```

`native_token_spacing.LEARNED_REPAIR_REGISTRY` declares split numeric tokens, split operator/native words and the existing constrained PDFium control-hyphen mapping. Layout-space reconstitution requires a unique complete physical source-line context, matching PDFium/native glyph characters, small contiguous native token gaps and overlapping vertical glyph geometry. Explicit source spaces, true line breaks, wide gaps, table tokens, conflicting characters/numbers and ambiguous repeated contexts remain unresolved. A PDFium newline before a raised ordinal suffix is accepted only when each boundary is proved to belong to the same physical source word. Only the independent reader's generated whitespace is removed; selected source text is untouched.

The exact-focus review filter rederives registered proof evidence from the original L1/PDF before resolving an item. Both selected and independent focus spans must fall within one proven native token, and their only difference must be horizontal layout whitespace. The original row hash, exact focus, source identity, automatic rule origin and proof remain in `resolved-audit.jsonl`. A page with other unresolved content remains held. An empty queue says “No unresolved questions in this focused queue”; decision controls are disabled and no whole-corpus completion is implied.

Native table feedback binds the physical source, table version, cell coordinates, both reading hashes and the exact original raster. Duplicate retries are idempotent; conflicting feedback, stale raster/profile/table/cell and corrections requiring changed text reject with terminal job receipts. Human-origin labels preserve the submitted question/reply provenance, not an independently authenticated reviewer account.

Plural native-table region proofs verify every cell/grid membership plus independently read outside-table prose. Literal thin original vector paths and contiguous corner tiles account for grid ink; table interiors are never masked. The detector's existing snap geometry is recorded. Words/numbers/operators are ordered within each cell and prose line, without claiming that interleaved table-column text is prose. Source top-row geometry is retained; semantic header interpretation remains a later review task. All eight quality components must pass before creating a ready new L1 version.

`proof_identity.verify_source_quality_repair` reconstructs registered proof operations and rejects quality mutations outside their exact affected page scope. Matching hashes, a valid JSON document, a closed review queue or an extracted table alone cannot establish source quality or legal correctness. All outputs keep publication and legal review separate; no model or network operation is required.

The source-font review registry in `quality_policy.json` also learns negative
cases. The observed `Devnagari-ChanakyaNormalA` family emits Latin character
codes for Hindi on a real held Gazette page. Its exact family (ignoring a PDF
subset prefix) now requests independent raster linguistic evidence, even when
native readers agree. This registry routes review; it never converts characters
or strips Unicode. Fresh package assessment checks original fonts even with
supplied geometry. Native table region proofs reject this unverified mapping.
Unlisted legacy font families remain outside this observed-family rule. Earlier
frozen proof epochs retain their original behavior and receipts; they do not
inherit the new guard retroactively.
