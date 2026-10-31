# Original-to-output page map

Reviewed against all six original page rasters. This is the supplementary explanatory memorandum dated 26 October 2026, not the full principal regulations, July draft or final notification. The relationship map is a supplied review fixture, not automatically extracted or legally approved output. No currentness determination is made.

Original PDF SHA-256: `17b7dd0fe13e4e4f1ca5d4793c7485d66bb5e1b527f9b846a44564577f4d49ff`

Supplied review annotations; relationships below are not existing parser output or approved legal conclusions.

| Original page | Body blocks | Furniture blocks | What is present / missing |
| --- | ---: | ---: | --- |
| 1 | 13 | 2 | Title, file number, date, clauses 1.1–1.2 and beginning of the April OM quote are present. Date is duplicated; heading and quote formatting are flattened. |
| 2 | 14 | 2 | April OM conditions (i)–(iii) continue from page 1; clause 1.3 introduces the October OM. Both quoted sources and their nested numbering lack explicit parent/scope links. |
| 3 | 7 | 2 | Clause 1.3 commentary, 1.4–1.7, minimum tenure and COD conditions are present. Their parents, retained conditions and relative timing are not typed. |
| 4 | 6 | 2 | Clause 1.7 continues from page 3; clause 1.8 contains extension and termination branches. Illustration 1 heading points forward to page 5, without a stored edge. |
| 5 | 6 | 2 | Both original 2×9 tables and 36 cells are retained. Illustration 1 scenarios (a)–(c) and Illustration 2 table need separate associations; cited regulatory Table 1 is an external reference. |
| 6 | 5 | 1 | Illustration 2 scenarios and alternate filing/scheduling assumptions are present. They lack the backward link to its page-5 table and hypothetical-date labels. Decorative closing stars are omitted. |

## Findings

### F01: Overlapping date regions create duplicate content

Original pages: 1

The source has one date field. The export prints the combined key/value region and both contained child regions: Dated: 26.10.2026, Dated:, 26.10.2026.

**Required change:** Store containment/ownership for key-value regions as well as tables. Render either the parent or its children; retain both raw readings.

**Validation:** The readable body contains one date field; all three raw regions remain addressable. Adjacent independent date fields must not be deduplicated by text equality.

### F02: Document hierarchy and emphasis are flattened

Original pages: 1, 2, 3, 4, 5, 6

The current HTML renders non-table blocks as preformatted text. JSON keeps kind=section_header/list_item but no document parent, heading level, quotation scope or list parent. Bold/italic/underline distinctions visible in the PDF are not represented in these output blocks.

**Required change:** Keep one source-bound document tree with heading paths, typed list markers, candidate indentation and observed style spans. Reuse existing readable-export machinery rather than maintaining the reduced renderer.

**Validation:** Round-trip every selected body block and span. Test nested numbering resets in quotes, list parent accuracy and meaningful source styling without changing text.

### F03: Two quoted Ministry OMs lose attribution boundaries

Original pages: 1, 2, 3

April OM quotation begins under 1.2 on page 1 and ends on page 2. October OM quotation starts under 1.3 and closes on page 2. Page-3 commentary is the memorandum author’s account, outside that quotation. Plain text retains quote marks but not machine-readable scopes.

**Required change:** Represent quoted-source containers with cited date/issuer, exact opening/closing evidence and multiple physical spans. Keep author commentary separate.

**Validation:** Do not attribute April conditions to the October quote or promote Ministry quoted suggestions into a final CERC regulation. Test quote-boundary precision on page breaks.

### F04: Conjunctive eligibility conditions are ungrouped

Original pages: 1, 2, 3

The April quote’s paragraph 2 introduces (i), (ii) and (iii), joined by and, on the next page. Clause 1.4 says other conditions continue under the April OM. Flat list_item blocks do not capture this dependency.

**Required change:** Preserve the nested condition list and its parent, conjunction text and source attribution. Any normalized ALL_OF rule belongs in a separately reviewed interpretation layer.

