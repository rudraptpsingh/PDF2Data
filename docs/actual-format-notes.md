# Actual Indian document format notes

These are observations from local source packages and source exports, used to
check [document_profiles.json](../src/local_vector_batch/document_profiles.json).
Profiles propose literal format/field cues with source spans. They do not
establish a document's legal status, resolve dates, infer a missing value, or
override extraction quality gates.

| Family | Observed fields and layout | Important failure pattern |
| --- | --- | --- |
| Official letter / office memorandum | Bilingual letterhead; `Ref. No.`; `Sub:`; a separate date line; current subject; quoted earlier references; signature/designation; `To`, `Copy to`, `CC` | Recipient columns belong to separate horizontal sections. Reading the whole left column before the right can attach one person's designation/address to another or move a later CC ahead of a recipient. |
| Gazette / regulation | Gazette masthead, notification/instrument number, authority recital, short-title/commencement section, definitions, chapters, schedules and annexures | A non-Gazette regulation copy may have no `NOTIFICATION` heading. Hindi/English portions and annexures can restart printed pagination; physical PDF page identity must remain separate. |
| Order / judgment / corrigendum | Petition number, Coram, parties and addresses, appearances, hearing/decision labels, numbered reasoning, quoted submissions/prior orders, operative section and sign-off | A corrigendum may amend an order, not a tender. Quoted prayers or an earlier order's directions are not automatically the present decision. |
| Tender / bid documents | RfS/NIT identifiers, bid-information sheet, fees/security, pre-bid and submission labels, external schedule references, eligibility/technical clauses, annexed standard agreements | An RfS can refer the deadline to an external NIT rather than state it. Amendment tables, result ladders and agreement templates have different relationships and must not be flattened together. |

Verified package-report source URLs used for these checks:

- [CTUIL amendment letter](https://ctuil.in/uploads/ists_scheme_approve/176545177063Amendment%20to%20CTU%20OM%20dated%2023-10-2024%20regarding%20implementation%20of%20ISTS.pdf): current letter date, cited earlier OM, and an explicit old/new implementation timeframe are separate fields.
- [CTUIL scope amendment and recipients](https://ctuil.in/uploads/ists_scheme_approve/169141880805Amendment%20dtd%2007-08-2023%20to%20CTU%20OM%20dtd%2024-06-2022%20reg%20implementation%20of%20ISTS.pdf): numbered scope/implementation table and separate To/Copy-to/CC areas.
- [CERC tariff regulations copy](https://cercind.gov.in/regulations/finalregulations_terms&condition.pdf): the dated instrument header and commencement clause are distinct; the structured short-title section identifies a regulation without a Gazette masthead.
- [Bilingual Gazette source](https://cercind.gov.in/2016/regulation/GZT/59.pdf): Hindi and English text columns require language-specific recognition and reading order. The directory year is not a document date.
- [CERC order](https://cercind.gov.in/2026/Orders/214-TD-2026.pdf): Coram, decision date, party/appearance roles, and quotations from earlier proceedings coexist.
- [CERC order corrigendum](https://cercind.gov.in/2021/orders/Corrigendum-425-TL-2019.pdf): its own order date, cited base-order date, agreement date and scheduled COD belong to different events; the replacement table retains its parent paragraph link.
- [Procedure approval order with annexure](https://webcdn.grid-india.in/files/grdw/2025/05/Detailed-Procedure-for-Security-Constrained-Unit-CommitmentSCUCUnit-S_586.pdf): the annexed procedure and an effectiveness trigger are explicitly linked to the approval order.

Local SECI RfS, amendment, result and standard-PPA text exports were also read.
They are outside the current package-report cache, so no package-report URL is
asserted for those exports. Specific observed pitfalls:

- A bid-information sheet says pre-bid, submission and opening schedules are
  according to the external NIT. That is an unresolved reference, not the RfS
  issue date or a date borrowed from another tender.
- Existing/amended columns carry the changed clause and scope. A revised
  capacity in the amendment does not rewrite every older source artifact.
- A result sheet contains two side-by-side tables: an alphabetical qualified
  bidder list and a ranked tariff/award list. Two `Bidder's Name` headings do not
  justify joining the names at the same visual row across those tables.
- Standard PPA placeholders such as an insert-date field or unnamed developer
  are template fields, not execution evidence. LoA, PSA, PPA, financial closure
  and scheduled commissioning are separate event roles.

Dates need both a role and a literal source span: issue, notification,
publication, hearing, decision, commencement, cited prior instrument, deadline,
and implementation/COD are not interchangeable. Do not copy historical
project-specific year limits or reject a future commencement/deadline merely
because it is later than today.

Preserve unit and scope with every quantity: MW/MWh, kV/MVA/MVAR, nos./lots,
currency, lakh/crore, and per-MW/per-project denominators or caps. Do not compute
total security from aggregate tender capacity or infer currency from an Indian
issuer. Legacy `FACT` candidates may capture only `INR 10` from `INR 10 lakh`.
The current extractor fixes that truncation using the global quantity lexicon:
new monetary spans retain lakh/crore scales and per-MW/per-project denominators
literally, including compact OCR forms. It performs no conversion or inferred
total. Existing stored candidates are not silently rewritten; partial legacy
candidates remain unverified and require regeneration before quantitative use.

Visual context can nominate a logo, seal, signature, scanner mark or rule for
review; it cannot silently approve or remove ink. In the actual p16 blot case,
a model described repeated dots while the source crop showed an irregular
black stain. The strict dotted-rule geometry correctly rejected it. Likewise,
unreadable signatures and blank template fields are not missing words to invent.
