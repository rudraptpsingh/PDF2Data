# Literal page reading candidates

`source-page-reading` obtains a bounded original-image reading through a named
local AskMill vision profile. Use it when a saved reading is corrupt or missing
and source quality remains held. The source PDF, native reading, cached OCR,
older failures and quality states stay immutable. Model completion supplies a
candidate transcription, not independent source verification.

Prepare an explicit literal task prompt. The native `pdf-ocr` prompt is `OCR:`;
it is accepted without appended instructions. Additional instructions may change
model behavior. Include no expected answers, catalogue facts, suggested
corrections or legal interpretation. Select the reading scope explicitly; an English-only request
cannot establish bilingual completeness or column ownership, and the model may
fail to follow that scope.

```sh
pdf2data source-page-reading run --package parent --page 1 \
  --profile pdf-ocr --prompt literal-prompt.txt \
  --scope visible_english_literal_candidate \
  --max-tokens 2048 --timeout 120 --output independent-reading --json
pdf2data source-page-reading verify --bundle independent-reading \
  --package parent --json
```

The command verifies the parent, renders the exact physical page from its original
PDF, snapshots the selected profile and configured shared host lease paths, and
runs AskMill doctor before invoking the reader once. It requires an existing
local MLX vision model owned by the worker. Provider fallback, model download and
external-service control are unavailable. `--askmill-config` selects an explicit
existing configuration. `--askmill` selects the installed AskMill executable.
The request budget is at most 2048 output tokens and 120 seconds. Renderer output,
physical page rectangle, selected parent reading, exact prompt, profile, request,
raw result, AskMill receipt and invocation outputs are separately pinned. Keep
configuration files and invocation receipts in distinct namespaces.

Incomplete generation remains `held_incomplete` and exits 3. Partial output is
retained verbatim, including repetition, malformed text or incorrect scripts.
Preflight failures exit 2 and retain any created diagnostic receipts. There is
no automatic retry or partial-output adoption. A complete result remains a
candidate; it cannot establish reading fidelity, source coverage or legal facts.
The command does not create a new selected L1, change page quality, alter existing
field checks, admit a candidate to an application or repin a consumer job.

Verification checks AskMill's content-addressed request/profile/runtime receipt,
including incomplete inference results, and re-renders the original source image
without inference. It does not compare answers against catalogue expectations.
Preserve the installed producer wheel and dependency versions with the bundle
for reproducibility. Reusing a saved bundle requires no additional model call.
`runner_calls_performed` and `inference_calls_performed` describe the current
operation; both are zero for verification. `recorded_runner_calls` and
`recorded_fresh_inference_calls` describe the saved original attempt.
Further source correction needs a separately qualified producer operation and
independent original review. Keep the prior required assertions as
failures wherever the selected source reading cannot support them; never weaken
checks to accommodate model output.