**Validation:** A retrieval result about eligibility must recover the parent plus all three relevant conditions and clause 1.4; a single matching date is an incomplete evidence set.

### F05: Cross-page clause continuation is missing

Original pages: 2, 3, 4

Page 3 begins with unnumbered commentary under 1.3. Clause 1.7 begins on page 3 with direct supply and continues on page 4 with intermediary supply and the missing-PSA exclusion. Output has independent page blocks without continuation_of or shared clause IDs.

**Required change:** Attach multiple exact page spans to the same candidate clause while keeping each physical source anchor.

**Validation:** Queries about intermediary supply must include the page-4 exclusion; no new clause may be invented for the unnumbered continuation.

### F06: Contract roles, exceptions and fallback outcomes are not connected

Original pages: 3, 4, 5

Clause 1.7 distinguishes direct PPA from intermediary PPA plus PSA. Clause 1.8 distinguishes same-consumer extension from termination/different buyer. Illustration 1(b) separately describes fallback to the existing Table 1 trajectory. Text is present, but these branches are not linked.

**Required change:** Extract candidate role/contract/exception relations with exact evidence; retain separate scopes for proposed-waiver loss and the illustration’s existing-trajectory fallback.

**Validation:** Answer checks must reject a universal claim that termination removes every waiver. Missing client contracts remain unknown; no company-specific impact follows from these examples alone.

### F07: Illustration headings, tables and scenarios are detached

Original pages: 4, 5, 6

Illustration 1 heading is on page 4, its table and scenarios on page 5. Illustration 2 heading/table are on page 5 and explanations on page 6. No caption_of, illustration_parent or explains edge is emitted.

**Required change:** Create separate illustration containers with caption, table and all associated scenario spans. Do not merge the two tables because their headings largely match.

**Validation:** Each table retrieval must be expandable to the correct heading and scenario group, including across pages. Test that Illustration 2 never receives Illustration 1’s GNA value.

### F08: Tables retain cells but lack semantic field projections

Original pages: 5

Both nine-column grids and original values survive source reconciliation. The signing-date/tenure column holds two facts in one cell. Not effective is a source status, not a missing date. No reviewed field/header-to-value projection or illustration identity is stored.

**Required change:** Keep the original cell as authority; add candidate normalized fields with cell IDs, row/column paths and exact substrings. Model GNA status separately from a nullable normalized date.

**Validation:** Assert 36/36 cells unchanged, nine columns, correct header associations and distinct illustration identities. Normalization must retain Not effective verbatim and never invent a date.

### F09: Scenario assumptions and normative dates are conflated in an untyped stream

Original pages: 1, 2, 3, 5, 6

Dates include prior consultation history, quoted/proposed contract criteria, relative COD timing and hypothetical illustration dates. Page 6 says Suppose the amendment becomes effective from 1.12.2026; it does not establish an actual effective date.

**Required change:** Store date kind, source scope, proposed/hypothetical status, trigger and duration units. Keep original text and normalized value separate; do not convert calendar months into 60 days.

**Validation:** Timeline tests must reject 1.12.2026 as an established commencement date and 15.2.2027 as a universal filing deadline. Test leap-year/calendar-month boundaries without silently resolving legal interpretation.

### F10: Cited regulatory Table 1 is not either illustration table

Original pages: 3, 5, 6

The phrase Table 1 of Regulation 13(2)(a) points outside this memorandum. Its location is not the first table on PDF page 5. The July memorandum and principal regulation are referenced but not reproduced in this six-page source.

**Required change:** Use literal citation nodes with cited instrument/version/clause and unresolved target status until another verified source supplies that target.

**Validation:** External citation resolution must not bind to a same-number local object. Missing external material must be surfaced as missing evidence.

### F11: Text normalization and decorative omissions need distinct severity

Original pages: 2, 3, 5, 6

