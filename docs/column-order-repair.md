# Source-bound OCR column repair

`column_order.derive_column_order_repair` prepares an order-only repair from
the original page record, independent OCR lines, source-bound layout regions,
and original PDF bytes. `pdf_repair.apply_column_order_repair` freshly proves
the repair again before atomically rebuilding the package's page text,
embedding inputs, fact spans and structure spans. Every other page is checked
for preservation. No human review receipt is created and source-reviewed
decisions take precedence.

The algorithm preserves every original OCR line and its text exactly once.
Left-margin section headings can delimit horizontal bands only when no block
crosses the boundary. Within each band, separate x-intervals establish columns
and source raster ink must confirm the gutter. Both readers' line boxes must
assign unambiguously to one block; expanded source blocks must not overlap.
Every block requires identical ordered words, numbers and logical operators
from both readers. Numeric grids, tables, figures, missing blocks, overlapping
lines, strike marks, unaccounted source ink, and unsupported languages remain
held. All original extraction quality gates still apply.

Successful repairs retain an embedded source-bound proof. The ordinary
`assess_package(..., derive_ocr_source_raster=True)` path regenerates that proof
from original PDF bytes and the preserved original line streams. It compares
the independently reordered text against the proposed page SHA, rather than
accepting a stored `reading_order_proven` boolean.

```python
receipt = derive_column_order_repair(
    original_page, independent_lines, layout_regions, original_pdf,
    independent_metadata=metadata, table_count=0,
)
if receipt["ready"]:
    apply_column_order_repair(staged_package, receipt)
```

Independent metadata binds its original line-stream SHA, PDF SHA, physical page,
engine and version; the existing language gate additionally requires the proper
Devanagari recognition-model receipt. Layout rows are the reusable
`pdf_compare(..., layout_only=True)` outputs. Package mutation remains separate
from candidate preparation so the resumable repair dispatcher can stage work.

The initial three corpus calibration pages yielded no safe repair:

- Contact page `075c6033…`, page 2: layout classified address areas as tables.
- Contact page `3a29856b…`, page 2: the right recipient's name line was outside
  the proposed address block; it stayed unmapped.
- Hindi GZT/59 page 2: original OCR line boxes overlap.

These pages remained unchanged. Their exact source-bound hold receipts are
under `powerlex-site/.wrangler/pdf-backfill/quality-gate/column-order-pilot/`
and `heron-layout-regions-20261029/hindi-image/`. Real PDF tests separately
exercise a successful repair through the atomic writer and normal fresh
quality assessment, as well as numeric disagreement, tables, extra source ink,
stale receipts, source provenance, and section-band ordering.
