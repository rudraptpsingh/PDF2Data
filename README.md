# local-vector-batch

A small, local, resumable embedding CLI for JSONL documents. It runs on Apple Silicon with MLX, Windows/Linux with PyTorch CUDA, or CPU. GPU encoding is batched in one process; CPU encoding can use a bounded number of worker processes. The base package has no ML dependencies and does not download a model merely by importing it.

The output is deterministic, checksum verified, and ready for a Vectorize import step. This project generates embeddings; it does not upload data, create an index, or assert that a retrieved passage supports an answer.

## Install

Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
pip install -e '.[mlx,test]'   # Apple Silicon GPU
# or: pip install -e '.[torch,test]'  # Windows CUDA / Linux CUDA / CPU
local-vector-batch doctor --json
```

For CUDA, install the appropriate official PyTorch build for your CUDA version first, then install `.[torch]`. The selected Torch build determines CUDA availability. On a Mac, the Torch backend also supports MPS, but MLX is the default when installed. The default model is `BAAI/bge-base-en-v1.5` with CLS pooling and 768 dimensions. MLX currently supports its `bge-base` registry mapping; use Torch for arbitrary Hugging Face encoder models, mean pooling, or a pinned model revision. Ensure your retrieval query encoder uses the same model, pooling, and normalization.

## Input and output

Input is one JSON object per line. `id` and `text` are required. `metadata` is optional and may contain scalar string, number, or boolean values. Keep metadata small for your target vector database.

```json
{"id":"reg-2024-1","text":"The actual extracted document text...","metadata":{"title":"Regulation title","url":"https://example.org/reg.pdf","authority":"CERC"}}
```

Run a small chunk first:

```bash
local-vector-batch embed --input examples/documents.jsonl --output /tmp/vector-demo --backend mlx --device gpu --batch-size 8 --chunk-chars 1500 --overlap 150 --json
local-vector-batch verify --output /tmp/vector-demo --json
```

Use `--backend torch --device cpu --workers 4 --threads-per-worker 1` for bounded CPU parallelism. One batched encoder process is used for GPU modes to avoid competing GPU contexts. Tune batch size to GPU memory; reduce it if the model runs out of memory. `--offline` is available with Torch when the model is already cached. The MLX backend uses its local registry/cache behavior.

Output contains `manifest.json` and numbered `batch-*.ndjson` files. Each Vectorize row is:

```json
{"id":"stable-sha256-prefix","values":[0.01,0.02],"metadata":{"document_id":"reg-2024-1","char_start":0,"char_length":1500,"offset_encoding":"codepoint","url":"https://example.org/reg.pdf"}}
```

Use `--format generic` for rows with an `embedding` field instead of `values`. Character offsets are Unicode codepoints in the original input text; the source application can show the passage by slicing that original text at the stored offsets. Keep the original text and its URL separately. Do not treat metadata URLs or the synthetic example files as verified legal citations.

## Resume and provenance

If a run stops, execute the same command with `--resume`. The CLI checks the input SHA-256, model and processing options, every existing batch checksum, vector dimensions, and duplicate IDs. It skips completed batches and writes new batches atomically. It will refuse a changed input or settings; use a new output directory for a new corpus or model. An empty output directory is required for a fresh run.

The manifest includes input hash, model, backend, pooling, device, normalization, dimensions, batch checksums, and completion state. Running `verify` does not need a model or accelerator. An agent can call the CLI with `--json` and parse one JSON object on stdout. Errors are JSON on stderr and exit code 2.

## Programmatic use

```python
from pathlib import Path
from local_vector_batch import Config, run, verify

manifest = run(Config(Path("documents.jsonl"), Path("vectors"), backend="torch", device="cpu"))
print(verify(Path("vectors")))
```

`run(config, encoder_factory=...)` accepts an in-process custom encoder for an agent or test. Its `encode(list[str])` method returns one float vector per input string, and it exposes a `device` string. The custom hook requires `workers=1`.

## Cloudflare Vectorize handoff

The default NDJSON has Vectorize-compatible `id`, `values`, and `metadata` fields. Create an index with the manifest's `dimensions` and cosine distance, then use the current Cloudflare Vectorize bulk import mechanism or SDK to upload batches. Upload only after `verify` reports `complete: true`. The CLI deliberately keeps upload separate so API credentials and deployment choices never enter the local embedding process. Vectorize metadata/index constraints vary by account and should be checked before importing large corpora.

## Scope

- Fixed codepoint windows keep offsets reproducible. This is intentionally simple; application-specific PDF/OCR cleanup and semantic section boundaries should happen before input JSONL.
- Text length is capped at the tokenizer limit for the Torch encoder (`--max-tokens`, default 512). The MLX registry model uses its own tokenizer limit. Choose character windows that fit the model.
- This tool does not perform retrieval, ranking, citation validation, deduplication across documents, or evaluation. A downstream app must verify passage relevance and source authority.
- Do not include private text unless the selected model and local cache policies permit it. No cloud embedding API is called by this tool.

## Development

```bash
pip install -e '.[test]'
pytest -q
```

Backend references: [Apple MLX](https://github.com/ml-explore/mlx), [PyTorch CUDA/MPS install](https://pytorch.org/get-started/locally/), [MLX embedding models](https://pypi.org/project/mlx-embedding-models/).

MIT licensed. Contributions welcome after the initial public release.
