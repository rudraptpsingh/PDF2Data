# DeepSeek-OCR, Ollama and Open WebUI assessment

31 October 2026. Integration research; no DeepSeek backend or model download
has been added to PDF2Data.

Both supplied guides were read. Their setup ideas are useful, but OCR output
must enter PDF2Data's source-bound extraction/evaluation contract.

Reviewed guides: [DeepSeek/Ollama setup](https://www.aitooldiscovery.com/how-to/deepseek-ocr-ollama)
and [Open WebUI setup](https://www.aitooldiscovery.com/how-to/setup-open-webui-ollama).

## DeepSeek via Ollama: useful optional reader candidate

Ollama's official `deepseek-ocr` entry is a vision-capable 3B model, listed at
6.7 GB with an 8K context window and minimum Ollama 0.13.0. Its documentation
warns that prompt punctuation/newlines affect output. It offers text,
Markdown and layout-oriented prompts. These facts establish availability,
not quality on PDF2Data's sources. [Official model entry](https://ollama.com/library/deepseek-ocr).

Ollama's REST vision interface accepts base64 images. An adapter would consume
original-page rasters or hash-bound crops already prepared by PDF2Data, with
the original PDF/page/crop transform retained. Raw PDFs still need page
rendering. [Official vision API](https://docs.ollama.com/capabilities/vision).

DeepSeek also publishes **OCR 2**, with PDF/image inference scripts, dynamic
resolution and CUDA-oriented Transformers/vLLM examples. It deserves a separate
candidate evaluation; the Ollama v1 tag must not be assumed to run OCR 2.
[Official OCR 2 repository](https://github.com/deepseek-ai/DeepSeek-OCR-2).

Proposed adapter requirements:

1. Explicit optional setup, pinned runtime/model digest and no automatic model
   pull or replacement of good native text. Keep the core dependency set small.
2. Fixed versioned prompts; bind source pixels, resolution, crop transform,
   decoding settings and code identity. Preserve raw responses and timing.
3. Treat empty, looping and token-limited responses as incomplete. Keep
   disagreements; do not manufacture a completed table or missing words.
4. Reconcile OCR table output with source geometry, headers and footnotes.
   Generated Markdown is not itself a verified source-cell/character locator.
5. Use the common cross-page assembly pass. Concatenating per-page Markdown
   cannot establish table/list/paragraph identity across boundaries.

The guide's shell snippets should not become parser implementation: its batch
example escapes the grounding marker, and shell newline/base64 conventions are
not portable across environments. Use structured requests and a tested literal
prompt. Benchmark results quoted by a guide are not comparable to our tuned
70-PDF subset without the same data, scoring and reader configuration.

## Open WebUI: optional client, separate from the parser

Open WebUI documents native MCP Streamable HTTP connections; stdio/SSE tools
need a bridge. [Official MCP documentation](https://docs.openwebui.com/features/extensibility/mcp/).
PDF2Data already exposes CLI-backed parse/status/read tools and a loopback
Streamable HTTP option. A future interoperability test can connect those tools
without bundling a second chat/RAG application into the parser. Tool-calling
quality and access controls still need testing; this review does not claim an
executed Open WebUI integration.

Keep PDF2Data responsible for extraction, anchors and quality reports. Keep
PowerLex responsible for reviewed facts, complete retrieval, applicability and
drafting. A general chat UI does not resolve incorrect headers, units, periods
or legal assertion roles.

## Evaluation before adoption

Local preflight found no `ollama` executable and no responding loopback server
at port 11434. No DeepSeek inference was run, and no speed/accuracy gain is
claimed.

First compare the candidate with the existing readers on the same frozen weak
scan, handwriting/math, dense-table and bilingual cases, including the two
remaining hard reading failures. Add independent source-family holdouts before
tuning. Measure critical digits/signs/negations, table alignment, omitted regions,
reading order, truncation, cold/warm latency and peak memory. Re-run source-bound
retrieval and answer checks after recognition improves. Select a reader only
when measured gains outweigh regressions and operational cost.
