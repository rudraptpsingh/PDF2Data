import json
from pathlib import Path

import pytest

from local_vector_batch import evidence_graph as graph
from local_vector_batch.cli import parser
from local_vector_batch.legal_l1 import extract_document
from test_legal_l1 import source_fixture


def packages(tmp_path):
    root = tmp_path / 'packages'
    extract_document(source_fixture(tmp_path), root / 'example')
    return root


def test_native_projection_retains_all_pages_quality_and_literal_references(tmp_path):
    root = packages(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    receipt = graph.build(tmp_path / 'graph', packages=root)
    data = graph.validate(graph.load(tmp_path / 'graph/graph.json'))
    assert receipt['by_kind']['Page'] == 2
    assert receipt['by_kind']['Reference'] == 1
    assert receipt['by_relationship']['MENTIONS'] == 2
    assert receipt['publication_eligible'] is False
    doc = next(n['properties'] for n in data['nodes'] if n['kind'] == 'Document')
    assert doc['source_id'] == 'example' and 'quality' in doc and 'readiness' in doc
    assert all(before[p] == p.read_bytes() for p in before)
    assert not any(n['kind'] == 'Assertion' for n in data['nodes'])
    assert graph.build(tmp_path / 'graph2', packages=root)['snapshot_id'] == receipt['snapshot_id']
    with pytest.raises(ValueError, match='new directory'):
        graph.build(tmp_path / 'graph', packages=root)
    with pytest.raises(ValueError, match='outside immutable'):
        graph.build(root / 'graph', packages=root)


def test_catalogue_metadata_is_not_verified_authorship_or_document_type(tmp_path):
    catalog = tmp_path / 'catalog.jsonl'
    catalog.write_text(json.dumps(dict(document_id='format', title='Format 4',
        organization_id='CEA', document_type='Regulation (Notified)')) + '\n')
    receipt = graph.build(tmp_path / 'graph', catalog=catalog)
    data = graph.load(tmp_path / 'graph/graph.json')
    assert receipt['by_relationship'] == {'CATALOGUED_UNDER': 1, 'CATEGORIZED_AS': 1}
    doc = next(n for n in data['nodes'] if n['kind'] == 'Document')
    assert doc['properties']['metadata_state'] == 'catalogue_only_unverified'
    assert all(e['predicate'] != 'ISSUED_BY' for e in data['edges'])
    catalog.write_text(catalog.read_text() * 2)
    with pytest.raises(ValueError, match='Duplicate'):
        graph.build(tmp_path / 'bad', catalog=catalog)
    assert not (tmp_path / 'bad').exists()


def test_source_anchored_assertions_stay_candidates_and_fail_on_bad_quote(tmp_path):
    root = packages(tmp_path)
    catalog = tmp_path / 'catalog.jsonl'
    catalog.write_text(json.dumps(dict(document_id='target', title='Example instrument')))
    assertions = tmp_path / 'assertions.json'
    value = dict(source_id='example', target_source_id='target', predicate='references',
        page=1, exact_quote='Petition No. 1/MP/2026', review_state='verified')
    assertions.write_text(json.dumps([value]))
    graph.build(tmp_path / 'graph', packages=root, catalog=catalog, assertions=assertions)
    data = graph.load(tmp_path / 'graph/graph.json')
    candidate = next(n['properties'] for n in data['nodes'] if n['kind'] == 'Assertion')
    assert candidate['review_state'] == 'candidate'
    assert candidate['evidence_check'] == 'literal_quote_only_semantic_review_required'
    assert len([e for e in data['edges'] if e['predicate'] == 'SUPPORTED_BY']) == 1
    for changes in ({'exact_quote': 'not present'}, {'page': True},
                    {'source_span': [-1, 5]}, {'target_source_id': 'missing'},
                    {'predicate': 'x) DELETE n'}):
        assertions.write_text(json.dumps([{**value, **changes}]))
        with pytest.raises(ValueError):
            graph.build(tmp_path / 'bad', packages=root, catalog=catalog, assertions=assertions)
        assert not (tmp_path / 'bad').exists()


def test_corrupt_graph_and_dangling_edges_fail_before_database_access(tmp_path):
    catalog = tmp_path / 'c.jsonl'
    catalog.write_text('{"document_id":"a","title":"A"}')
    graph.build(tmp_path / 'g', catalog=catalog)
    data = graph.load(tmp_path / 'g/graph.json')
    data['nodes'][0]['properties']['title'] = 'tampered'
    with pytest.raises(ValueError, match='hash mismatch'):
        graph.validate(data)
    data['edges'].append(dict(id='edge', source='missing', target='also-missing', predicate='CONTAINS'))
    data['snapshot_id'] = graph.digest({k: v for k, v in data.items() if k != 'snapshot_id'})
    with pytest.raises(ValueError, match='Invalid graph edge'):
        graph.validate(data)


def test_connection_rejects_remote_and_embedded_credentials_before_driver_access(monkeypatch):
    monkeypatch.delenv('NEO4J_PASSWORD', raising=False)
    for uri in ('https://localhost', 'bolt://user:secret@localhost', 'bolt://external.example'):
        with pytest.raises(ValueError):
            graph.connection(uri, 'neo4j', 'NEO4J_PASSWORD')
    with pytest.raises(ValueError, match='environment'):
        graph.connection('bolt://127.0.0.1:17687', 'neo4j', 'NEO4J_PASSWORD')
    args = parser().parse_args(['graph', 'import', '--bundle', '/tmp/example', '--json'])
    assert args.password_env == 'NEO4J_PASSWORD' and not args.allow_remote


def test_mcp_graph_only_uses_owned_run_and_requires_server_database_configuration(tmp_path, monkeypatch):
    from local_vector_batch.mcp_server import Bridge
    source = source_fixture(tmp_path)
    bridge = Bridge(tmp_path / 'workspace', [tmp_path])
    try:
        run = bridge.parse(source['path'], ocr='off')
        bridge.workspace.executor.shutdown(wait=True)
        receipt = bridge.graph(run['run_id'])
        assert receipt['by_kind']['Page'] == 2
        assert bridge.graph(run['run_id'])['snapshot_id'] == receipt['snapshot_id']
        monkeypatch.delenv('PDF2DATA_NEO4J_URI', raising=False)
        with pytest.raises(ValueError, match='Configure'):
            bridge.graph(run['run_id'], 'import')
        with pytest.raises(ValueError):
            bridge.graph('../outside')
    finally:
        bridge.close()


def test_live_neo4j_round_trip_and_resume(tmp_path, monkeypatch):
    """Opt-in disposable-server test; no unrelated data is removed."""
    import os
    uri = os.environ.get('PDF2DATA_TEST_NEO4J_URI')
    if not uri or not os.environ.get('PDF2DATA_TEST_NEO4J_PASSWORD'):
        pytest.skip('Configure disposable local Neo4j to exercise the live adapter')
    catalog = tmp_path / 'catalog.jsonl'
    catalog.write_text('\n'.join(json.dumps(dict(document_id=str(tmp_path) + suffix,
        title='Live round-trip fixture ' + suffix)) for suffix in ('a', 'b')))
    receipt = graph.build(tmp_path / 'g', catalog=catalog)
    kwargs = dict(uri=uri, password_env='PDF2DATA_TEST_NEO4J_PASSWORD')
    original = graph.connection

    # Fail once after the snapshot marker exists. It must remain unpublished.
    class FailingSession:
        def __init__(self, session):
            self.session = session
            self.calls = 0
        def __getattr__(self, name):
            return getattr(self.session, name)
        def __enter__(self):
            self.session.__enter__()
            return self
        def __exit__(self, *args):
            return self.session.__exit__(*args)
        def execute_write(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError('Injected import interruption after one committed batch')
            return self.session.execute_write(*args, **kwargs)
    class FailingDriver:
        def __init__(self, driver):
            self.driver = driver
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.driver.close()
        def session(self, **kwargs):
            return FailingSession(self.driver.session(**kwargs))
    monkeypatch.setattr(graph, 'connection', lambda *a, **k: FailingDriver(original(*a, **k)))
    with pytest.raises(RuntimeError, match='resumed'):
        graph.import_graph(tmp_path / 'g', batch_size=1, **kwargs)
    monkeypatch.setattr(graph, 'connection', original)
    with pytest.raises(ValueError, match='incomplete'):
        graph.inspect_graph(receipt['snapshot_id'], **kwargs)
    assert graph.import_graph(tmp_path / 'g', batch_size=1, **kwargs)['status'] == 'imported'
    first = graph.inspect_graph(receipt['snapshot_id'], **kwargs)
    assert graph.import_graph(tmp_path / 'g', **kwargs)['status'] == 'already_imported'
    assert graph.inspect_graph(receipt['snapshot_id'], **kwargs) == first
    assert first['by_kind'] == [dict(kind='Document', n=2)]
