# Source-bound table observations

`structured-table-observations` reads an immutable L1 package and creates a separate immutable candidate JSON. It does not edit the PDF, selected text, page-quality report, metadata catalog or legal publication state.

```sh
pdf2data structured-table-observations \
  --package /absolute/path/to/l1-package \
  --pages 27 389 \
  --calendar-policy india_april_financial_year \
  --output /absolute/path/to/new-observations.json --json
```

Omit `--calendar-policy` to leave calendar dates unset. Financial-year endpoints and the inclusive count remain available. The explicit India policy derives April 1 of the first starting year through March 31 after the last starting year; those dates are labelled policy derivations, not literal source dates.

The adjacent `structured_table_observation_rules.json` configures financial-year prefixes and range separators, permitted year bounds, header row/range column, bounded caption distance, unit expressions and named calendar policies. These rules contain no station name, PDF identity, particular rate or particular financial-year endpoints. They do not remove Unicode or silently repair Hindi character mappings.

A candidate binds exact raw period/value/header/caption strings, cell IDs, table version and geometry hashes, original PDF hash, L1 manifest, page text hash, PDF-point bounding boxes, cell-local codepoint spans and any uniquely located selected-page spans. A wrapped table cell can map to multiple noncontiguous page spans: an intervening value is never inserted into the quoted range. Fresh pdfplumber reads of each original cell must exactly match the stored cell string. `verify_observations` independently regenerates the entire receipt under the exact rule and implementation identities; changing an amount or inventing a period total fails verification.

The conservative initial association is the explicit same-column header and same-row range cell, with the nearest single matching caption above that table on the same page. Blank headers, ambiguous captions, duplicated slots, merged cells, unresolved grid slots, nonnumeric values and unsupported ranges produce no quantity candidate. Current table geometry/header associations remain candidates; a fresh raw-cell read does not approve the grid or clear page quality. Complex header structures require additional source-bound rules, not empty-cell inheritance.

For the actual bilingual CERC tariff source `0701e5553c0d06890a179c8f277be92380a59017b6218bc5803154c769fa5e78`, Hindi physical page 27 and English physical page 389 retain five inclusive financial years, FY 2024-25 through FY 2028-29. Both source rows contain 42.52 with a nearby lakh-rupees-per-MW caption. The English raw header is `Tanda TPS (Unit 1)`; Hindi raw glyph/transcription errors remain visible. Numeric/range/unit agreement across the two rows does not independently establish translation or legal equivalence.

The candidate does not establish an annual schedule, a total, a deadline, project applicability, savings or a client fact. It never multiplies 42.52 by five. Source quality, semantic review and legal publication remain independent gates. No embedding/import/publication consumer is enabled by this command.

The Powerlex pilot receipt separately preserves the incorrect catalog title mentioning the 2026 Second Amendment and the conflicting original-source identity: Gazette 468 published July 1, 2024, with the English notification/title at physical page 359 naming the 2024 tariff regulations and March 15, 2024 notification. This observation does not overwrite the catalog remotely.

Tests:

```sh
PYTHONPATH=src python -m pytest -q tests/test_structured_table_observations.py
```
