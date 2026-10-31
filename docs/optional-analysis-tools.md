# Optional pattern analysis and relationship tools

NetworkX, DuckDB and Cytoscape.js fit the exported-data boundary. They can expose
missing structure and repeated patterns without changing the original extraction.

| Tool | Useful integration | Extraction decisions it can support |
| --- | --- | --- |
| DuckDB | Query page/block/metadata JSON, compare field frequencies by document family and source version; reconcile typed monetary observations using DECIMAL | Find absent date/reference labels, unusual unit combinations, missing table rows and incompatible period/scope joins |
| NetworkX MultiDiGraph | Preserve parallel typed relationships, find dangling references and validate selected containment/dependency graphs | Check heading hierarchy, table/header/footnote links, clause dependencies and stale calculations |
| Cytoscape.js | Review-focused graph with selectable nodes/edges and evidence links | Let reviewers inspect why a field was associated with a header, project, condition or source passage |

A frequency is a candidate pattern. It does not establish a field's meaning or
legal effect. Learning a new extraction profile needs labelled examples and
held-out evaluation. Keep source observation, normalization, domain interpretation
and derived calculation separate. Legal citation graphs may legitimately contain
cycles; only the intended containment/calculation dependency graph should be
checked for acyclicity. A numeric match does not resolve entity or project scope.

DuckDB's DECIMAL addition/subtraction/multiplication is exact within its declared
precision; DECIMAL division returns floating point. Use explicit scale/range
validation and a reviewed Decimal/rational calculation for division/rounding.
Do not blindly SUM a flattened table: parent totals, period charges and credits
must have separate roles and matching scope/periods first.

## Runnable example

After `pdf2data export-readable`, run the optional example:

```bash
uv run --with duckdb --with networkx python examples/profile_exports.py \
  --package /absolute/readable-export --output /absolute/new-analysis
```

It verifies the export manifest for the consumed JSON, profiles node/table shapes
with DuckDB, checks existing source-structure links with NetworkX, and writes
`profile.json` plus `cytoscape-elements.json`. The latter is ready for a Cytoscape
`elements` input. No graph widget or graph database is added to the parser.
The example does not claim to verify the source's legal relationships or to learn
an extraction model. It leaves all source files unchanged.

For Powerlex, company/site/matter relationships and calculation eligibility stay
in the consumer layer. Generic PDF2Data exports provide the stable source IDs,
geometry, metadata, tables and candidate links that layer needs.

Primary documentation: [NetworkX graph types](https://networkx.org/documentation/stable/reference/classes/index.html),
[DuckDB JSON](https://duckdb.org/docs/stable/data/json/overview),
[DuckDB numeric behavior](https://duckdb.org/docs/stable/sql/data_types/numeric),
[Cytoscape.js JSON and graph rendering](https://js.cytoscape.org/).

## Executed check

The example was run against a retained three-page regulatory calculation-note
export using DuckDB 2.0.0 and NetworkX 3.7: 1,324 nodes, 1,757 edges, zero dangling
references, and an acyclic containment projection. Four raw table candidates
were retained. These are structural observations, not independent semantic-table
counts or verified legal relationships. The original export was unchanged.
