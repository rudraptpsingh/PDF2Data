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

The region decoder can retain open rounded grouping brackets as source geometry
candidates. It matches all native subpaths to one complete independent PDFium
object, including its transform and opaque stroke, then renders that object
alone to account for its exact source pixels. Adjacent text blocks need both
independent rectangle readings and the saved layout witness. The proof does
not approve the bracket's semantic ownership or treat a blank field as zero.
Unsupported, ambiguous, overlapping or differently painted paths remain held.

Equal thin footnote separator pairs retain their source paths. Glyph visibility
remains a separate gate: metric-box overlap can clear only when a fresh isolated
source-text render proves no ink under the later fill. Multiple font traces
sharing one paint operation keep distinct glyph keys and cannot claim separate
single-character ink bounds. Failed runs retain `source-path-components.json`
when available; this component evidence never grants whole-page quality.

Every literal frame, separator and footnote path owns only its independently
matched source-object raster pixels. Padded bands grant no ink ownership.
Foreign paint beside a footnote rule remains unowned, including at an endpoint;
neither a footer-rule heuristic nor the speck filter can clear that adjacent
paint. Exact source text paint may explain a touching footnote marker, while a
later rule crossing actual glyph ink stays held.

For relocation of frozen parents and review inputs, see [frozen native proof custody](frozen-native-proof-custody.md).
