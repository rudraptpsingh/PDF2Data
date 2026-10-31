# Document structure and editable Word export

PDF2Data now emits `document-model.json` from the same lossless source blocks
used by its other exports. It is also included in `data.json`. This is a
presentation model with source evidence, not recovery of the original author's
Word file or proof of legal hierarchy.

## What Word teaches us

Word separates the main story from header, footer, footnote and other stories.
The main story contains paragraphs and tables. Paragraphs contain runs with
character formatting. Paragraph styles, numbering definitions and section layout
are distinct from those runs. Sections control page size, margins and related
headers/footers; a PDF page boundary alone does not establish a section boundary.
These distinctions follow Microsoft's
[WordprocessingML structure](https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document)
and [section properties](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.sectionproperties?view=openxml-3.0.1).

The model uses those distinctions without pretending that the PDF retained its
original style names, automatic numbering identity or equation trees.

## Pattern mapping

| Observed source pattern | Representation | Word behavior and limits |
| --- | --- | --- |
| Issuer masthead, location | `masthead` candidate | Separate centred paragraphs when supported by geometry |
| Long opening title, document-type subtitle | `title`, `subtitle` candidates | Named Title and Subtitle styles; original text retained |
| Short section heading | `heading`, candidate level | Word heading style; inferred level remains unverified |
| `1.1`, `1.2` clause paragraphs | `numbered_paragraph`, literal label | Body paragraphs, preserving the label rather than inventing numbering |
| `(a)`, `(i)`, `i.`, bullets | List marker/type/depth candidates | Literal markers with indentation; Roman versus alphabetic ambiguity recorded |
| `Reference:` followed by a list | Reference-list context candidate | Retained list paragraphs; no claim that cited documents have been resolved |
| Document number, date line | Metadata plus inline date candidate | Separate paragraph rather than part of the document title |
| `Regulation 13(2)(a)`, `Petition`, `OM`, `Order` | Typed literal citation candidate with exact source spans | Target stays null pending a resolver |
| Currency, percentages, measurements, durations | Typed quantity mention with exact literal text | No invented exposure, calculation or deadline |
| Equality/inequality expression | Literal formula candidate | Equation-tree recovery and native editable math remain unsupported |
| Bold/italic changes inside text | Source-aligned character runs | Character formatting can change within one paragraph |
| Illustration/table captions | Caption candidate and following-table link | Caption style, kept with the editable table |
| Table rows/cells/merges | Existing table IDs and retained cell spans | Editable cells, geometric column widths, repeating candidate header rows |
| Repeated footer and printed page label | Separate source story and original page evidence | Shared Word footer with PAGE and, where applicable, NUMPAGES fields |
| Varying header/footer text | Separate source story; no common template assumed | Preserved inline pending section reconstruction, rather than discarded |

Native exact-line alignment supplies font family, size, bold and italic run
candidates. If the selected reading cannot align exactly, style remains
unresolved; the text is still present. Indentation and alignment are observed
formatting candidates, not legal relationships. A short last line's width is not
used as the width of a Word text column.

## Model and pipeline

`physical_pages` retain dimensions and element references. `elements` retain
block IDs, exact source spans, text, source-aligned runs, role candidates,
paragraph formatting, inline mentions and table references. `stories` partition
the elements into body, header, footer, whitespace and alternative readings.
`styles` group presentation candidates; `outline_candidates` retain unverified
heading roles. `semantic_sections` remains empty when no section recovery is
proved. Captions link to an immediately following table as a candidate relation.

Both native `export-readable` and region `pdf-structure-export` emit the model.
The latter uses detector page dimensions and records missing native font
evidence as unresolved. The overlay viewer uses model roles; it does not run a
different classifier in the browser. Word export consumes the same model and
existing table payloads, without reading the PDF again.

```sh
pdf2data export-readable --package PACKAGE --output NEW_OUTPUT \
  --docx-python /absolute/path/to/python-with-python-docx --json
```

This writes `document.docx`, the source model, the other readable exports, and
`document-word-validation.json`. Word paragraph text reflows PDF line wrapping;
original line breaks and offsets remain in the source model. There is no
automatic renumbering of legal clauses. A varying footer is not silently treated
as a shared footer. A rendered Word page number describes the Word output, not a
source PDF citation.

## Validation

1. Validate original block coverage, quotes, codepoint offsets and page hashes.
2. Require model elements and stories to partition the original block inventory.
3. Require every run and inline mention to retain exact source anchors and text.
4. Validate table spans; reject overlaps or merges hiding populated cells.
5. Read back Word body text with only whitespace reflow normalization. Read back
   all table cell values. Inspect OOXML for real footer relationships, page fields
   and repeating table headers; ensure no unintended automatic numbering.
6. Render using the selected document runtime and inspect every output page for
   wrapping, clipping, missing glyphs and footer/table placement. Automated
   readback alone does not establish visual correctness.

The optional Word contract tests use `PDF2DATA_DOCX_PYTHON`. The combined local
suite passed **1,085 tests**, with one optional `docling_core` test skipped.

## Real memorandum example on 31 October 2026

Source SHA-256:
`17b7dd0fe13e4e4f1ca5d4793c7485d66bb5e1b527f9b846a44564577f4d49ff`.

The model separates the title, subtitle and metadata shown in the review
screenshots. `1. Background` is a heading; `1.1` and `1.2` are numbered body
paragraphs. The date fragment `16.04.2026provides as under:` stays attached to
its preceding paragraph instead of becoming a false heading. Lowercase sentence
continuations ending in a colon no longer become headings. The source's missing
space and literal footer spelling are not silently corrected.

The run contains one title, one subtitle, one section heading, fifteen numbered
paragraphs, fourteen list items, two caption-to-table candidate links, two
editable tables and six source footer blocks. It records 46 date, 19 citation
and 33 quantity mentions. These are candidate counts, not measured extraction
precision or recall. No real formula was present in this sample; the formula
rule has only unit-test coverage here.

Raw page payloads, both table payloads and quality outcomes match the preceding
footer-validation run. All 36 table values remain, including the distinct
`31.12.2027` and `Not effective` cells. The Word output reflows six source pages
into five pages; original pagination is not preserved. The repeating footer is a
real Word footer and both table header rows are repeatable. The retained
repair-required source page remains repair-required.

Local receipts and renders are under `out/document-model-*-validation.json` and
`out/document-model-*-render`. The CLI also generated a two-page proceedings
conversion and passed its body/table readback; that secondary DOCX has not been
visually reviewed and is not the delivered example.

## Remaining work

- Evaluate role accuracy and outline relationships on independent document types;
  these native-text examples do not establish performance on scans.
- Recover complete quotation ranges, multi-column flow, varying section layouts,
  footnote/endnote anchors and figure relationships with source evidence.
- Resolve citations against actual versioned documents, separately from detecting
  their literal mentions. Never infer legal applicability from typography.
- Add trusted equation-tree extraction before claiming editable OMML math.
- Reconstruct automatic numbering only when its sequence, restarts and nesting
  are established. Preserve literal numbering otherwise.
- Benchmark retrieval and answer completeness independently. A richer model or a
  successful Word render alone does not prove a better OCR or RAG score.
