# PDF2Data

A small npm launcher for the open-source Python engine. Parses PDFs locally into
text, tables, Markdown, HTML, JSON and source-linked layout views. Includes an
optional MCP server. No cloud account or model download is required for native PDF text.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node 20+.
Then:

```bash
npm install -g @rudraptpsingh/pdf2data@next
pdf2data --version
pdf2data doctor
pdf2data workspace --workspace ./pdf2data-workspace
```

Or run `npx @rudraptpsingh/pdf2data@next workspace --workspace ./pdf2data-workspace`.
The first engine invocation downloads the pinned Python wheel and its dependencies
from the matching GitHub release into uv's isolated cache. uv can obtain Python
3.12 if needed. Later invocations reuse that environment. OCR engines and local
AI models are separate, explicit setup choices; `doctor` reports availability.

## MCP

Prepare the MCP runtime with `pdf2data-mcp --help` before connecting a desktop host. Configure:

```json
{
  "mcpServers": {
    "pdf2data": {
      "command": "pdf2data-mcp",
      "args": ["--workspace", "/absolute/mcp-workspace", "--allow-dir", "/absolute/pdfs"]
    }
  }
}
```

The tools are `pdf2data_parse`, `pdf2data_status`, and `pdf2data_read`.
A single workspace has one UI or MCP owner. Choose separate workspace directories
for separate clients. Local stdio integration depends on host support; a localhost
server is not automatically reachable by a hosted ChatGPT connector.

## Outputs and quality

`workspace` runs the engine's extraction, verification and full-document export
commands. Outputs remain available when source quality needs review. Source hashes,
physical pages, table structure and unresolved observations are retained. Parsing
alone does not establish factual correctness or legal applicability.

[CLI, examples and source](https://github.com/rudraptpsingh/PDF2Data).
For controlled mirrors/testing, `PDF2DATA_ENGINE_SPEC` overrides the pinned wheel
specification, `PDF2DATA_UV` selects uv, and `PDF2DATA_RUNTIME_PYTHON` selects the
Python runtime. These are explicit overrides; normal use needs none of them.
