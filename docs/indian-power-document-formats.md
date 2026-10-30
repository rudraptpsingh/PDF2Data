# Indian power-sector document structure for ingestion

This is a parsing contract, not a source of legal facts. A field is usable in chat only when its literal value is tied to the original PDF hash, physical page, and exact text span or reviewed table cell. A format cue merely proposes where to look.

| Family | Identify from | Distinct fields | Retrieval unit | Common mistake to prevent |
| --- | --- | --- | --- | --- |
| Official letter / circular | Issuer masthead, file/reference number, addressee, subject, signature, enclosures, copy list | Issuer, addressee, printed date, subject, referenced instrument, instruction, enclosure | Subject plus operative paragraphs, with the signed page and referenced enclosure linked separately | Treating the addressee as issuer, or the letter date as a legal commencement date |
| Regulation / Gazette notification | Notification, authority and enabling provision, instrument title, short title and commencement, numbered regulations, schedules | Notification number, printed/notification/publication/effective dates, parent instrument, clause hierarchy, operative text, table rows, amendment links | Clause and subclause with parent heading; table with its header and notes | Treating a draft or notification date as commencement, or mixing amended and superseded text |
| Commission / court order | Forum, petition/appeal number, parties, coram, hearing and decision dates, findings, directions, signature | Matter identity, forum, parties and roles, order type, hearing date, order date, operative direction, recipient, deadline trigger | Numbered paragraph and direction with matter identity; annexures separately | Treating a hearing date or interim direction as final disposal; assigning one matter's direction to another |
| Tender / RfS / NIT | Issuer and procurer, RfS reference, bid information sheet, schedule, eligibility, financial terms, annexures | Portal/tender ID, capacity and technology, bid submission and pre-bid dates, EMD/PBG, tariff, eligibility, bid version | Clause or schedule row plus inherited table headers and footnotes | Treating a cover date as bid deadline, or reading an old deadline after corrigendum |
| Corrigendum / amendment | Corrigendum number/date, parent reference, existing/revised or read-as table, substituted text | Parent document, affected clause/field, old/new literal, version sequence, effective date | Changed row or clause plus parent version and unmodified context | Applying a change to the wrong tender or assuming every old clause is superseded |

An order may name a petitioner and respondent, then direct only one party to act. Preserve the party label and the operative direction together; a deadline cannot be assigned until its trigger and recipient are read. A corrigendum's existing/revised table is a candidate change until the parent tender reference and row are matched. Its printed date is not the revised bid deadline.

Official examples show why these distinctions matter: CERC lists a principal DSM regulation separately from its amendments and multiple effective-date notices; one commencement notice gives different start times to different clauses. SECI's procurement manual describes a corrigendum as the way to amend tender documents and extend submission dates. The ingestion pipeline therefore needs clause-level effective scope and tender version links, rather than one date copied to every record. Sources: [CERC current regulations](https://cercind.gov.in/current_reg.html), [CERC commencement notification](https://www.cercind.gov.in/Regulations/G-766-15Sep2024.pdf), [SECI procurement manual](https://www.seci.co.in/assets/uploads/88367059-4C54-43A7-9ABE-6659F28377BC.pdf).

## Shared record shape

Keep `source_pdf_sha256`, official URL, physical `pdf_page`, page text hash, text span/box, observed text, candidate role, review status, and any normalized value in separate fields. Preserve printed page labels separately from physical PDF pages. A reviewed fact also carries issuer, jurisdiction, document family, parent/child and version relationships, and the precise page/row or clause that supports it. Unknown stays unknown.

## Retrieval order

Resolve named document or matter identity first. Search both text and embeddings within applicable jurisdiction and version. Fetch the complete clause or table row with heading, notes, adjacent continuation and source page. Rerank only these source-bound units. Answer from verified units, citing the physical PDF page. A structured value speeds filtering, but its quoted evidence remains the citation.

## Current implementation boundary

The local parser emits review-only format and field candidates from configurable `document_profiles.json`. It does not promote date labels, headings, or numbers into verified facts. The Cloudflare ingestion adapter must preserve these distinctions and must not claim that a candidate label is the operative date or a current tender deadline without source review and amendment resolution.
