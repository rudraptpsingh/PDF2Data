# Scanned table candidate pilot (28 Oct 2026)

The parser now uses OCR word boxes and visible grid lines to propose cell
coordinates. It stores physical page, PDF-point bounds, original PDF SHA,
selected page-text SHA, OCR engine, and render DPI. All cell candidates remain
`review_required`. Blank cells are left blank; words crossing printed boundaries
are listed as unassigned. Native PDF tables still take precedence.

| E2E fixture page | OCR tables found | Check |
| --- | ---: | --- |
| KERC 88018 p46 | 2 | `Loan Opening Balance` and `New Loan` values retain their printed rows |
| MoP 40999 p16 | 1 | Visible technical grid mapped; 14 ambiguous words unassigned |
| CEA 33743 p28 | 2 | Visible grids mapped; 4 ambiguous words unassigned |
| CERC 29891 p26 | 0 | Rotated table remains for source review |
| CERC 2473 p25 | 0 | Broken/dashed rulings remain for source review |

Benchmark used the five local fixture images resized to at most 1530×2100,
with macOS Vision OCR. Grid reconstruction median was 14–16 ms per page over
five repetitions. Vision OCR on these resized images completed in roughly
0.8–1.3 s per page after warmup. The tool reuses the OCR render for grid
inspection, so it does not render each page twice. These timings exclude PDF
decode and should not be treated as corpus throughput.

The deliberately missed two pages show the safety boundary: lack of a reliable
printed grid cannot become a row/column fact. A later rotation and broken-grid
pass needs independent source validation before it can expand coverage.
