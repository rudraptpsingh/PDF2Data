# Source-bound semantic fields

Classify verified native field observations through an installed AskMill runner.
Models select source IDs and configured roles. PDF2Data projects the original
source parts, including cross-page spans and quality holds; generated literals,
formulas and eligibility decisions are rejected.

Start with an extracted and verified `configured-fields.v3` bundle. Define a JSON
profile:

```json
{
  "schema_version": "pdf2data.semantic-profile.v1",
  "id": "project-conditions",
  "version": "1",
  "field_ids": ["project.cod_window"],
  "roles": ["condition", "document_metadata"],
  "role_descriptions": {"condition": "A stated prerequisite, without an eligibility decision"},
  "unresolved_dependencies": ["principal-regulation-version"]
}
```

The package includes the declared-family
[CERC supplementary-draft rules](../src/local_vector_batch/extraction_rules/cerc-sharing-supplement-v1.json)
and [JSON checks](../src/local_vector_batch/extraction_rules/cerc-sharing-supplement-v1.checks.json).
These require explicit source selection; they are not automatic document-family
classification or a complete eligibility model.

```sh
pdf2data semantic-fields prepare --bundle fields --profile profile.json --output semantic-job
pdf2data semantic-fields run --job semantic-job --askmill /path/to/askmill \
  --profile general --store receipts --output semantic-run
pdf2data semantic-fields validate --job semantic-job \
  --result semantic-run/stdout.json --store receipts --output replay-validation
```

AskMill 0.1.0a4 supports the required opt-in request/result v2 schema contract.
Install PDF2Data with its `pdf` extra for source re-verification; the npm launcher
already requests that extra. A bare Python wheel lacks `pypdf`. The minimal
standalone replay was independently checked with `pypdf==6.21.4`; it does not
qualify a fresh PDF-parsing runtime.
Use an existing configured profile and shared GPU leases. PDF2Data does not call
model HTTP endpoints, download models, repair responses or change providers.
Failed/incomplete raw output remains in the run directory.

Preparation re-derives the native fields and freezes the profile, scope and
request. Validation repeats native re-derivation, rejects altered job files,
requires the exact response-schema hash, and accounts for every selected atom
once as a role candidate or unresolved. Source sections outside the selection
are not measured. Missing dependencies remain unresolved.

The output contains `candidates.json` and a hash-bound validation receipt. The
run command also verifies AskMill's request/result receipt and settled session.
Standalone validation can repeat that check with `--store`.
Completion, schema validity, selected-atom coverage, source quality and semantic
approval remain separate. A supplied result file alone does not establish
runner receipt integrity. Keep AskMill's own receipts and installation pins
when qualifying a real run. Downstream relationship and domain validation stay
with the consuming application.
