# Verified text regions on held PDF pages

`local_vector_batch.verified_regions` provides a separate, quotation-only
evidence contract. It never changes a PDF package, page outcome, full-page gate,
legal fact, or publication database.

```python
from local_vector_batch.verified_regions import (
    region_candidates, derive_text_region, validate_text_region, write_region_images,
)

for selection in region_candidates(page):
    receipt = derive_text_region(page, source_pdf, cached_independent_receipt,
                                 selection, table_count=known_table_count)
    if receipt["ready"] and validate_text_region(
            page, source_pdf, cached_independent_receipt, receipt,
            table_count=known_table_count):
        images = write_region_images(receipt, source_pdf, output_directory)
```

The generator nominates complete numbered clauses, otherwise sentence-ended
groups of original OCR lines. It preserves literal selected text and original
codepoint offsets. It holds incomplete continuations, an excluded adjacent
exception, known tables, ambiguous or inexact independent token matches,
parallel columns, strikes, intersecting excluded lines, invalid geometry, and
**any** uncovered dark pixel inside the selected context rectangle. It reuses
the existing source-raster and language/model provenance contracts and performs
no OCR inference. Words, ordered numbers and operators must agree exactly;
passing whole-page similarity scores cannot substitute for this local check.

A ready receipt binds the PDF bytes and physical page, full raster and crop
SHA-256, raster dimensions/DPI, pixel/normalized/PDF-point bounding boxes,
original page-text SHA and span, exact region text SHA, original OCR snapshot,
independent cached receipt/model/text, and current quality policy SHA. PDF points
use a top-left origin on the rendered cropbox; the PDFium source box is retained
so that this is not confused with a raw PDF bottom-left coordinate system.

The original page OCR SHA is lineage. An unrelated subsequent OCR edit does not
revoke an immutable source quotation. Validation rechecks the original snapshot,
cached independent receipt and physical PDF rather than substituting new parent
text. A changed PDF, crop, quote, original snapshot, model receipt or proof field
fails validation.

All unexplained outside components remain `held` with `role: unknown` and source
crop hashes. Excluded source lines also remain unverified and have crop hashes;
this preserves a stamp that OCR mistakenly interpreted as text. The exporter
writes the entire unchanged source-page raster as well as the region and all
outside crops. No unknown ink is deleted or classified as harmless decoration.

`acceptance_scope: verified_transcription_only` does not establish completeness,
authentication, execution, present legal status, or applicability. In particular,
an unread stamp might affect those questions. Consumers must use a separate
region citation/FTS contract and must not put these records in the existing
verified-page store or label their physical pages verified. `page_complete`
remains false and the source page remains held.

## Actual bounded pilot, 29 October 2026

The dev staging manifest is
`powerlex-site/.wrangler/pdf-backfill/quality-gate/verified-region-pilot/summary.json`.
The source-bound cache was the existing routed PP-OCRv5 audit, compared with the
original Vision text; no model or OCR was run. Eleven generic candidates took
1.678 seconds including fresh validation and image export:

| Original source | Physical page | Ready | Held |
|---|---:|---|---|
| [CERC AO.pdf](https://cercind.gov.in/2018/orders/AO.pdf) | 16 | Clauses 4.9, 4.11, 4.12: 887 characters | Clause 4.8 has `4.8.s` versus `4.8.`; clause 4.10 has `tuture` versus `future` |
| [CERC 11-RC-2019.pdf](https://cercind.gov.in/2019/draft_reg/11-RC-2019.pdf) | 25 | First two complete body groups: 533 characters | `ili`/`iii` disagreement; Benefit group has 37 uncovered pixels; excluded condition and unfinished continuation |

Total: **5 ready quotations / 1,420 characters; 6 held candidates; zero page
promotions**. Original blot, stamp/signature and missing-footer-number images
remain preserved. Ready receipts passed a second fresh source re-render.

Shared corpus identity belongs to the consuming adapter, not to parser-generated
IDs: these two PDFs map to main-shard `cerc-docket-124576fb5b7781f8` and
`cerc-reg-725bb6d70cd4e90a`. Staging manifests explicitly distinguish those IDs
from `parser_document_id`; the adapter must verify canonical URL and PDF identity.
