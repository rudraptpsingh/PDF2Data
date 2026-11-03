# Source-bound export views

Processed and readable exports retain the original page text, table cells,
quality receipts and candidate states. These additive views do not admit a
source, approve a claim or make an index ready for Query.

```sh
pdf2data export-l1-data --packages packages --output processed.json --json
pdf2data export-readable --package packages/example --output readable --json
```

Each processed document and readable `data.json` includes:

| Field | Contract |
| --- | --- |
| `fact_candidates` | Complete original date, number and percentage candidates. |
| `structure_candidates` | Complete original structure and configured-field candidates. |
| `candidate_inventory` | `pdf2data.candidate-inventory.v1`: parent package, source PDF, evidence manifest and candidate artifact hashes/counts. |
| `page_states` | `pdf2data.page-state.v1`: extraction selection state and authoritative source-quality outcome, separately. |

Inventory artifact paths are relative to the original package. Its
`document_pointer` addresses the containing exported document object.
`binding_scope: export_snapshot` means the new export binds the observed
candidate sidecar; it does not retroactively add that sidecar to an older L1
manifest. The export's own hash must be pinned by the consumer.

The raw page `status` remains the extraction selection status for compatibility.
Use `page_states[].source_quality` for quality and retain its receipt reference.
A selection can be `ready` while source quality is `repair_required`. Without a
package quality ledger, presentation shows **Source quality: not assessed**.
Neither state grants legal approval or publication.

Readable exports also contain `presentation/index.html`, linked page HTML/MD,
and presentation layout JSON with these page states. Document/layout views keep
their package-quality badges; overlays carry the same versioned state.

Frozen packages and running jobs are unchanged. These views require adoption of
a future producer release; they do not select or repin an extraction runtime.
