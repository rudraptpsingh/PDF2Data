# Physical source locations without text filtering

```sh
pdf2data source-locations --input original.pdf --output new-locations.jsonl --json
pdf2data source-locations --input original.pdf --package existing-l1-package --output new-cached-locations.jsonl --json
```

The optional package may be an existing L1 package or its PDF extraction directory. The command checks the original PDF identity and selected cached artifact/text hashes. It does not rerun extraction, OCR, models, complete-package validation or source-quality proof. Every physical PDF page is exported, including held pages, empty cached readings and pages absent from the text cache. Unicode text is preserved exactly. A location is `source_only` when usable cached text is unavailable; the original page remains addressable.

Each record binds the original PDF SHA256, physical page and stable region identity, preserves raw MediaBox, explicit CropBox (or null when absent), rotation and UserUnit, and reports its declared rotated CropBox dimensions. `--bbox x0 y0 x1 y1` defaults to the full page `[0,0,1,1]`. Normalized coordinates use a top-left origin in the **declared CropBox after page rotation**, before any renderer-specific clipping. Raw PDF boxes retain their unrotated bottom-left user-space coordinates. These frames must not be interchanged. A renderer must separately record actual viewport, clipping, raster dimensions and image hash; this exporter does not render or verify pixels.

Cached quality remains an observation with its original artifact/physical-page binding. Model review is `not_run`; future model output is a candidate, not source-quality or legal approval. Legal answer, publication and source-quality approval flags stay false. Hash agreement alone does not establish correct transcription, legal dependencies or legal correctness. Output must be a new JSONL file outside the immutable source package; originals remain unchanged.

The Powerlex application currently uses its own source-location adapter. Adding this generic PDF2Data command does not mean that application already invokes it.
