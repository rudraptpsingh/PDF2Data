# Reprove white paragraph-background candidates

Some PDFs draw a white, unstroked rectangle behind each text line. The native
geometry reader can interpret adjacent backgrounds as a single-column grid.
The conservative raw-candidate fallback retains those observations. Their cell
agreement alone does not establish that they are visible tables.

`native-whitefill-prose-reproof` exposes the existing registered white-background
source proof. It now accounts for a thin, long bottom-margin rule alongside the
white backgrounds. It never deletes or rewrites a raw table candidate.

```bash
pdf2data native-whitefill-prose-reproof --package ./sealed-l1 \
  --source-runtime ./captured-extraction-runtime --page 8 \
  --output ./new-source-proof --json
pdf2data verify-native-quality-repair --package ./new-source-proof/packages/source-id \
  --source-runtime ./captured-extraction-runtime --json
```

Use the original extraction runtime matching the captured source configuration;
it is provenance, not the current proof engine. Install the current release with
`[pdf,proof]` support. Use a new output outside the immutable parent and runtime.
A held source writes a terminal receipt and exits 3. An already accepted page
cannot be cleared again.

The candidate predicate requires at least two single-column cells, each matched
to exactly one white fill-only rectangle within 0.5 PDF points, one nonempty
physical text baseline per cell, and no intersecting ruling rectangle, line or
curve. Every archived matrix and cell box must match the fresh source geometry.
Multi-column, colored, stroked, multiline, unmatched and ambiguous candidates
retain ordinary review. Candidate diagnostics appear in native table previews.

The whole-page rule additionally requires no images, curves or body rulings.
A permitted footer rectangle must span at least 60% of page width, be at most
4 PDF points high, lie wholly in the bottom 10% of the page and overlap no
nonempty glyph. Every other rectangle must be exactly white and fill-only.
Fresh independent paint-stack analysis must find no occluded glyph or
unsupported paint. The source-line reader must prove complete glyph coverage
and single-column flow. Selected text must equal the original native reading
or an exactly reproduced registered source-line representation.

All eight ordinary quality gates still run, including independent words,
numbers, operators, source-raster accounting and reading order. A new immutable
L1 version is emitted only when every gate passes. Its registered decoder
reconstructs the source proof again. The version records white backgrounds,
footer objects and complete raw-candidate evidence separately from semantic
or legal table interpretation.

Original PDF bytes, page/text/table artifacts, structural clauses and values
remain identical. Other pages' quality records remain unchanged. Frozen jobs,
quality ledgers, failed receipts and consumer pins remain unchanged. Adoption
of the new version requires a separate, explicit consumer qualification.

Development references cover three prose-only pages, a threshold grid, a merged
cell table and two illustration grids. Mechanical controls additionally retain
borderless multi-column and ambiguous single-column candidates. These bounded
checks do not establish general accuracy or borderless-table coverage, semantic
interpretation, legal correctness or publication eligibility.
