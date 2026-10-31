# Source structure required for legal-PDF RAG

Reviewed 31 October 2026. Scope: the six-page supplementary explanatory memorandum
dated 26 October 2026 and the existing local PDF2Data implementation. The source
is not the final notification, the complete fifth-amendment draft or the principal
regulations. No Cloudflare or authoritative legal database changes were made.

## Decision

Preserve a single source-linked document structure before chunking. Create separate
readable and searchable projections from that structure, then evaluate evidence
coverage and answer claims. Retain original page images for inspection and selective
visual retrieval. A higher OCR benchmark score alone does not establish better RAG.

This is an engineering recommendation informed by the research below and the
source review. It is not a claim that this architecture or the cached local model
is the current overall state of the art. The reviewed research spans different
corpora, dates, metrics, hardware and models; their reported scores are not directly
comparable to our 70-PDF benchmark.

## Research and what it actually supports

| Primary source | Relevant evidence | Application here / limitation |
| --- | --- | --- |
| [OHRBench, ICCV 2025 / v3 paper](https://arxiv.org/html/2412.02592v3), sections 4.2–4.3 | Recognition noise harms retrieval and generation. OCR edit-distance rankings do not reliably predict RAG rankings. Table-format effects differ between retrieval and generation; merged-cell support matters. | Keep canonical cells/spans, use a separate search projection, and evaluate end-to-end. The paper does not establish a universally best serialization or a guaranteed gain for these Indian regulatory PDFs. |
| [LegalBench-RAG](https://arxiv.org/html/2408.10343v1) | Measures precise relevant source spans rather than merely retrieving the correct document or large chunks. | Score span precision/recall and source grounding; return a small sufficient evidence set. Its legal corpora do not validate our company-impact rules. |
| [DoclingDocument documentation](https://docling-project.github.io/docling/concepts/docling_document/) | Provides document hierarchy, body/furniture separation, layout provenance and typed items. | Preserve the structural representation instead of reducing it to independent text crops. This is a format capability, not a guarantee of correct legal hierarchy. |
| [Docling chunking documentation](https://docling-project.github.io/docling/concepts/chunking/) | Hierarchical chunks carry headings/captions; HybridChunker adds tokenizer limits and supports repeated table headers. | Trial structure-aware chunks with retained row headers. Do not silently omit a long header to meet a token limit; use an explicit alternate projection or overflow state. |
| [Docling heading levels](https://docling-project.github.io/docling/usage/heading_levels/) | Optional hierarchy uses bookmarks, numbering and style. It is disabled by default and primarily updates detected headings. | Installed Docling 2.144.0 contains HeadingHierarchyOptions. A flag alone cannot recover quotations, unnumbered clause continuations, missing-PSA exceptions or illustration scenarios. |
| [M3DocRAG](https://arxiv.org/abs/2411.04952) | Evaluates multimodal retrieval and answering across pages/documents, preserving visual evidence that text extraction can lose. | Keep original rasters and test a visual evidence path for difficult pages. Its results do not prove local GPU cost or legal correctness. |
| [ViDoRe v3, revised April 2026](https://arxiv.org/abs/2601.08620v2) | Human-verified queries include relevant pages, bounding boxes and answers; the evaluated visual/hybrid approaches improve several measures but retain grounding failures. | Evaluate page retrieval, region grounding and answer correctness separately. Do not adopt a visual-only system merely from a leaderboard score. |
| [VL-DocIR, October 2026 preprint](https://arxiv.org/abs/2610.17057v1) | Explicitly evaluates evidence-complete retrieval across single pages, multiple pages and documents; multi-document evidence remains difficult. | Measure whether all required evidence is retrieved for a query, not only whether one relevant page appears. New preprint; no local replication or dataset integration is claimed. |
| [MIDR, September 2026 preprint](https://arxiv.org/abs/2609.01316v1) | Studies source-grounded multimodal enrichment at indexing time with fielded lexical/dense retrieval as an alternative to serving-time visual indexes. | Trial bounded table/header projections and other grounded index fields. Generated descriptions remain derived candidates and must not replace original legal text. Reported memory/latency benefits do not establish ours. |
| [Legal RAG Bench, March 2026](https://arxiv.org/abs/2603.01710v1) | A factorial retrieval/generation evaluation finds retrieval a major driver of results in its criminal-law benchmark. | Hold the generator constant when testing extraction and retrieval changes; diagnose evidence absence separately from reasoning failure. Findings are corpus-specific. |
| [OmniDocBench official implementation](https://github.com/opendatalab/OmniDocBench) | Separately evaluates text, tables, formulas and reading order. | Add structural evaluation beyond the current literal/neighbour probes. Pin the dataset and evaluator before any run; no OmniDocBench score has been measured here. |

## What the original-to-output review found

The map binds all **6 physical pages and 62 exported blocks** to saved original
rasters. There are **51 body blocks and 11 furniture blocks**. Both page-5 tables
retain their 2×9 grids and all 36 cells. These counts establish inventory, not
substantive completeness.

All six original page images were inspected. A diagnostic comparison of existing
native glyph artifacts against the text inside output body boxes found mostly
punctuation/format differences, alongside a duplicate date projection. Non-whitespace
native glyphs outside those body boxes were the recurring footer and closing stars.
Native decoding is not an independent visual gold standard; this does not certify
every character. The strongest observed problem is lost **relationships**, rather
than demonstrated loss of large substantive passages in this specific PDF.

| Original pages | Relationship absent from current output | Likely RAG failure to test |
| --- | --- | --- |
| 1 | One date key/value region contains two separately exported child readings | Duplicate evidence and repeated dates |
| 1–2 | April OM quote → paragraph 2 → conditions (i), (ii), (iii), joined by “and” | Retrieving one condition as sufficient eligibility |
| 2–3 | October quote closes on page 2; author commentary on page 3 remains under clause 1.3 | Wrong speaker or legal authority attributed to a passage |
| 3–4 | Clause 1.7 direct-supply branch continues into intermediary supply and no-PSA exclusion | PPA-only answer omits the back-to-back PSA requirement |
| 4–5 | Illustration 1 caption → first table → scenarios (a), (b), (c) | A scenario is answered without its original input dates |
| 5–6 | Illustration 2 caption/table → following scenarios | “Not effective” and page-6 assumptions separated or confused with Illustration 1 |
| 5 | Regulatory “Table 1 of Regulation 13(2)(a)” is an external citation | Wrongly resolves to the first local illustration table |
| 6 | “Suppose” scopes the 1.12.2026 commencement assumption and later filing examples | Hypothetical date becomes an actual effective date or universal deadline |

Full evidence, exact block references, proposed fixes and validation cases are in
`october-em-review-notes.json` and the generated `page-map.md`. Its 16 edges are
**supplied source-review annotations**, not automatically inferred extraction output.

## Concrete implementation gap

The region path in `structured.py:detect` calls Docling separately for each physical
page and reduces its result to region kinds/coordinates and candidate XY-cut order.
Whole-document structure is not retained by that reduction. `table_reconcile.py`
then writes its own Markdown and a flat `<pre>`-based HTML renderer for non-table
blocks. Detection success and correct table grids therefore do not produce a
document tree or legal relationship graph.

The repository already has `document_blocks.py` and `readable_export.py`: candidate
parent units, exact page spans, some cross-page paragraph continuation, list depth,
headings, source anchors and `relations.json`. These are useful reusable components,
but their current heuristics are not a complete solution to the 12 review findings.

**Build an adapter to this shared contract. Do not create a third independent
document renderer.** The new `pdf-document-map` is a diagnostic viewer of immutable
outputs, not the missing structure extractor or an alternate authoritative export.

## Proposed pipeline and ownership

| Stage | Required output | Validation before downstream use |
| --- | --- | --- |
| Source intake | PDF hash, document version, physical page inventory, original rasters | Hash/page identity; missing pages fail visibly |
| Layout and recognition | Regions, styles when observed, source coordinates, independent readings and unresolved content | Image binding; text/glyph differences; critical-value checks; table structure; unrepresented regions |
| Physical structure assembly | Document/body/furniture tree; heading/list parents; quote boundaries; continuation and caption/table associations | Character conservation; no duplicate rendering of overlapping alternatives; valid links; multi-page evidence; ambiguity remains explicit |
| Legal interpretation candidates | Cited instrument and status, actor/action/condition/exception/outcome, scenario and date roles | Exact evidence for every field/edge; proposed/quoted/hypothetical distinguished; unresolved citations remain unresolved |
| Search projections | Precise text units with heading path; table-row field projection; illustration IDs; optional derived visual descriptions | Each projection resolves to source spans/cells; preserve raw text; tokenizer limits are checked without clipping |
| Retrieval | Lexical + dense candidates, reranking, bounded structure expansion; optional image route | Precise and complete evidence at a fixed token budget; leakage/latency tests |
| Answer or draft | Supported claims with citations and missing-information fields | Per-claim evidence sufficiency, scope, amounts/dates/negations and temporal checks; unsupported impact stays unknown |

The parser owns source fidelity and physical relationships. Legal interpretation
and client applicability belong in separate candidate layers. A parser should not
declare that a company qualifies for a waiver merely because it found a date/table.

### Canonical source contract to add

Keep stable document, physical-page, block, span, table and cell identities. A
multi-page clause has multiple exact spans, not a synthetic flattened source page.
Edges should include `type`, endpoints, evidence spans, extraction method/version,
candidate/review status and ambiguity. Suggested physical edges:

- `contains`, `next_in_source`, `alternative_reading_of`
- `continues`, `caption_of`, `member_of_illustration`
- `list_child_of`, `within_quote`, `explains`

Keep legal edges such as `condition_of`, `exception_to`, `references` and
`scenario_variant_of` separate and reviewable. A graph data model does not require
a graph database. Local relational tables/JSON are sufficient for initial tests;
GraphQL changes the API, not extraction fidelity or retrieval quality.

### Real example: the second illustration

Observed source inputs, not approved applicability facts:

```json
{
  "document_sha256": "17b7dd0fe13e4e4f1ca5d4793c7485d66bb5e1b527f9b846a44564577f4d49ff",
  "container": "Illustration 2",
  "caption_region": "56bebe798f88ed13c0d7:p5:r4",
  "table_region": "56bebe798f88ed13c0d7:p5:r5",
  "table_row": 1,
  "gna_cell": {"column": 7, "raw_text": "Not effective"},
  "cod_cell": {"column": 8, "raw_text": "30.07.2026"},
  "explanation_regions": [
    "56bebe798f88ed13c0d7:p6:r0",
    "56bebe798f88ed13c0d7:p6:r1",
    "56bebe798f88ed13c0d7:p6:r2",
    "56bebe798f88ed13c0d7:p6:r3",
    "56bebe798f88ed13c0d7:p6:r4"
  ],
  "relationship_status": "supplied_review_example_not_implemented_extraction"
}
```

Row/column indexes above are zero-based, including the header row. Add stable cell
IDs in the canonical contract; do not replace the current raw cell with an invented
normalized date. A candidate date normalizer may separately represent GNA as
`status=not_effective, date=null` with the original literal and evidence.

For the question **“Does this document say the amendment took effect on 1 December
2026?”**, retrieve the page-6 scenario and document status. The supported response
is that the illustration assumes that date; this memorandum alone does not establish
actual commencement. No larger model or better similarity score can correct a
pipeline that loses the word “Suppose” or its scope.

For **“What happens when the original agreement terminates?”**, retrieve clause
1.8(b) plus Illustration 1(b) and its scenario identity. The memorandum describes
loss of eligibility under the proposed route, while the illustration describes a
fallback to the existing trajectory for the remaining period. Avoid an unqualified
answer that every transmission waiver disappears. This is a reading of this draft
memorandum, not advice on an actual project or confirmation of current law.

## Search and display must use different projections

1. Keep canonical cells, spans, merged structure and original images for fidelity.
2. Render HTML/Word/Markdown from the same structure. Styling must not define truth.
3. Index precise clause/list units with document status, heading/quotation path and
   parent IDs. Do not index duplicate parent/child alternative readings as separate facts.
4. For tables, trial compact **header → value** row projections with caption,
   illustration, table/cell IDs and exact raw values. Do not embed only large HTML
   strings. For oversized tables repeat required header context and retain row boundaries.
5. Retrieve small units, then expand only needed parents, continuation spans,
   exceptions and illustration scenarios within a measured evidence budget.
6. Keep original page crops available to a visual verifier/retriever for ambiguous
   cells/layout or missing text. Test this route separately; do not download/change
   local models or replace the current index based solely on research.

Derived summaries, synthetic questions or visual descriptions can be search aids,
but their IDs must lead back to original evidence. Their generated assertions must
not serve as legal citations. Date roles and conditional rules need separate
validation; a bag of dates is not a legally valid timeline.

## Evaluation: prove the RAG benefit

Freeze the current PDF/output hashes and review fixtures before changing structure.
The example is a development fixture, not a blind legal holdout. Use the same query
set, corpus, embedding model, reranker, generator settings and context-token budget
across comparisons; record hashes and actual timing/device. Keep failures in the
denominator. Split cases by source/instrument family for later independent evaluation.

| Variant | Change isolated | Main question |
| --- | --- | --- |
| A | Existing extracted flat body/current retrieval | Measured baseline; no new relationship annotations supplied at query time |
| B | Same recognized content, source-tree and table search projections | Does representation improve retrieval without more model knowledge? |
| C | B plus bounded parent/continuation/exception/scenario expansion | Does complete evidence improve at the same context budget? |
| D | C plus selective original-image evidence | Does visual grounding fix remaining table/layout failures at acceptable latency? |

Do not compare a human-annotated oracle graph against flat retrieval and describe
the difference as automated parser performance. It is an upper-bound diagnostic.
Evaluate automatically produced relationships against held-out source annotations
before attributing downstream gains to the extraction system.

Required measures:

- **Extraction:** critical literal accuracy; duplicate/omission counts; text and
  reading-order measures; table TEDS/TEDS-S plus critical header/value association;
  relationship precision/recall and multi-page grouping accuracy. TEDS alone does
  not establish correct legal meaning.
- **Retrieval:** source-span precision/recall, Recall@k/nDCG@k, and **complete-evidence
  rate**: every required supporting condition/exception/span for the query is present.
  An unresolved external citation is not a complete legal evidence bundle.
- **Answers:** independently checked claim support, condition/exception completeness,
  critical amount/date/negation accuracy, quote/proposal/hypothetical attribution,
  citation correctness and appropriate abstention.
- **Operations:** ingestion/serve latency, token use, memory, retry/error rates,
  source-review workload and reproducible device/model/version receipts.

Use deterministic critical-field checks plus independent human review. A local LLM
can assist with grading; model agreement cannot be the only legal correctness judge.
The current 71.75% OCR rule macro and seven legal probes remain useful but **are not
relationship extraction, complete-evidence retrieval or answer correctness scores**.

## Review of the suggested parsing-library article

Reviewed 31 October 2026:
[Transform Any Document into LLM-Ready Data](https://martinuke0.github.io/posts/2026-01-06-transform-any-document-into-llm-ready-data-top-parsing-libraries-revealed/).
It is useful for library discovery and reinforces structure preservation, multiple
export formats, local processing and validation. Its ranking is not a reproducible
accuracy/RAG benchmark: it does not specify a test corpus, pinned configurations,
measured metrics or comparable results. It does not demonstrate recovery of this
PDF's cross-page clauses, quote attribution, exceptions or illustration associations.

Recommendations were checked against primary documentation and the installed
Docling API rather than copying its snippets.

| Library | Verified capability / correction | Decision for PDF2Data |
| --- | --- | --- |
| Docling | The documented export is `result.document.export_to_markdown()`, not the article's `result.to_markdown()`. The installed API confirms the latter is absent. Picture descriptions require explicit enrichment configuration; importing an Ollama client does not enable them. [Quickstart](https://docling-project.github.io/docling/getting_started/quickstart/), [enrichment configuration](https://docling-project.github.io/docling/usage/enrichments/). | Reuse the existing installed engine; retain/adapt its structured document representation and source provenance. Keep the tested local reader for recognition. Add image enrichment only as a bounded, separately evidenced candidate stage. |
| Microsoft MarkItDown | Official Python usage instantiates `MarkItDown` then calls its conversion method. CLI file output uses `-o` or shell redirection, rather than a second bare output filename. The project describes itself as a Markdown conversion utility and cautions that it may not be best for high-fidelity human-facing conversion. [Official repository](https://github.com/microsoft/markitdown). | Potential lightweight comparison or future Office-format adapter. No evidence here justifies replacing our legal-PDF recognition, exact cell/span model or source-view export. |
| NanoNets LLM-Data-Converter | Its README confirms default cloud processing, schema/field-oriented JSON, explicit local preferences and CUDA for its documented GPU mode. Enhanced JSON can use Ollama and fall back when unavailable. [Official repository](https://github.com/NanoNets/llm-data-converter). | Borrow the consistent conversion/export interface and explicit schema concept. A new engine would need a source-fidelity comparison, explicit local execution and Apple Metal verification. Schema-shaped JSON alone does not prove field values or relationships. No package was installed or document uploaded. |
| Monkt | Official documentation describes an authenticated hosted transformation API. This review did not verify the article's classification of it as a local library or establish an open-source offline implementation. [Official API documentation](https://monkt.com/api/documentation/). | No dependency proposed for the current local open-source pipeline. Its service description is not evidence that it solves our source-binding requirements. |

One further correction from the local environment: Docling 2.144.0's default
`PdfFormatOption.backend` is `docling.backend.docling_parse_backend.ThreadedDoclingParseDocumentBackend`,
not the PdfPlumber backend asserted in the article. This was checked by inspecting
the installed class and by instantiating the format option, without converting a
PDF or running recognition.

### Concrete improvement retained from this review

Use one source-backed conversion result with several projections: canonical JSON
for blocks/spans/cells and relationships; Markdown/HTML/DOCX for reading; compact
clause and header/value text for indexing. All projections must refer to the same
original evidence. Do not make a generated JSON summary the canonical document.

The next implementation remains the **canonical region-to-block adapter and shared
exporter**. Validate the duplicated date, clause 1.7 continuation, both illustration
associations and hypothetical-date scope before indexing the new projection. Then
run the fixed-budget A/B/C retrieval experiment described above. This article adds
useful interface ideas and corrected integration details; it does not establish an
accuracy improvement or require replacing the current engine.

This was a research/API review only. No model swap, new OCR run, database mutation
or new RAG performance measurement was performed.

## Build order

1. Complete this source map and freeze targeted questions/required evidence.
2. Implement canonical region-to-block adapter, overlap ownership and shared renderer.
3. Add supported cross-page, quote/list and illustration associations with explicit
   ambiguity; validate against this fixture and separate unseen sources.
4. Generate reversible search projections in the isolated local index, coordinated
   with the existing retrieval-policy contract. Keep old retrieval as the baseline.
5. Run A/B/C, then the selective visual D experiment only where the diagnostics justify it.
6. Promote only demonstrated improvements; leave source/interpretation review gates explicit.

This turn implements **the diagnostic map/CLI and research-backed specification**.
It does not claim these structure repairs or the RAG ablations have been executed.
