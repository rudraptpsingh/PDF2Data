"""Optional, read-only DuckDB/NetworkX analysis of a verified readable export."""
from pathlib import Path
from hashlib import sha256
import argparse
import json


def analyze(package, output):
    import duckdb
    import networkx as nx
    package = Path(package).resolve(); output = Path(output)
    if output.exists(): raise ValueError('Use a new analysis output directory')
    manifest = json.loads((package / 'manifest.json').read_text())
    def load(name):
        path = (package / name).resolve()
        if not path.is_relative_to(package) or manifest['files'].get(name) != sha256(path.read_bytes()).hexdigest():
            raise ValueError('Export artifact identity changed: ' + name)
        return json.loads(path.read_text())
    data, relations = load('data.json'), load('relations.json')
    if data['source_pdf_sha256'] != relations['source_pdf_sha256'] or data['source_pdf_sha256'] != manifest['source_pdf_sha256']:
        raise ValueError('Export source identity mismatch')
    graph = nx.MultiDiGraph()
    for node in relations['nodes']: graph.add_node(node['id'], **node)
    dangling = []
    for number, edge in enumerate(relations['edges']):
        if edge['from'] not in graph or edge['to'] not in graph:
            dangling.append(edge); continue
        graph.add_edge(edge['from'], edge['to'], key=str(number), **edge)
    hierarchy = nx.DiGraph((a, b) for a, b, e in graph.edges(data=True) if e['type'].startswith('contains_'))
    db = duckdb.connect(':memory:')
    db.execute('CREATE TABLE nodes(id VARCHAR, kind VARCHAR)')
    db.executemany('INSERT INTO nodes VALUES (?, ?)', [(n['id'], n['kind']) for n in relations['nodes']])
    counts = db.execute('SELECT kind, COUNT(*) AS count FROM nodes GROUP BY kind ORDER BY kind').fetchall()
    db.close()
    elements = [{'data': {**node, 'label': node.get('label', node['kind'])}} for node in relations['nodes']]
    elements.extend({'data': {**edge, 'id': 'edge-' + str(i), 'source': edge['from'], 'target': edge['to'], 'label': edge['type']}}
                    for i, edge in enumerate(relations['edges']))
    report = dict(schema_version='pdf2data.optional-profile.v1', source_pdf_sha256=data['source_pdf_sha256'],
        libraries=dict(duckdb=duckdb.__version__, networkx=nx.__version__), nodes=graph.number_of_nodes(),
        edges=graph.number_of_edges(), dangling_edges=len(dangling), contains_hierarchy_acyclic=nx.is_directed_acyclic_graph(hierarchy),
        nodes_by_kind=dict(counts), tables=[dict(id=t['id'], page=t['page'], rows=len(t['cells']),
            columns=max((len(row) for row in t['cells']), default=0), status=t.get('status')) for t in data['tables']],
        extraction_modified=False, semantic_relationships_verified=False, publication_eligible=False)
    output.mkdir(parents=True)
    (output / 'profile.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'cytoscape-elements.json').write_text(json.dumps(elements, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 3 if dangling or not report['contains_hierarchy_acyclic'] else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raise SystemExit(analyze(args.package, args.output))
