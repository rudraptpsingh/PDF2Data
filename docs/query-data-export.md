# Query records from cached packages

`export-query-data` is an optional ingestion projection. The default
`export-l1-data` remains the full archival export of all representations.

```sh
pdf2data export-query-data --packages EXISTING_L1_PACKAGE_DIRECTORY --output NEW_QUERY_RECORDS.jsonl --json
```

This verifies each existing L1 parent once and emits compact UTF-8 JSONL. It does
not extract PDFs again, run OCR/models, clear quality flags or approve law.
The existing parent verifier can inspect layout; the exporter does not load the
large layout JSON again. Raw sidecars are stream-hashed and referenced in place.
Non-finite JSON values such as NaN or Infinity refuse publication, even if a
permissive cached-artifact reader would otherwise accept them.

Each record has schema `pdf2data.query-record.v1`, a deterministic `id`, `kind`,
`ordinal`, `source_id`, original PDF and package hashes, `payload`, canonical
`payload_sha256`, and `source_ref`. All three source-quality, legal-answer and
publication approval flags are false. Offsets are Unicode codepoints.

| Kind | Payload |
| --- | --- |
| `document` | Source receipt, complete cached quality/readiness, verification, structural metadata, counts and raw-sidecar references |
| `page` | Entire original selected page record, including complete Unicode text, text hash and reading status |
| `table` | Entire original table object, including cells, spans, headers, blank-cell state and unresolved candidate metadata |
| `unit` | Entire original structural candidate, including all cross-page spans, exact quotes and unresolved dependencies |

The source reference pins the exact cached artifact and its physical JSONL
record index or JSON pointer. Unit references retain span coordinates; the
original exact quotes remain in the complete payload. Document sidecars bind
artifact kind, SHA, byte size, package identity and absolute local path with
`availability: local_not_uploaded`. This does not assert that any cloud asset
exists. Store or convert these locators through a separately verified upload
adapter before using them remotely.

Glyph streams, alternative raw readers, layout alignments, retrieval children
and repair diagnostics remain available in their unchanged, hash-bound cached
artifacts. They are not silently deleted or copied into every query row. The
projection is therefore not an all-representations export. Candidate table/unit
structure and cached quality observations retain their original uncertainty;
successful export alone proves neither extraction accuracy nor legal coverage.

Output must be new and outside the immutable package directory. Corrupt or
changing parents refuse publication; an existing output is never overwritten.
JSONL records can be mapped by Data2DB using `/id`, `/kind`,
`/source_pdf_sha256` and JSON `/payload`; source references and package/payload
hashes should also be retained for evidence resolution.
