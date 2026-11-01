# Installed native table proofs

Install the release with `[pdf,proof]` support. Native table source capture uses
one coherent current package directory in either a wheel or a source checkout.
Every participating proof module must reside in that same resolved directory;
mixing old and current modules remains refused.

The original extraction runtime is a separate provenance input. Its captured
configuration must match the source package. Its source files are inspected as
data and are not added to the proof interpreter's import path.

```bash
pdf2data native-table-region-reproof --package ./sealed-source \
  --source-runtime ./captured-extraction-runtime --page 7 \
  --feedback ./source-cell-feedback.jsonl --dossier ./source-raster-dossier.json \
  --output ./new-region-proof --json
pdf2data verify-native-quality-repair \
  --package ./new-region-proof/packages/source-id \
  --source-runtime ./captured-extraction-runtime --json
```

An empty feedback JSONL and `{"decisions":[]}` dossier request independent
source-cell verification without asserting human review. A disagreement remains
held. The command cannot infer a missing review or choose a source answer.
A held cell review writes a terminal outer `validation.json`, retains the full
cell-validation receipt and source snapshot, exits 3 and creates no new L1
quality version. Integrity or provenance failures remain refusals.

Choose a new output outside the immutable parent, captured runtime and inputs.
The command preserves raw page text, tables and structural clauses. Complete
cell membership, original glyph/ink ownership, independent readings and all
ordinary source gates remain mandatory. A successful new L1 version requires
its registered decoder; a resealed manifest alone cannot prove source quality.

The installed-route source cases cover a reference/date header with an
underlined heading, a real threshold grid and unchanged prose backgrounds.
A large-grid reference preserves a held control-code reading instead of
normalizing it without sufficient source context. These are bounded source
checks, not general table accuracy or legal, numeric or publication acceptance.
Existing jobs and consumer pins require separate explicit qualification.

For relocation of frozen parents and review inputs, see [frozen native proof custody](frozen-native-proof-custody.md).
