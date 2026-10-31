# Footer mapping validation — 31 October 2026

The six-page regulatory memorandum had footer text in its selected page text and
footer blocks, but no footer overlay. A PDF text row contained invisible trailing
space glyphs extending to x=666.842 on a 595.32-point-wide page. Including those
spaces in its box caused the physical-page geometry validator to reject it.

Line matching still uses the complete literal source line. Display geometry now
uses non-whitespace glyph bounds. Known multiline headers and footers also retain
their exact-line geometry and font metadata in `document-blocks.json`. Visible
out-of-page glyphs remain invalid; they are never clamped or silently discarded.
The original selected text and its codepoint spans remain unchanged.

Native margin observations distinguish Header, Footer and Page no. They retain
the original glyph text, page coordinates, font names and whether their text
aligns with the selected reading. Repeated margin content is a classification
candidate, not a legal or transcription approval. Alignment and indentation
metadata also remain explicitly unverified candidates.

## Real execution

- Source SHA-256: `17b7dd0fe13e4e4f1ca5d4793c7485d66bb5e1b527f9b846a44564577f4d49ff`.
- Workspace run: `2dee08027abd404d934eabfea1858ebc`.
- Each physical page has one located footer block and a separate printed page-number overlay: **6/6 pages**.
- Page text and both table payloads exactly match the prior run.
- Quality outcomes match the prior run: five accepted pages, one repair-required page. Quality policy hashes/components reflect the preceding proof-rule merge and are not byte-identical to the older run.
- The literal source misspelling `Explanaotory` remains unchanged.
- All processing used the workspace's existing `legal-l1`, `verify-legal-l1` and `export-readable` CLI sequence.
- `pdf-visualize` generated the page-one preview from the new manifest-verified output, without recognition reruns.
- An in-memory DOM check verified footer hover text, page-number selection and a parsed pane outside the source canvas. This was not browser visual QA.

Local receipts: `out/footer-mapping-validation.json`,
`out/footer-mapping-dom-validation.json`, and
`out/footer-mapping-preview-v1/manifest.json`.

## Tests and limits

Regression cases cover invisible whitespace outside the page, multiline footer
source spans, and visible out-of-page glyphs remaining unresolved. The full suite
passed **1,074 tests**, with one optional `docling_core` dependency test skipped.
This demonstrates the repair on this native-text PDF. It does not establish
footer recognition accuracy on scanned or bilingual documents, nor a higher OCR
benchmark score. Previously exported bundles stay unchanged; use a new export
or the new workspace run to see updated overlays.
