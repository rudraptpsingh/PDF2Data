# Release setup and next milestones

31 October 2026. Release candidate: `v0.2.0-rc.1`; Python version `0.2.0rc1`;
npm version `0.2.0-rc.1`. The npm name is `@rudraptpsingh/pdf2data`; the unscoped
`pdf2data` npm package is owned by another publisher.

## Distribution in this release

- GitHub release: Python wheel/source distribution, npm tarball, installable agent
  skill ZIP and SHA-256 checksums.
- npm: thin CLI/MCP launcher pinned to the matching GitHub wheel. Node 20+ and uv
  are required. The Python runtime is isolated in uv's cache. No postinstall script,
  cloud service, proprietary client profile or model download is bundled.
- Core extraction supports Python 3.10+; the MCP v2 extra requires 3.11+. The npm
  launcher selects 3.12. OCR/MLX/CUDA remain explicit optional installs.
- Word is optional through `[word]`; advanced source-paint proofs use `[proof]`.
  Third-party dependencies retain their own licenses; PyMuPDF has AGPL/commercial
  licensing. Neither proof nor OCR model dependencies are installed by the npm CLI default.
- `local_vector_batch` and `local-vector-batch` stay available as compatibility APIs.

## Release process

1. Merge reviewed work into main; run cross-platform CI and local source tests.
2. Keep the three version declarations aligned: pyproject, Python __version__, npm.
3. Build with `python -m build`; run `twine check` and `scripts/release_checks.py`.
4. Install the wheel in a clean environment outside the checkout. Exercise CLI,
   a real PDF, full-document output and MCP stdio parse/status/read.
5. Verify the npm package allowlist, tests and pinned-wheel launcher.
6. Push an annotated `v…` tag. The Release workflow reruns tests, builds assets,
   publishes GitHub artifacts, then publishes npm (`next` for RC; `latest` for stable).
   Publishing uses the repository NPM_TOKEN secret or a configured npm trusted publisher.
7. Verify npm metadata, GitHub asset hashes and installation from the public artifacts.

The active npm configuration reuses the existing BuildView publishing credential
as PDF2Data’s own `NPM_TOKEN` Actions secret. It was transferred encrypted to
PDF2Data’s repository public key; no plaintext was logged or committed. This token
has publishing permission and 2FA bypass, unlike a normal npm login session.
Register npm trusted publishing for this repository/workflow to replace the token
credential with OIDC.
[Official npm guidance](https://docs.npmjs.com/trusted-publishers).

## Stable-release gates

| Priority | Improvement | Completion evidence |
| --- | --- | --- |
| P0 | One guided setup command for OCR/languages and a concise doctor report | Fresh macOS/Linux/Windows runs identify and install only selected backends; native PDF path works without models |
| P0 | Simplify common commands around parse, inspect, export and workspace | Existing advanced commands remain compatible; CLI/UI/MCP produce equivalent source-bound artifacts |
| P0 | Durable publisher configuration and artifact verification | OIDC configured; clean-machine installation from released wheel and npm package; no dependency on a checkout |
| P0 | Artifact schema/version compatibility and migration tests | Old exports remain readable, corrections create new snapshots, stale caches are explicit |
| P1 | Document-family extraction profiles and an annotated holdout corpus | Per-category field/relationship/condition recall, unit/scope accuracy, appropriate abstention and OCR/layout metrics reported separately |
| P1 | Source review and repair integrated into the compact viewer | Source/parsed region links, unresolved cells and correction history are tested; no hidden content clipping |
| P1 | Optional export profiling with DuckDB/NetworkX | Reproducible missing-field, table-group, relationship and numeric-consistency diagnostics on multiple source families |
| P2 | Optional Cytoscape relationship inspector | Focused document/table/footnote/condition views link back to physical-page evidence; core parsing has no graph-UI dependency |

## References used

- [uv isolated tools and extras](https://docs.astral.sh/uv/guides/tools/).
- [Official MCP SDK and Python requirements](https://py.sdk.modelcontextprotocol.io/).
- [MCP stdio and Streamable HTTP](https://py.sdk.modelcontextprotocol.io/run/).
- [PyPA publishing and attestations](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

No PyPI publication is assumed: package ownership and trusted publishing must be
established before that channel is enabled. GitHub wheels provide the engine for
the npm launcher in this release.
