# Explicit source-space reader witness

The `explicit_source_space_glyph_reader_restoration` rule repairs only a reader's omission of a literal source whitespace character. It never invents a gap from a dictionary or model, rewrites selected source text, deletes a character or merges two words.

The adjacent JSON policy in `src/local_vector_batch/native_cell_space_rules.json` currently permits explicit U+0020 and U+00A0 source objects. All non-whitespace characters and their order must already agree. Every missing separator must be present in the native glyph stream and in the same unique independent pypdf layout context. Its source advance center must lie in the positive gap between adjacent same-line glyphs; its width is bounded by the font size. A font's nominal whitespace advance may overhang neighboring glyph metrics, so advance-center geometry is recorded rather than pretending the nominal box is visible ink.

The mapper refuses cells intersecting a partial non-whitespace original glyph. A trailing space's advance box is not visible ink and does not authorize another character. Cross-line joins, ambiguous contexts, extra reader separators, changed letters/numbers/operators, source spaces absent from the actual glyph stream, or inconsistent space geometry remain held. Raw native, PDFium and third-reader strings and the exact source space codepoint/bounding boxes remain in the proof.

On the actual 40-page cell-disagreement cohort, 67 cells were independently rechecked. The rule resolved three cells across two pages; all 64 other cells retained their holds. No new vision-model calls were needed. The three resolved cells contained omitted native NBSP separators in “If so”, “Appropriate Commission” and “or the”. Whole-page acceptance still requires the existing eight-component source-quality reconstruction and registered verification. A cell-reader proof is not a table semantic, project applicability or legal publication approval.

Tests:

```sh
PYTHONPATH=src python -m pytest -q tests/test_native_cell_space_witness.py tests/test_native_table_cell_spacing.py
```
