# PDF2Data L1 handoff — 30 October 2026

All nine originals and all 98 physical pages were parsed. A quality flag does not mean a page was dropped or is unavailable. Raw text, selected text, glyph geometry, table candidates, source locations and original PDFs remain available for every page.

## Executed commands and results

The complete [CLI reference](cli-reference.md) documents all 39 commands. This pass used `legal-l1`, `verify-legal-l1`, `l1-qwen-prepare`, `l1-qwen-run --device gpu`, `l1-qwen-validate` and `l1-qwen-reproof` through the supported PDF2Data CLI.

- Pinned cached Qwen processed all 32 originally flagged pages offline; none hit the token cap.
- Seven full-page readings passed exact character, word, number and operator comparisons. Twenty-five differed; differences include formatting and model changes, not necessarily errors in the stored extraction.
- Qwen reproduced both supplementary EM illustrations as distinct 2×9 grids, matching all 36 source cells, including the different GNA-effectiveness values.
- Initial unchanged-text Qwen reproof accepted three additional pages: 69 accepted / 29 flagged.
- Fixing source-bound PDFium control-to-hyphen mapping raised native acceptance to 77/98.
- Fixing repeated subscript rows with a uniquely anchored complete source band raised native acceptance to **81/98**.
- The latest immutable extraction is `out/pdf2data-source-line-fixed/`; `run-report.json` contains all nine verified document receipts.
- Strict content-readiness returns exit 3 while flags remain. Ordinary extraction succeeds and retains all data.
- 355 tests passed; one optional `docling_core` test skipped.

No policy threshold was relaxed. Source hyphens require exact source glyphs and a unique independent pypdf line. Subscript repairs require PDFium/pypdf agreement and exact conservation of source characters. Raw readings and repair receipts remain available. No legal claims were promoted.

## Remaining 17 physical pages

| Source | Pages | Why source-quality certification is still incomplete |
|---|---|---|
| Serentica India 2 licence | 1–5 | Small repeated footer emblem leaves visible ink outside text glyph boxes. Text is present. |
| Serentica India 2 licence | 6 | Footer/signature visual regions plus word/number-order disagreement. |
| Serentica CERC RoP | 1–2 | Small footer emblem remains outside glyph coverage. Both pages' text is present. |
| October supplementary EM | 5 | Two table grids need page-level structure/reading-order proof. Qwen matched their 36 cells, but the flattened page reading and grid ink still fail the page gate. |
| October supplementary draft | 2 | Amendment/table layout, visual rules and reading-order differences. |
| July EM | 2, 4, 8 | Unaccounted table/formula or other visual regions. |
| July EM | 10, 18 | Independent native reading/order still differs; a model reading cannot silently replace it. |
| July draft | 4 | Visual/formula layout and operator-order differences. |
| CERC amount-conflict order | 24 | Table grid/reading order remains unresolved. |

These are diagnostic flags, not 17 unavailable pages. The next layer should ingest all processed data into an isolated candidate/staging store, preserve these diagnostics, and keep legal interpretation and publication approval separate. It must not infer a verified legal claim from parse success, or refuse to store a page merely because its footer or table requires more proof.

## GPU and repository

Repository: https://github.com/rudraptpsingh/PDF2Data . Local checkout: `/Users/rp/.codex/worktrees/legal-l1-build/PDF2Data`.

The runtime reports `Device(gpu, 0)` and Metal available. Explicit-GPU continuation receipts record device and peak memory (about 7.13 GB in the observed continuation). The first 24 completed readings predate per-reading device metadata and remain labelled as such; they are not retroactively stamped as measured GPU runs.

The original Python namespace/command and an old-worktree-path symlink remain compatible. Cloudflare services, deployments, remote databases and indexes were untouched. Local commits have not been pushed.
