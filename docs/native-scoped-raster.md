# Native raster accounting inside a held page

An existing `field-witness` request can bind a small source-object/raster
component to an exact original crop and native field observation:

```sh
pdf2data field-witness native-raster --request ./request --output ./raster-1 --json
pdf2data field-witness verify-native-raster --bundle ./raster-1 --json
```

Use the existing [field witness rules and preparation](FIELD_WITNESS.md), with
explicit physical pages, `bbox_pdf_points`, configured-field v2 observations
and a render DPI of 120–240. No model or OCR command is involved. The command
requires known native field geometry wholly inside each selected crop. A field
with missing geometry stays held rather than acquiring an estimated rectangle.

The component compares the complete cached target-page glyph inventory to fresh
original PDF objects. It then checks the selected native glyphs, independent
source paint objects, clipping/transparency, visible text paint and exact crop
pixels. A proved component has zero unaccounted dark pixels under the unchanged
source raster policy. It does not mask a whole paragraph or table interior.
Source tables, nontext paint, clipped glyphs, unsupported text paint, ambiguous
geometry and unexplained ink inside the crop retain a hold.

A finite invalid glyph box outside the crop is retained with its original
glyph index, text, font and full geometry as `outside_held_invalid_geometry`.
Contact with the crop boundary remains held. An unlocalizable box cannot be
excluded. Independent text paint crossing the crop boundary also stays held;
an invalid header cannot be hidden by selecting a convenient substring.

`accounting.json` binds original/package hashes, selected page hash, native
field handles and spans, crop hash/coordinates/DPI, layout record hash, fresh
paint capture hash, source glyph membership, invalid outside regions and exact
source paints. The manifest pins the request, implementation and artifacts.
Verification rederives the result from the original PDF and frozen native
reading. A resealed proposal cannot substitute a different result.

The result names its scope as
`source_object_and_raster_accounting_component_only`. Transcription, language
and layout remain `unproved`; page quality, source approval, legal approval,
calculation permission and publication gates remain unchanged. This evidence
alone does not enable a claim. It does not settle a cross-page clause, formula
meaning, bilingual equivalence or applicability.

The RERC development trial preserves all 54 held pages. Four bounded English
regions pass this component, including the English text below the bilingual
boundary and a clause above a Hindi printer footer. An overlapping Hindi header
and an illustration with unlocated native field geometry remain held. A
separate ordered-operator whitespace defect is not evidence for language or
raster acceptance. Originals, raw text, tables, structures and the original job
remain frozen; no merge, release or consumer adoption is implied.
