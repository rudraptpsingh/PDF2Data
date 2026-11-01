# Replay frozen native proofs from custody

`verify-native-quality-repair` can explicitly bind captured resource paths to
byte-identical frozen resources. The original package JSON remains unchanged.
The registered decoder still reconstructs its source proof from the original
PDF. A successful replay proves the selected source-quality operation; it does
not grant publication, legal, numeric or source approval.

```sh
pdf2data verify-native-quality-repair \
  --package custody/child \
  --source-runtime custody/extraction-source \
  --proof-runtime custody/decoder-source \
  --relocations custody/bindings.json --json
```

Both runtime roots and the child must resolve inside `custody_root`. Each runtime
root contains `src/local_vector_batch`, `local_vector_batch`, or
`site-packages/local_vector_batch`; the package directory itself is also accepted.
Keep regular `.py` and `.json` package source files, including their original
names. The extraction source closure is inspected as provenance and never
imported. The decoder closure must match every captured source-file hash and
the complete captured package code identity. Supported decoders are the installed
CLI's exact current code and the exact released rc12 and rc13 code. Other historical epochs
fail closed. The adapter executes only the verified flat decoder files in an
isolated Python subprocess, using the installed interpreter and dependencies.

The bindings file uses `pdf2data.proof-relocations.v1`:

```json
{
  "schema_version": "pdf2data.proof-relocations.v1",
  "custody_root": "/absolute/custody",
  "packages": [{
    "captured_path": "/captured/parent",
    "resolved_path": "/absolute/custody/parent",
    "manifest_sha256": "<captured parent_manifest_sha256>",
    "source_pdf_sha256": "<captured original PDF SHA256>",
    "source_configuration_sha256": "<canonical source configuration SHA256>"
  }],
  "files": [{
    "captured_path": "/captured/review-input.json",
    "resolved_path": "/absolute/custody/review-input.json",
    "sha256": "<captured input SHA256>"
  }]
}
```

Use absolute lexical paths without `..`. Resolved resources must stay inside the
custody root. Source configuration hashing uses UTF-8 JSON with sorted keys,
`ensure_ascii=False`, and separators `(',', ':')`; the value is the captured
`quality_repair_configuration.source_extraction_configuration`. The parent
manifest pin comes from that same configuration. Its original PDF bytes and
source receipt must match the child's captured source identity and configuration;
ordinary complete package verification must also pass. A newly resealed parent
cannot replace the captured manifest. Missing bindings never fall back to the
captured caller path.

Supported operations are `native_whitefill_prose` and `native_table_regions`.
The latter also requires explicit `files` entries for captured feedback, dossier,
and every dossier decision's reviewed raster. Each raster is freshly rendered by
the original decoder and compared with the frozen reviewed image. Empty feedback
and dossier files are still required bindings. Preserve every stage's parent and
verify each stage separately when accepting a multi-stage chain; verifying the
last repair alone does not independently reconstruct earlier repairs.

Keep captured dependency versions available. The decoder checks the captured
`pypdf` and `pdfplumber` versions and code profile; its existing proof algorithms
and renderer comparisons remain unchanged. The replay receipt additionally lists
`pymupdf` and `pypdfium2` versions. This interface performs bounded native source
verification, without OCR or model inference.

The JSON result contains `custody_replay`: map hash, captured and resolved runtime
paths, executed decoder code identity, dependency versions, adapter hash, and all
validated captured-to-resolved resource bindings. Custody resources and runtime
files are sealed before and checked after replay. Keep the mapping configuration
separate from invocation receipts, use immutable output names, and pin both by
hash. A frozen replay can be tested with the original caller directories denied;
no caller parent, feedback, dossier, raster or source-runtime path is needed.
