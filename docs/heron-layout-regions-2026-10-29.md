# Source-bound layout regions — 29 October 2026

The existing Docling comparison pipeline now supports `layout_only=True`.
It runs cached Heron layout and TableFormer without whole-page OCR, preserves
empty image-only text clusters, and exports source-bound work regions for
targeted text OCR and table-cell recovery. The source package remains unchanged.

Each region retains original PDF SHA-256, physical page, selected page-text
SHA-256, original rendered PNG SHA-256, kind, PDF-point top-left bounding box,
normalized box, and candidate reading order from Docling's document tree.
Detector-only regions retain an unknown reading order. Hash-verified crops
retain their exact source pixel bounds and crop SHA-256. Figures and unknown
regions route to visual review; they are never implicitly ignored.

The existing table importer normalizes cell geometry, row/column offsets,
merged spans and header candidates. Cell crops remain unverified. A proposed
layout box covering ink establishes localization only: it does not prove the
words, numbers, relationships or complete source coverage inside that box.

Three held pages were processed with Docling 2.144.0 and PyTorch 2.14.1 on
Apple M5 Max. Model parameters and actual forward input tensors were recorded
on `mps:0` for all three conversions. Models were already cached; Hugging Face
and Transformers offline modes prevented new downloads.

| Case | Physical page | Layout regions | Cell candidates | Layout time | Table time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Short native extraction, 10-GZ | 2 | 17 text regions | 0 | 6.541 s | <0.001 s |
| CTUIL tariff table | 29 | 15 text + 1 table | 77 | 0.805 s | 25.145 s |
| Hindi image-only GZT/59 | 2 | 21 text regions | 0 | 0.877 s | <0.001 s |

The first short native extraction contained 55 native glyphs while 14 body
blocks had no native text. Layout localized those missing OCR targets.
For the Hindi image-only page, 88 existing Vision lines mapped against 21
layout regions yielded 39 descents in the proposed block reading sequence:
the existing full-page OCR mixed the two columns. The candidate block order
provides a concrete target for independent region OCR. One OCR line and
12,597 dark source pixels remain outside the proposed regions, including
scan borders and header material; coverage is explicitly unverified.

The tariff page demonstrates a remaining hard limitation. Layout-only
TableFormer proposed a 20-by-6 grid with 77 cells, while the earlier
OCR-enabled comparison proposed 24-by-6. The new geometry cannot approve
row/column relationships or numeric values. All 77 cell inputs are held for
source comparison and targeted recovery.

The reusable API is:

```python
from local_vector_batch.pdf_compare import compare_pdf

compare_pdf(package, original_pdf, empty_output_directory,
            pages=[physical_page], limit=1, language="iso:hi",
            device="mps", layout_only=True)
```

`page-regions.jsonl` contains routed block inputs and `page-table-cells.jsonl`
contains cell inputs. `page-comparisons.jsonl` retains native/OCR box mapping,
unaccounted raw raster observations, model forward device evidence and stage
timings. The source `.docling.json` output and existing layout-import protocol
remain available. These are review candidates; no quality gate or policy was
weakened and no page was promoted.

The staged three-page batch and its 54 block crops plus 77 cell crops are at
`powerlex-site/.wrangler/pdf-backfill/quality-gate/heron-layout-regions-20261029/`.
Its `summary.json` binds the results to exact original source/render hashes.

The [Docling pipeline reference](https://docling-project.github.io/docling/reference/pipeline_options/)
documents empty-cluster preservation, and the
[Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/)
documents Transformers layout inference on MPS. Actual device use above was
measured locally rather than inferred from the configured device.
