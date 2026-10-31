"""Optional, evidence-preserving graph projection; no extraction or legal approval.

Neo4j stores immutable snapshot-scoped nodes. Semantic links are assertions,
not silently accepted document-to-document edges. This module imports no driver
until a user explicitly requests a database operation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse

SCHEMA = 'pdf2data.evidence-graph.v1'
KINDS = {'Document', 'Artifact', 'Page', 'Table', 'Unit', 'Reference',
         'Assertion', 'Organization', 'Category'}
LINKS = {'HAS_ARTIFACT', 'CONTAINS', 'MENTIONS', 'SUBJECT', 'OBJECT',
         'SUPPORTED_BY', 'CATALOGUED_UNDER', 'CATEGORIZED_AS', 'CATALOGUE_MENTIONS'}
# Deliberately narrow literal-reference detectors. A matching identifier is
# not a determination that one instrument amends or implements another.
REFERENCES = (
    ('file_identifier', re.compile(r'\b[A-Z]-\d{1,4}/\d{1,4}/\d{4}/[A-Z][A-Z-]{1,20}\b')),
    ('docket_identifier', re.compile(r'\b\d{1,5}/[A-Z]{1,5}/\d{4}\b')),
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def build(output, packages=None, catalog=None, assertions=None):
    """Build a new graph bundle from verified L1 and/or declared catalogue rows.

    Catalogue JSONL: document_id, title, organization_id, document_type, url.
    Organization/type values are declared metadata, never verified authorship.
    Assertions: source_id, target_source_id, predicate, page, exact_quote;
    source_span optional [start,end]. All remain candidates, even if supplied
    with a claimed verified status. Source hashes are bound by the L1 exporter.
    """
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Graph output must be a new directory')
    if not packages and not catalog:
        raise ValueError('Supply L1 packages or catalogue JSONL')
    if packages and output.is_relative_to(Path(packages).resolve()):
        raise ValueError('Graph output must be outside immutable source packages')
    nodes, edges, source_ids, pages = {}, {}, {}, {}

    def node(kind, key, properties):
        identifier = digest([kind, key])
        value = dict(id=identifier, kind=kind, properties=properties)
        if identifier in nodes and nodes[identifier] != value:
            raise ValueError('Conflicting graph identity: ' + str(key))
        nodes[identifier] = value
        return identifier

    def edge(source, predicate, target):
        value = dict(source=source, predicate=predicate, target=target)
        identifier = digest(value)
        edges[identifier] = dict(id=identifier, **value)

    def source_id(alias, identifier):
        source_ids.setdefault(alias, set()).add(identifier)

    records = []
    if packages:
        from .query_export import export_query_data
        with tempfile.TemporaryDirectory(prefix='pdf2data-graph-') as directory:
            path = Path(directory) / 'records.jsonl'
            export_query_data(Path(packages), path)
            records = [json.loads(line) for line in path.read_text(encoding='utf8').split('\n') if line]
        documents = {}
        for record in records:
            if record['kind'] != 'document':
                continue
            payload = record['payload']
            declared = payload['source']['declared']
            doc = node('Document', record['id'], dict(
                source_id=record['source_id'], title=declared.get('title', record['source_id']),
                source_pdf_sha256=record['source_pdf_sha256'],
                package_sha256=record['package_sha256'],
                document_type=declared.get('family', 'generic'),
                metadata_state='declared', quality=payload['quality'], readiness=payload['readiness']))
            artifact = node('Artifact', record['source_pdf_sha256'], dict(sha256=record['source_pdf_sha256']))
            edge(doc, 'HAS_ARTIFACT', artifact)
            documents[record['package_sha256']] = doc
            source_id(record['source_id'], doc)
        for record in records:
            if record['kind'] == 'document':
                continue
            kind = {'page': 'Page', 'table': 'Table', 'unit': 'Unit'}[record['kind']]
            payload = record['payload']
            item = node(kind, record['id'], dict(payload=payload, source_ref=record['source_ref'],
                source_pdf_sha256=record['source_pdf_sha256'], source_id=record['source_id']))
            doc = documents[record['package_sha256']]
            edge(doc, 'CONTAINS', item)
            if kind != 'Page':
                continue
            page = payload['page']
            pages.setdefault((record['source_id'], page), []).append((doc, item, payload.get('text', '')))
            for ref_kind, pattern in REFERENCES:
                for match in pattern.finditer(payload.get('text', '')):
                    literal = match.group()
                    ref = node('Reference', [ref_kind, literal], dict(reference_type=ref_kind, literal=literal,
                        resolution_state='unresolved', legal_effect='not_inferred'))
                    edge(item, 'MENTIONS', ref)

    catalog_sha = None
    if catalog:
        raw = Path(catalog).read_bytes()
        catalog_sha = hashlib.sha256(raw).hexdigest()
        seen = set()
        for line in raw.decode('utf8').split('\n'):
            if not line.strip():
                continue
            row = json.loads(line)
            for field in ('document_id', 'title'):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError('Catalogue requires nonempty ' + field)
            if row['document_id'] in seen:
                raise ValueError('Duplicate catalogue document_id')
            seen.add(row['document_id'])
            doc = node('Document', ['catalog', catalog_sha, row['document_id']], dict(
                source_id=row['document_id'], title=row['title'], metadata=row,
                metadata_state='catalogue_only_unverified', document_type=row.get('document_type', 'unknown')))
            source_id(row['document_id'], doc)
            for ref_kind, pattern in REFERENCES:
                for match in pattern.finditer(row['title']):
                    literal = match.group()
                    ref = node('Reference', [ref_kind, literal], dict(reference_type=ref_kind, literal=literal,
                        resolution_state='unresolved', legal_effect='not_inferred'))
                    edge(doc, 'CATALOGUE_MENTIONS', ref)
            hashes = row.get('pdf_sha256', [])
            if not isinstance(hashes, list) or any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in hashes):
                raise ValueError('Catalogue pdf_sha256 must be a list of SHA-256 hashes')
            for sha in hashes:
                artifact = node('Artifact', sha, dict(sha256=sha))
                edge(doc, 'HAS_ARTIFACT', artifact)
            if row.get('organization_id'):
                org = node('Organization', row['organization_id'], dict(name=row['organization_id']))
                edge(doc, 'CATALOGUED_UNDER', org)
            if row.get('document_type'):
                category = node('Category', [row.get('organization_id'), row['document_type']],
                                dict(name=row['document_type'], organization_id=row.get('organization_id')))
                edge(doc, 'CATEGORIZED_AS', category)

    if assertions:
        values = load(assertions)
        if not isinstance(values, list):
            raise ValueError('Assertions must be a JSON array')
        for value in values:
            source, target = value['source_id'], value['target_source_id']
            if len(source_ids.get(source, ())) != 1 or len(source_ids.get(target, ())) != 1:
                raise ValueError('Assertion endpoint is missing or ambiguous')
            page = value['page']
            if type(page) is not int or page < 1:
                raise ValueError('Assertion requires a positive physical page')
            candidates = pages.get((source, page), [])
            if len(candidates) != 1:
                raise ValueError('Assertion requires one source-bound extracted page')
            doc, page_id, text = candidates[0]
            quote = value['exact_quote']
            if not isinstance(quote, str) or not quote.strip() or quote not in text:
                raise ValueError('Assertion quote does not match source page')
            span = value.get('source_span')
            if span is None:
                if text.count(quote) != 1:
                    raise ValueError('Repeated quote requires an explicit source_span')
                start = text.index(quote)
                span = [start, start + len(quote)]
            if (not isinstance(span, list) or len(span) != 2 or any(type(i) is not int for i in span)
                    or not 0 <= span[0] < span[1] <= len(text) or text[span[0]:span[1]] != quote):
                raise ValueError('Invalid assertion source_span')
            predicate = value['predicate']
            if not isinstance(predicate, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', predicate):
                raise ValueError('Invalid assertion predicate')
            props = dict(predicate=predicate, source_id=source, target_source_id=target,
                page=page, exact_quote=quote, source_span=span, review_state='candidate',
                asserted_by=value.get('asserted_by'), conditions=value.get('conditions'),
                evidence_check='literal_quote_only_semantic_review_required')
            assertion = node('Assertion', props, props)
            edge(assertion, 'SUBJECT', doc)
            edge(assertion, 'OBJECT', next(iter(source_ids[target])))
            edge(assertion, 'SUPPORTED_BY', page_id)

    graph = dict(schema_version=SCHEMA, nodes=sorted(nodes.values(), key=lambda r: r['id']),
                 edges=sorted(edges.values(), key=lambda r: r['id']),
                 input_catalog_sha256=catalog_sha, publication_eligible=False,
                 semantic_review_required=True)
    graph['snapshot_id'] = digest(graph)
    validate(graph)
    output.mkdir(parents=True)
    try:
        (output / 'graph.json').write_text(canonical(graph) + '\n', encoding='utf8')
    except BaseException:
        # No database writes occurred; a failed artifact is never a usable bundle.
        raise
    return dict(output=str(output), **summary(graph))


def validate(graph):
    if graph.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported graph schema')
    unsigned = {k: v for k, v in graph.items() if k != 'snapshot_id'}
    if graph.get('snapshot_id') != digest(unsigned):
        raise ValueError('Graph snapshot hash mismatch')
    if graph.get('publication_eligible') is not False or graph.get('semantic_review_required') is not True:
        raise ValueError('Graph must retain review gates')
    ids = set()
    for node in graph['nodes']:
        if node['kind'] not in KINDS or not isinstance(node['properties'], dict) or node['id'] in ids:
            raise ValueError('Invalid or duplicate graph node')
        ids.add(node['id'])
        if node['kind'] == 'Assertion' and node['properties'].get('review_state') != 'candidate':
            raise ValueError('Graph exporter cannot promote assertion review state')
    seen = set()
    for edge in graph['edges']:
        if (edge['predicate'] not in LINKS or edge['source'] not in ids or edge['target'] not in ids
                or edge['id'] in seen):
            raise ValueError('Invalid graph edge')
        seen.add(edge['id'])
    return graph


def summary(graph):
    return dict(snapshot_id=graph['snapshot_id'], nodes=len(graph['nodes']), edges=len(graph['edges']),
        by_kind=dict(Counter(n['kind'] for n in graph['nodes'])),
        by_relationship=dict(Counter(e['predicate'] for e in graph['edges'])),
        publication_eligible=False, semantic_review_required=True)


def connection(uri, user, password_env, allow_remote=False):
    parsed = urlparse(uri)
    if parsed.scheme not in {'bolt', 'bolt+s', 'bolt+ssc', 'neo4j', 'neo4j+s', 'neo4j+ssc'} or parsed.username or parsed.password:
        raise ValueError('Use a Neo4j URI without embedded credentials')
    if not parsed.hostname or (not allow_remote and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise ValueError('Neo4j defaults to loopback; remote servers require --allow-remote')
    password = os.environ.get(password_env)
    if not password:
        raise ValueError('Set the configured Neo4j password environment variable')
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise RuntimeError('Install PDF2Data[neo4j] for optional database operations') from None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
        driver.verify_connectivity()
        return driver
    except Exception:
        raise RuntimeError('Neo4j connection failed; check server, URI and credentials') from None


def import_graph(bundle, uri, user='neo4j', password_env='NEO4J_PASSWORD', database='neo4j', allow_remote=False, batch_size=500):
    graph = validate(load(Path(bundle) / 'graph.json'))
    snapshot = graph['snapshot_id']
    if type(batch_size) is not int or not 1 <= batch_size <= 5000:
        raise ValueError('batch_size must be 1..5000')
    def write_nodes(tx, rows):
        tx.run('UNWIND $rows AS row MERGE (n:PDF2DataNode {snapshot_id:$snapshot,id:row.id}) '
               'SET n.kind=row.kind,n.title=row.title,n.source_id=row.source_id,n.properties_json=row.properties '
               'WITH n MATCH (s:PDF2DataSnapshot {id:$snapshot}) MERGE (s)-[:HAS_NODE]->(n)',
               snapshot=snapshot, rows=rows).consume()
    def write_edges(tx, rows):
        tx.run('UNWIND $rows AS row MATCH (a:PDF2DataNode {snapshot_id:$snapshot,id:row.source}),'
               '(b:PDF2DataNode {snapshot_id:$snapshot,id:row.target}) '
               'MERGE (a)-[r:PDF2DATA_LINK {id:row.id}]->(b) SET r.predicate=row.predicate',
               snapshot=snapshot, rows=rows).consume()
    def finalize(tx):
        count = tx.run('MATCH (n:PDF2DataNode {snapshot_id:$id}) RETURN count(n) AS n', id=snapshot).single()['n']
        links = tx.run('MATCH (:PDF2DataNode {snapshot_id:$id})-[r:PDF2DATA_LINK]->() RETURN count(r) AS n', id=snapshot).single()['n']
        if count != len(graph['nodes']) or links != len(graph['edges']):
            raise ValueError('Imported graph counts do not match snapshot')
        tx.run('MATCH (s:PDF2DataSnapshot {id:$id}) SET s.status="ready",s.node_count=$nodes,s.edge_count=$edges',
               id=snapshot, nodes=count, edges=links).consume()
    with connection(uri, user, password_env, allow_remote) as driver:
        try:
            with driver.session(database=database) as session:
                session.run('CREATE CONSTRAINT pdf2data_node_identity IF NOT EXISTS '
                    'FOR (n:PDF2DataNode) REQUIRE (n.snapshot_id,n.id) IS UNIQUE').consume()
                session.run('CREATE CONSTRAINT pdf2data_snapshot_identity IF NOT EXISTS '
                    'FOR (s:PDF2DataSnapshot) REQUIRE s.id IS UNIQUE').consume()
                state = session.run('MERGE (s:PDF2DataSnapshot {id:$id}) '
                    'ON CREATE SET s.schema=$schema,s.publication_eligible=false,s.status="loading" '
                    'RETURN s.status AS status', id=snapshot, schema=SCHEMA).single()['status']
                if state == 'ready':
                    session.execute_write(finalize)
                    return dict(status='already_imported', **summary(graph))
                for offset in range(0, len(graph['nodes']), batch_size):
                    rows = [dict(id=n['id'], kind=n['kind'],
                        title=n['properties'].get('title', n['properties'].get('name', n['properties'].get('literal'))),
                        source_id=n['properties'].get('source_id'), properties=canonical(n['properties']))
                        for n in graph['nodes'][offset:offset + batch_size]]
                    session.execute_write(write_nodes, rows)
                for offset in range(0, len(graph['edges']), batch_size):
                    session.execute_write(write_edges, graph['edges'][offset:offset + batch_size])
                session.execute_write(finalize)
                return dict(status='imported', **summary(graph))
        except Exception:
            raise RuntimeError('Neo4j import failed; incomplete snapshot remains loading and can be resumed with the same bundle') from None


def inspect_graph(snapshot, uri, user='neo4j', password_env='NEO4J_PASSWORD', database='neo4j', allow_remote=False):
    with connection(uri, user, password_env, allow_remote) as driver:
        try:
            with driver.session(database=database) as session:
                exists = session.run('MATCH (s:PDF2DataSnapshot {id:$id,status:"ready"}) RETURN count(s) AS n', id=snapshot).single()['n']
                if not exists:
                    raise ValueError('Unknown or incomplete PDF2Data snapshot')
                kinds = session.run('MATCH (n:PDF2DataNode {snapshot_id:$id}) RETURN n.kind AS kind,count(*) AS n ORDER BY kind', id=snapshot).data()
                links = session.run('MATCH (n:PDF2DataNode {snapshot_id:$id})-[r:PDF2DATA_LINK]->() '
                                    'RETURN r.predicate AS predicate,count(*) AS n ORDER BY predicate', id=snapshot).data()
                refs = session.run('MATCH (d:PDF2DataNode {snapshot_id:$id,kind:"Document"})'
                    '-[:PDF2DATA_LINK {predicate:"CONTAINS"}]->(p:PDF2DataNode)'
                    '-[:PDF2DATA_LINK {predicate:"MENTIONS"}]->(r:PDF2DataNode) '
                    'WITH r,collect(DISTINCT d.title) AS titles WHERE size(titles)>1 '
                    'RETURN r.title AS reference,titles ORDER BY reference LIMIT 50', id=snapshot).data()
                catalogue_refs = session.run('MATCH (d:PDF2DataNode {snapshot_id:$id,kind:"Document"})'
                    '-[:PDF2DATA_LINK {predicate:"CATALOGUE_MENTIONS"}]->(r:PDF2DataNode) '
                    'WITH r,collect(DISTINCT d.title) AS titles WHERE size(titles)>1 '
                    'RETURN r.title AS reference,titles ORDER BY reference LIMIT 50', id=snapshot).data()
                return dict(snapshot_id=snapshot, by_kind=kinds, by_relationship=links,
                            shared_reference_candidates=refs, catalogue_reference_candidates=catalogue_refs,
                            reference_query_limit=50, publication_eligible=False)
        except ValueError:
            raise
        except Exception:
            raise RuntimeError('Neo4j inspection failed') from None


def configure(parser):
    sub = parser.add_subparsers(dest='graph_operation', required=True)
    build_parser = sub.add_parser('build', help='project verified L1 evidence and optional catalogue metadata into a new graph bundle')
    build_parser.add_argument('--packages', type=Path)
    build_parser.add_argument('--catalog', type=Path)
    build_parser.add_argument('--assertions', type=Path)
    build_parser.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify', help='check bundle identity and referential integrity')
    check.add_argument('--bundle', type=Path, required=True)
    importer = sub.add_parser('import', help='explicitly import a bundle into Neo4j; no source or extraction writes')
    importer.add_argument('--bundle', type=Path, required=True)
    importer.add_argument('--batch-size', type=int, default=500)
    inspector = sub.add_parser('inspect', help='run predefined read-only Neo4j coverage/reference queries')
    inspector.add_argument('--snapshot', required=True)
    for child in (importer, inspector):
        child.add_argument('--uri', default='bolt://127.0.0.1:7687')
        child.add_argument('--user', default='neo4j')
        child.add_argument('--password-env', default='NEO4J_PASSWORD')
        child.add_argument('--database', default='neo4j')
        child.add_argument('--allow-remote', action='store_true')
    for child in (build_parser, check, importer, inspector):
        child.add_argument('--json', action='store_true')


def execute(args):
    if args.graph_operation == 'build':
        return build(args.output, args.packages, args.catalog, args.assertions)
    if args.graph_operation == 'verify':
        return summary(validate(load(args.bundle / 'graph.json')))
    kwargs = dict(uri=args.uri, user=args.user, password_env=args.password_env,
                  database=args.database, allow_remote=args.allow_remote)
    if args.graph_operation == 'import':
        return import_graph(args.bundle, batch_size=args.batch_size, **kwargs)
    return inspect_graph(args.snapshot, **kwargs)