Inspection of all six source rasters and comparison of existing native glyph artifacts against body-region text found quote-mark normalization, a comma changed to semicolon in page 6(b), sub-clause rendered subclause and an ordinal emitted as raw LaTeX. Closing ***** is not in the readable output. Outside body boxes, encoded non-whitespace source glyphs are the recurring footer plus closing stars. This is not proof against every native decoding/OCR error, and does not establish that large substantive passages are missing here.

**Required change:** Report exact-vs-normalized transcription separately; retain style/superscript and decorative regions in a faithful-view mode. Classify evidence-changing errors above cosmetic ones.

**Validation:** Golden source comparisons preserve critical values/negations and surface punctuation differences. Do not count excluded repeated footers as substantive legal omissions or raw LaTeX as rendered superscript.

### F12: The new region-reading export bypasses existing structure exports

Original pages: 1, 2, 3, 4, 5, 6

structured.py detects one physical page per converter call, then reduces the Docling result to regions and candidate XY-cut order. table_reconcile.py builds another flat MD/HTML renderer. Existing document_blocks.py/readable_export.py already support source spans, candidate parent units, some cross-page continuation and relations.json, but the region export does not use that contract.

**Required change:** Add an adapter from verified region readings into one canonical block/span contract. Reuse the shared renderer and relations export. Add hierarchy/quotation/illustration relationships as reviewable candidates; do not simply switch on a detector flag and assume legal structure is fixed.

**Validation:** Run old and new paths on the same source; compare character conservation, source anchors, heading paths, cross-page groups and semantic retrieval. Preserve all prior source identities and benchmark receipts.

## Relationship map

- **R01 / contains_alternative_readings**: One source date field has a parent key/value reading and two child readings; choose one readable projection while retaining all evidence.
  - From: `56bebe798f88ed13c0d7:p1:r14`
  - To: `56bebe798f88ed13c0d7:p1:r9`, `56bebe798f88ed13c0d7:p1:r10`
- **R02 / quoted_source_scope**: Clause 1.2 introduces the April 16 OM quote, spanning pages 1–2.
  - From: `56bebe798f88ed13c0d7:p1:r5`
  - To: `56bebe798f88ed13c0d7:p1:r6`, `56bebe798f88ed13c0d7:p1:r7`, `56bebe798f88ed13c0d7:p1:r8`, `56bebe798f88ed13c0d7:p2:r0`, `56bebe798f88ed13c0d7:p2:r1`, `56bebe798f88ed13c0d7:p2:r2`, `56bebe798f88ed13c0d7:p2:r3`, `56bebe798f88ed13c0d7:p2:r4`
- **R03 / conditions_of**: April paragraph 2 introduces the following three conjunctive conditions across the page break.
  - From: `56bebe798f88ed13c0d7:p2:r0`, `56bebe798f88ed13c0d7:p2:r1`, `56bebe798f88ed13c0d7:p2:r2`
  - To: `56bebe798f88ed13c0d7:p1:r8`
- **R04 / quoted_source_scope**: Clause 1.3 introduces a different, October 5 OM quotation; it closes before page-3 commentary.
  - From: `56bebe798f88ed13c0d7:p2:r5`
  - To: `56bebe798f88ed13c0d7:p2:r6`, `56bebe798f88ed13c0d7:p2:r7`, `56bebe798f88ed13c0d7:p2:r8`, `56bebe798f88ed13c0d7:p2:r9`, `56bebe798f88ed13c0d7:p2:r10`, `56bebe798f88ed13c0d7:p2:r11`, `56bebe798f88ed13c0d7:p2:r12`, `56bebe798f88ed13c0d7:p2:r13`
- **R05 / commentary_under**: Page-3 opening commentary belongs under clause 1.3 but is outside the closed October quotation.
  - From: `56bebe798f88ed13c0d7:p3:r0`
  - To: `56bebe798f88ed13c0d7:p2:r5`
