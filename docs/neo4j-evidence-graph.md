# Optional Neo4j evidence graphs

PDF2Data can project cached L1 evidence into an inspectable graph and explicitly import it into a local Neo4j instance. No new OCR, LLM inference, source modification, entity merging or legal approval occurs. Neo4j is optional; building/verifying bundles uses the standard library and existing L1 exporter.

Install the optional Python driver:

```bash
pip install 'PDF2Data[pdf,neo4j]'
```

Install/start a compatible Neo4j 5.x server separately. PDF2Data does not download a database, start a service or change server authentication. The integration was exercised against local Community 5.26.0 with the 5.28.8 Python driver.

## Build, check, import and inspect

```bash
pdf2data graph build --packages /absolute/immutable-l1 --output /absolute/new-graph --json
pdf2data graph verify --bundle /absolute/new-graph --json
# Set NEO4J_PASSWORD in the environment without placing it in shell history.
pdf2data graph import --bundle /absolute/new-graph --uri bolt://127.0.0.1:7687 --json
pdf2data graph inspect --snapshot SNAPSHOT_ID_FROM_BUILD --uri bolt://127.0.0.1:7687 --json
```

The output is a new `graph.json`, with a content-derived snapshot ID. Existing directories are never replaced. L1 integrity verification runs before projection. Native page/table/unit payloads, source references, hashes, quality and readiness are retained. Graph export does not change source holds or grant semantic/query readiness.

Nodes: Document, Artifact, Page, Table, Unit, Reference, Assertion, Organization and Category. Structural relationships are distinguished from candidate semantic assertions. Native table cells remain in table payloads; this version does not independently resolve their semantics or create a node for every cell. It also does not infer logical document families, versions, issuer identities or current law.

The built-in conservative literal scanners find file identifiers such as `L-1/261/2021/CERC` and docket identifiers such as `276/MP/2025`. They emit unresolved Reference nodes. Two documents mentioning the same identifier are **candidate neighbors**, not proven `amends` or `implements` links. Spaced or differently formatted references may be missed; this is not exhaustive reference extraction.

## Optional catalogue records

`--catalog /absolute/catalog.jsonl` adds inventory coverage, with or without L1 packages. Each line:

```json
{"document_id":"record-1","title":"Full printed or stored title","organization_id":"CERC","document_type":"Draft Regulation","url":"https://example.org/source.pdf","pdf_sha256":["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]}
```

Only `document_id` and `title` are required. IDs must be unique within the catalogue. Metadata retains `catalogue_only_unverified` status. Organization/category links are named `CATALOGUED_UNDER` and `CATEGORIZED_AS`: they do not assert actual authorship or semantic document type. Catalogue title references use `CATALOGUE_MENTIONS`, distinct from extracted-page `MENTIONS`. A catalogue row and an L1 document are deliberately separate nodes; matching declared IDs or hashes never silently merges legal records. Shared artifact hashes let a reviewer inspect duplicate bytes while retaining every binding.

Catalogue counts are not distinct PDF or distinct legal-instrument counts. Optional metadata may contain sensitive URLs/paths; explicitly choose a permitted Neo4j server before import. The default accepts loopback only; CLI users can opt into a remote server using `--allow-remote` and its appropriate encrypted URI.

## Candidate semantic assertions

`graph build --assertions /absolute/assertions.json` accepts a JSON array from a researcher/model/tool:

```json
[{"source_id":"draft","target_source_id":"principal","predicate":"proposes_to_amend","page":1,"exact_quote":"EXACT SUPPORTING TEXT FROM THE EXTRACTED PAGE","asserted_by":"researcher"}]
```

Both endpoints must resolve uniquely within the input bundle. The source must have an extracted L1 page. An optional `source_span: [start,end]` uses Unicode code-point offsets; repeated quotes require an explicit interval. Missing text, wrong pages, invalid offsets or ambiguous endpoints reject the entire build before publication.

Assertions always remain `candidate`, including when an input claims to be verified. A quote proves only its literal presence: a reviewer must still check the predicate, target identity, direction, speaker, dates, exceptions and source quality. Each Assertion connects to SUBJECT, OBJECT and SUPPORTED_BY nodes. No LLM-authored Cypher is executed. Unknown targets should be resolved before submission or retained outside this assertion adapter as unresolved research notes.

## Neo4j storage and inspection

The integration owns only `PDF2DataSnapshot`, `PDF2DataNode`, `HAS_NODE` and `PDF2DATA_LINK`. Domain kinds and predicates are properties, avoiding untrusted dynamic labels/types. Nested provenance is retained losslessly in `properties_json`; `title` and `source_id` are directly queryable. For example:

```cypher
MATCH (d:PDF2DataNode {snapshot_id: $snapshot, kind: 'Document'})
      -[r:PDF2DATA_LINK]->(other)
RETURN d.title, r.predicate, other.title
LIMIT 50;
```

Imports are snapshot-scoped, idempotent and use parameterized values. Two uniqueness constraints are installed on the adapter's labels; data is loaded in bounded, idempotent transactions (default 500 records; CLI `--batch-size` range 1..5000). Other graphs are not deleted or rewritten. A failed import can leave schema constraints and partial records with snapshot status `loading`; repeat the same import to resume safely. Inspection excludes incomplete snapshots. Only after exact node/edge counts match does a final transaction mark the snapshot `ready`. This version still loads the bundle JSON in client memory; very large page corpora should be partitioned by family pending further memory measurements.

`inspect` returns kind/relationship counts plus at most 50 page-reference groups and 50 catalogue-title-reference groups. These are predefined read-only queries. Arbitrary database queries, automatic publishing, review-state promotion, full graph-RAG and model inference are outside this version.

## MCP

`pdf2data_graph(run_id, operation='build'|'import'|'inspect')` works only on a run owned by the configured PDF2Data workspace. It builds a graph beside the existing extraction, not within immutable L1 output. Database operations require server-side `PDF2DATA_NEO4J_URI`, `PDF2DATA_NEO4J_USER` (default `neo4j`), `PDF2DATA_NEO4J_PASSWORD` and optionally `PDF2DATA_NEO4J_DATABASE`. MCP does not accept arbitrary connection URIs, credentials, file paths or Cypher from document/model content. MCP currently permits loopback servers only. A graph import is an explicit write operation.

## Validation

Run `pytest tests/test_evidence_graph.py tests/test_query_export.py tests/test_mcp_server.py`. Tests cover immutability, stable snapshots, page counts, reference separation, catalogue/type uncertainty, quote validation, endpoint resolution, hash tampering, dangling edges, credential/remote boundaries and MCP run ownership.

For a live Neo4j round trip, set `PDF2DATA_TEST_NEO4J_URI` and `PDF2DATA_TEST_NEO4J_PASSWORD` to a disposable local server, then run the optional integration test. It uses namespaced snapshot nodes and does not delete unrelated data.
