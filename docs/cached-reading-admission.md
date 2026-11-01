# Admit a cached legacy reading

`admit-cached-l1` makes an immutable native package from an existing verified
`pdf-extract` package and its original PDF. It runs no extraction, OCR, model,
download or source correction. The original package remains unchanged.

```bash
pdf2data admit-cached-l1 --package ./legacy-reading --source-pdf ./original.pdf \
  --source-sha256 "$ORIGINAL_SHA256" --package-sha256 "$LEGACY_MANIFEST_SHA256" \
  --source-id retained-notice --family notice --output ./admitted-reading --json
pdf2data verify-legal-l1 --output ./admitted-reading --json
pdf2data fields extract --package ./admitted-reading --rules ./rules.json \
  --output ./configured-fields --json
pdf2data fields verify --bundle ./configured-fields --json
```

Pin the original PDF SHA-256 and the `evidence-package.json` SHA-256 from the
selected reading. A different original, changed cached artifact, unsupported
package or existing output is refused. Verification checks original bytes and
physical page count using `pypdf`; it does not read PDF text again. The ordinary
`verify-legal-l1` command re-derives the admission artifacts and checks every
retained file, including optional historical and failed receipts. A package
without a valid legacy evidence manifest cannot be admitted from a PDF alone.

The package copies the original to `original.pdf` and every legacy file unchanged
under `extraction/`. `cached-reading.json` records their exact hashes, the pinned
legacy manifest, an immutable reading identity, the producer version and code
identity, the operation and missing facets.
Changing a reading requires a separate output and produces a distinct reading
and L1 manifest identity. Caller identity and family describe the selected source;
they do not settle its issuer, dates or publication role.

Selected page text, text hashes, source provenance, native/OCR alternatives,
table cells and geometry, cached page dimensions, repair states and failed
receipts remain in their original files. `layout.jsonl` exposes cached page
dimensions where present. It supplies no invented glyphs, character boxes,
independent raw readers or reading-order proof. Other retained optional facets
remain historical evidence; this command does not reinterpret them as current
L1 source-quality proof. Configured fields therefore have exact text anchors
but no new character geometry. Native structure and units are lexical candidates
derived from the unchanged reading, with unresolved legal boundaries.

Legacy embedding readiness never becomes current L1 content readiness. Every
nonempty page is held as `repair_required`; every empty page is `source_blocked`.
The original legacy page statuses remain unchanged. Missing current source proof
is explicit in `quality.json` and `repair-queue.jsonl`; `content_ready` and
`publication_eligible` remain false. Integrity and configured-rule checks do not
grant source, translation, numeric, legal, eligibility or financial acceptance.
Source correction or reproof must use a separate source-bound package, retaining
this admitted reading and its original receipts.