- **R06 / preserves_referenced_conditions**: Clause 1.4 preserves other conditions from the earlier OM; this is not unconditional replacement of every earlier criterion.
  - From: `56bebe798f88ed13c0d7:p3:r1`
  - To: `56bebe798f88ed13c0d7:p1:r8`, `56bebe798f88ed13c0d7:p2:r0`, `56bebe798f88ed13c0d7:p2:r1`, `56bebe798f88ed13c0d7:p2:r2`
- **R07 / conditions_under**: Seven-year minimum and two-calendar-month COD requirements are listed under clause 1.5, with an external explanation in the July memorandum.
  - From: `56bebe798f88ed13c0d7:p3:r3`, `56bebe798f88ed13c0d7:p3:r4`
  - To: `56bebe798f88ed13c0d7:p3:r2`
- **R08 / continues_clause**: The intermediary-supply passage on page 4 continues clause 1.7 from page 3, including the no-PSA exclusion.
  - From: `56bebe798f88ed13c0d7:p4:r0`
  - To: `56bebe798f88ed13c0d7:p3:r6`
- **R09 / branches_under**: Extension and termination/different-buyer branches belong to clause 1.8; they are not independent universally applicable rules.
  - From: `56bebe798f88ed13c0d7:p4:r2`, `56bebe798f88ed13c0d7:p4:r3`
  - To: `56bebe798f88ed13c0d7:p4:r1`
- **R10 / caption_of**: Illustration 1 caption on page 4 labels the first page-5 table.
  - From: `56bebe798f88ed13c0d7:p4:r5`
  - To: `56bebe798f88ed13c0d7:p5:r0`
- **R11 / scenarios_of**: Illustration 1 has separate no-election, election/extension/termination and late-COD cases; all use its own table inputs unless explicitly varied.
  - From: `56bebe798f88ed13c0d7:p5:r1`, `56bebe798f88ed13c0d7:p5:r2`, `56bebe798f88ed13c0d7:p5:r3`
  - To: `56bebe798f88ed13c0d7:p5:r0`
- **R12 / caption_of**: Illustration 2 caption labels the second page-5 table, including GNA Not effective.
  - From: `56bebe798f88ed13c0d7:p5:r4`
  - To: `56bebe798f88ed13c0d7:p5:r5`
- **R13 / scenarios_of**: Page 6 continues Illustration 2, not Illustration 1.
  - From: `56bebe798f88ed13c0d7:p6:r0`, `56bebe798f88ed13c0d7:p6:r1`, `56bebe798f88ed13c0d7:p6:r2`, `56bebe798f88ed13c0d7:p6:r3`, `56bebe798f88ed13c0d7:p6:r4`
  - To: `56bebe798f88ed13c0d7:p5:r5`
- **R14 / scenario_continuation**: Subsequent unnumbered paragraphs develop Illustration 2(b), including different pre/post-effectiveness contract-submission assumptions.
  - From: `56bebe798f88ed13c0d7:p6:r2`, `56bebe798f88ed13c0d7:p6:r3`, `56bebe798f88ed13c0d7:p6:r4`
  - To: `56bebe798f88ed13c0d7:p6:r1`
- **R15 / contrasting_scenarios**: Already-submitted eligible contract and later-submitted eligible contract lead to different described commencement assumptions; never merge the dates into a universal deadline.
  - From: `56bebe798f88ed13c0d7:p6:r2`, `56bebe798f88ed13c0d7:p6:r3`
  - To: `56bebe798f88ed13c0d7:p6:r4`
- **R16 / scenario_condition_dependency**: Illustration 1(c) changes COD to 15.03.2028 and discusses missing the two-month window; its changed COD must not overwrite the source table’s 29.02.2028 input.
  - From: `56bebe798f88ed13c0d7:p5:r3`
  - To: `56bebe798f88ed13c0d7:p3:r4`, `56bebe798f88ed13c0d7:p5:r0`
