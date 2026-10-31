# Physical pages and candidate document parts

`document-parts.json` is generated with readable/region exports and retained in
`document-model.json`. The original PDF hash and physical page numbers remain
the evidence identity. The artifact adds:

- Printed page labels with their native margin boxes, exact source text and
  separately recorded physical pages. Repeated printed “Page 1” labels do not
  overwrite physical page identity; Roman labels remain literal.
- Explicit annexure, appendix, schedule and language markers in English/Hindi,
  with source spans. Narrative references and quoted markers do not become new
  document parts.
- Header clusters that retain a literal reference/matter number and nearby
  labelled dates. A repeated identity is distinguished from a different
  identifier or a conflicting date, including on pages after page 1.
- Comparisons against explicitly supplied `document_reference` and
  `document_date` catalog fields. Conflicts remain review issues; the original
  catalog is never edited.

These are **boundary/identity candidates**. A header or annexure marker alone
does not prove the end of a part or which operative paragraphs it governs.
Automatic semantic page ranges remain empty. A matching Hindi/English number
does not prove translation equivalence. Quoted/cited dates, hearing dates and
as-of dates are not silently promoted into an instrument's issue date.

Use `pdf2data_read(artifact="document_parts", page=<physical page>)` through the
existing MCP tool for a source-scoped read. Sidecar hashes and source identity
are checked; older exports request re-export. Tests cover bundled identities,
repeated headers, conflicting dates, catalog conflicts, bilingual markers,
Roman/duplicate printed page labels, table-margin exclusions and tampering.
