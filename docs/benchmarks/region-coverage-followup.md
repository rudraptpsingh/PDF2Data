# Source coverage findings

The new region reader preserves crop inputs, but source-image inspection found
real detector omissions. These findings must stay visible alongside benchmark
scores. [Source-bound findings](receipts/layout-guided/source-review-findings.json).

| Finding | Required improvement | Validation |
| --- | --- | --- |
| Unboxed lower mathematical expressions | Route residual source-ink bands for recovery; keep task/order uncertain where detector labels are absent | Original-pixel crop proof, missing-expression source probes and unchanged public rules |
| A headline crop clips its final glyph | Expand a crop only with source geometry; preserve both original and expanded bounds | Render original and crop together; no invented character completion |
| Two-leaf manuscript classified partly as a picture | Keep both leaves accessible and provide an explicit handwriting/visual reading route | All source leaves accounted; incomplete text remains flagged |
| Horizontal rules and gutters inflate uncovered-ink percentage | Separate long ruling/gutter components from potential glyph components | Synthetic rule-plus-text fixtures; no deletion of original ink or weakening of semantic review |

An ink bounding box cannot certify OCR text. Recovery must consume original
pixels rather than completing an expected sentence or benchmark answer. Partial
model responses and rejected table structures remain in raw readings, with
explicit token-cap/empty/parse reasons. Recovery should reuse only source/model/
prompt-bound completed readings; a reused run must not claim fresh throughput.
