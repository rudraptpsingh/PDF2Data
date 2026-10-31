# Local DeepSeek-OCR accuracy pilot

31 October 2026. **Keep DeepSeek experimental; retain the current native/Paddle
pipeline.** Real local inference was run on 14 public benchmark PDFs and two
legal PDFs (eight pages). No external AI service or production database was used.

## Measured results

| Check | Current pipeline | DeepSeek-OCR via Ollama |
| --- | ---: | ---: |
| Matched public pilot, coverage-adjusted category macro | 75.04% | **27.33%** |
| Public table relationships | 6/6 | 0/6 |
| Source-checked legal probes | 7/7 | **3/7** |
| Public incomplete/content-empty outputs | 1 nonblank-page coverage flag | 4 context-limited + 4 control-token-only outputs |

The public pilot has 14 one-page PDFs, two from each of seven source categories,
98 external rules and 14 per-page baselines. Selection used the existing seeded
filename strategy (`pdf2data-v1`) before model inference. These files are a subset
of the previously used 70-file development sample, **not an independent holdout**
or the full public benchmark. Both outputs were rescored with the same pinned,
unmodified olmOCR rule classes and fresh equation-render caches. All failures
remain in their denominators.

| Category | Current passes/tests | DeepSeek passes/tests |
| --- | ---: | ---: |
| arXiv math | 7/9 | 8/9 |
| Headers/footers | 9/9 | 2/9 |
| Dense small text | 16/17 | 11/17 |
| Multiple columns | 8/9 | 0/9 |
| Old scans | 0/9 | 0/9 |
| Old scan mathematics | 21/39 | 0/39 |
| Tables | 6/6 | 0/6 |
| Per-page baseline | 12/14 | 6/14 |

Macro averages give each of these eight categories equal weight. They are not
word accuracy, full-table accuracy, legal correctness or a leaderboard score.
The current pipeline's original predictions were reused and hash-checked, then
rescored on the identical subset. Its extraction timing was not rerun. The
comparison is between workflows: whole-page DeepSeek Markdown versus the
current region reader with native table reconciliation, with different reading
budgets and resolution policies. It is not a controlled model-only comparison.

## Accuracy failures actually observed

- Four public generations ended with `done_reason=length`. Their raw partial
  replies are retained; they cannot count as completed extractions.
- Four normally stopped replies contained only `<|im_end|>`. The first scoring
  pass mistakenly allowed these nonempty strings to earn absence/baseline
  credit, reporting 40.63%. The completeness guard now rejects control-only
  output. Hash-bound raw replies were revalidated with **zero new model calls**;
  the corrected result is **27.33%**. The evaluator itself was not changed.
- On a handwritten source, the model described the handwriting instead of
  transcribing it. A normal stop is not evidence of source coverage.
- In the legal memorandum, Illustration 1's values appeared in a flat HTML
  `table` string without row/cell structure. The date remained in raw text but
  failed its table-relationship probe. Illustration 2 retained “Not effective”
  with its header, passing two probes; another exact-neighbor probe failed due
  to an added comma in a date/tenure cell. That formatting mismatch should not
  be described as a different numeric value.
- The proceedings output omitted the reply/rejoinder continuation and its
  optional condition. The hearing date survived. Those omissions matter to
  retrieval even though the output looked clean.

Seven legal probes are custom, previously inspected development checks. They
do not establish accuracy across all courts, Hindi scans, account tables,
contracts or company-impact calculations. The 8/8 legal baseline result is
reported separately; combining it with 3/7 substantive probes would mask gaps.

## Runtime and provenance

- Model: `deepseek-ocr:3b`, digest
  `0e7b018b8a22373167ee79c9a852821235488cbd2a2a5a93495558627423278c`.
- Ollama 0.40.13, official macOS archive checksum verified, isolated loopback
  server/model store. This is **DeepSeek-OCR v1, not OCR 2**.
- Original pages rendered at 180 DPI. Literal prompt:
  `\n<|grounding|>Convert the document to markdown.` Temperature 0, seed 0,
  context 8,192, maximum generated-token request 8,192, one worker. Input image
  tokens reduce the space available for output within that context.
- Actual public inference: **361.43 seconds**; median page 9.53 seconds, with
  the first request including model loading. Legal inference: **55.08 seconds**.
  Shared-host timings are one repeat, not an isolated throughput benchmark.
- Apple M5 Max **Metal GPU**, 13/13 layers offloaded. Ollama reported
  2,248,922,889 bytes of model VRAM allocation. This is **not measured peak RAM
  or peak GPU memory**, nor the 6.7 GB downloaded model-file size.
- Original PDF hashes, rendered image hashes, raw replies, request hashes,
  runtime/model identities, timing and generation failures are retained. No
  reference answers were sent to the model.

The [official Ollama model page](https://ollama.com/library/deepseek-ocr) documents
the 8K model and prompt sensitivity. The
[DeepSeek repository](https://github.com/deepseek-ai/DeepSeek-OCR) links the
separate OCR 2 model. Availability and paper scores do not establish performance
for this local reader/configuration.

## Reproduce through the CLI

Install Ollama and explicitly pull the desired tag separately. PDF2Data does not
download a model, start a server or replace the production reader automatically.
The adapter permits only loopback HTTP, disables proxy inheritance and redirects,
and verifies the exact installed tag/digest before and after each request.

```sh
pdf2data benchmark-prepare --dataset-root ./bench_data --per-category 2 \
  --seed pdf2data-v1 --output ./suite-14 --json
pdf2data benchmark-ollama-run --suite ./suite-14/suite.json \
  --host http://127.0.0.1:11435 --model deepseek-ocr:3b \
  --output ./deepseek-run --json
pdf2data benchmark-score --suite ./suite-14/suite.json --run ./deepseek-run \
  --upstream ./olmocr --eval-python ./eval-env/bin/python \
  --mode tables --output ./deepseek-score --json
```

`benchmark-project-subset` projects verified cached predictions onto a frozen
source-identical subset; it does not perform inference or supply fresh timing.
`benchmark-ollama-replay` revalidates saved, hash-checked raw OCR replies after a
completeness-policy fix; it does not generate new readings or change labels.
Use fresh output paths. `--resume` on an inference run requires unchanged code,
suite, configuration and model identity and verifies saved artifact hashes.

Checked-in receipts: [runtime summary](receipts/deepseek-ollama-pilot/run-summary.json),
[public DeepSeek score](receipts/deepseek-ollama-pilot/public-deepseek.json),
[public current score](receipts/deepseek-ollama-pilot/public-current.json),
[legal DeepSeek score](receipts/deepseek-ollama-pilot/legal-deepseek.json), and
[legal current score](receipts/deepseek-ollama-pilot/legal-current.json).

## Adoption decision

The optional CLI evaluation adapter adds no mandatory model dependency. Keep
it for experiments. Do not route approved native text or tables through it by
default. Before adopting a new reader, require independent bilingual/scan
holdouts, source coverage, digit/sign/negation checks, table-cell associations,
cross-page dependency recovery and downstream answer validation. Preserve
disagreements; model agreement never grants legal or financial approval.
