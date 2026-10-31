# Contributing

Use a feature branch and a separate extraction output directory for each run.
Python 3.10+ supports the core CLI; MCP requires Python 3.11+; Node 20+ is needed
only for the npm launcher. A typical development setup uses Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[pdf,mcp,word,test]' build twine
.venv/bin/python -m pytest -q
npm --prefix npm ci --ignore-scripts
npm --prefix npm test
```

On Windows use `.venv\Scripts\python.exe`. OCR engines/models are optional.
The deterministic tests use fixtures; do not silently download model weights.

Preserve source hashes, physical page locators, original text/cells and review
issues. A parser improvement must not upgrade legal meaning or turn missing
information into zero. Keep the CLI, UI and MCP on the same processing path.

For a parser change, include meaningful source/regression tests. For packaging,
install the built wheel outside the checkout and check CSS/HTML/JSON assets.
Check `scripts/release_checks.py` and the release runbook before changing a version.
