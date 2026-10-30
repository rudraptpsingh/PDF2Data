# Granite-Docling local primary-OCR pilot

The bounded three-page pilot produced **zero source-gated passes**. Granite was
fast, but it is not ready to replace the current primary OCR path on these
Indian document samples. No source package was modified or candidate published.

The [official Granite-Docling model](https://huggingface.co/ibm-granite/granite-docling-258M)
is English and Apache-2.0 licensed. This test used IBM's existing
[MLX conversion](https://huggingface.co/ibm-granite/granite-docling-258M-mlx), pinned
to revision `e9939db25d2f296c8678d0491c4609a8c596c50a`. Its 630.7 MB weights
downloaded in 28.89 seconds; no conversion or new inference runtime was needed.

Hardware/runtime: Apple M5 Max, MLX 0.32.4, mlx-vlm 0.7.11, explicit
`Device(gpu, 0)`, measured peak Metal memory 1.46 GB. Model load took 0.259 s.
Original physical pages were rendered at 144 DPI. The official Docling prompt
was used with temperature zero and a 4096-token cap; all three generations
stopped normally. There was no hosted inference API.

| Actual held source | Physical page | Inference | Generated tokens | Candidate text | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| RE annexure / blank form | 16 | 3.473 s | 731 | 692 chars | Dropped substantial form text and emitted no table structure. |
| Mission governance prose + graphic | 22 | 1.809 s | 733 | 2,159 chars | Strong word overlap, but reading order, source graphic coverage and layout remained unproved. |
| Scanned regulatory prose | 16 | 0.207 s | 6 | 0 chars | Returned empty DocTags despite clearly visible English text. |

The first two pages generated approximately 469 and 461 tokens/s. They retained
the independent reader's ordered numeric tokens; that alone did not establish
correct extraction. The form's word precision/order were 0.656/0.623. The
mission page's word overlap was 0.997 but order similarity was 0.884. Original
render coverage still left 27,353 and 49,768 dark pixels unaccounted for,
respectively. Existing table/visual proof requirements remained in force.

Quality comparison used a fresh independent PP-OCRv5 English reader and the
original source raster; native pypdf text was recorded where available. The
existing Vision page was not treated as ground truth. All numeric/operator,
language, reading-order and source-coverage gates were retained. No broad
DocTags paragraph box was substituted for verified source line/cell evidence.

DocTags were parsed with the existing Docling-core implementation. Its
standalone parser uses page 1 and image-pixel dimensions, so the reusable
`pdf_compare.parse_doctags_page` adapter restores the true physical page and
PDF-point coordinates. Unknown cell boxes stay unknown; no cell geometry is
invented from table text.

Source bindings:

- Form: PDF SHA `78286eb287f4d637ae4dab1762208a1155481e3f9a92d855adae118bfa7ffd39`, page 16.
- Mission: PDF SHA `d550c9505ebc63381ed07e4503107423f85366a6455fbdab261569b37fd1ee0c`, page 22.
- Scan: PDF SHA `5724fb9bf915737e6f52d16d28f5e8498022716affbfd22054d62c753f29907c`, page 16.

Raw source hashes/renders, pinned model receipts, DocTags, normalized Docling
JSON, regions, independent observations and gate results are staged under
`powerlex-site/.wrangler/pdf-backfill/quality-gate/granite-docling-pilot/`.
`summary.json` records all three holds. They are benchmark candidates, not
source review receipts or verified legal facts.
