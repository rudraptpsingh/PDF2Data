# Mixed native text and embedded source images

`native-image-regions` captures every embedded image object on one native PDF page, includes both literal CPU OCR readings beside the complete original native text, and preserves all disagreements. Each region is bound to original PDF bytes, immutable L1 manifest, physical page and native codepoint hash, exact image-object bounds, rendering DPI and regenerated PNG SHA.

```
python -m local_vector_batch native-image-regions --package ORIGINAL_L1 --source-runtime ORIGINAL_EXTRACTION_RUNTIME --page 8 --readings CACHED_CPU_READINGS.json --output NEW_PACKET --json
python -m local_vector_batch verify-native-image-regions --output NEW_PACKET --json
```

Omitting `--readings` explicitly runs bounded CPU Vision plus RapidOCR using three exact installed model paths. It fails if weights are missing rather than downloading or starting a GPU model. Cache/readers are candidate witnesses, not approvals. Raw OCR lines retain normalized and original source coordinates. Unique literal/geometry agreements do not infer table slots or resolve reading order. Disagreeing dates remain separate literals.

The command reruns the existing native whole-page quality gates against the original. The mixed representation has separate explicit gates for all images captured, reader agreement, raster visual coverage, table/field associations and mixed reading order. Visual/cell/order proof remains unknown until independently implemented and validated; this command does not fabricate all-pass. Candidate output is available while `page_complete` and `embedding_candidate_allowed` remain false. It never alters selected text, existing L1 packages or their quality states.

An optional `export-l1-data --region-packets NEW_PACKET` includes verified mixed candidates alongside the unchanged source pages and quality flags. The export requires an exact PDF hash and parent manifest match; unrelated or duplicate page packets fail. A directory of packets is also accepted. Matching hashes do not establish legal correctness, and explanatory sample forms are not real client facts.

Verification checks full artifact accounting, reopens the immutable source, recaptures every source image and its PNG bytes, reruns original whole-page quality and reconstructs the structured candidate packet. It rejects resealed crop/text/gate changes. It can validate region capture/provenance without claiming that agreement between recognizers proves the printed text.
