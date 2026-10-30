import json
import sqlite3
import pytest
from concurrent.futures import Future
from local_vector_batch import auto_proof_queue as q


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


class ImmediatePool:
    def __init__(self, max_workers): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future


def test_provisional_candidate_never_claims_verification():
    page = {'text': 'वर्ष 2024 में 51%', 'text_sha256': 'b' * 64}
    receipt = {'independent': {'text': '2024 51%', 'recognition_languages': ['en']}}
    chosen = q.provisional_disposition(page, receipt)
    assert chosen['candidate'] == 'selected' and chosen['reason'] == 'selected_supports_devanagari'
    assert not chosen['verified'] and not chosen['searchable']
    receipt['independent']['text'] = '2024 15%'
    assert q.provisional_disposition(page, receipt)['reason'] == 'numeric_disagreement'
    receipt['independent']['text'] = '2024 51'
    assert q.provisional_disposition(page, receipt)['reason'] == 'operator_disagreement'


def test_word_alignment_ignores_line_wrapping_but_targets_changed_source_line():
    lines = [{'text': 'Heading'}, {'text': 'The tariff is 20 percent'}, {'text': 'and payable annually'}]
    assert q.changed_source_lines(lines, 'Heading\nThe tariff is\n20 percent and payable annually', set()) == []
    assert q.changed_source_lines(lines, 'Heading The tariff is 25 percent and payable annually', set()) == [1]
    with pytest.raises(ValueError, match='overlaps'):
        q.changed_source_lines(lines, 'Heading The tariff is 25 percent and payable annually', {1})
    with pytest.raises(ValueError, match='overlaps'):
        q.changed_source_lines(lines, 'Heading NEW The tariff is 20 percent and payable annually', {1})


def test_boundary_insertion_proposes_both_crops_without_choosing_a_line():
    lines = [{'text': 'Heading'}, {'text': 'The tariff is 20 percent'},
             {'text': 'and payable annually'}]
    assert q.changed_source_lines(lines, 'Heading The tariff is 20 percent 3 and payable annually', set()) == [1, 2]
    with pytest.raises(ValueError, match='budget'):
        q.changed_source_lines(lines, 'Heading The tariff is 20 percent many omitted words and payable annually', set())


def test_queue_deduplicates_and_resumes_current_hash(tmp_path, monkeypatch):
    package = tmp_path / 'package'; package.mkdir()
    (package / 'report.json').write_text(json.dumps({'source_pdf_sha256': 'a' * 64}))
    write_rows(package / 'pages.jsonl', [{'page': 1, 'status': 'review_required', 'text_sha256': 'b' * 64}])
    manifest = tmp_path / 'manifest.jsonl'
    row = {'package': str(package), 'source_pdf': str(tmp_path / 'source.pdf'), 'pages': [1]}
    write_rows(manifest, [row, row])
    independent = tmp_path / 'independent.jsonl'
    write_rows(independent, [{'source_pdf_sha256': 'a' * 64, 'page': 1, 'selected_page_text_sha256': 'b' * 64}])
    monkeypatch.setattr(q, 'ProcessPoolExecutor', ImmediatePool)
    monkeypatch.setattr(q, '_attempt', lambda job, *_: {'key': job['key'], 'page': job['page'], 'status': 'awaiting_evidence'})
    out = tmp_path / 'out'
    result = q.run_auto_proof(manifest, independent, out)
    assert result['counts'] == {'awaiting_evidence': 1}
    assert q.run_auto_proof(manifest, independent, out)['planned'] == 0
    assert q.run_auto_proof(manifest, independent, out, retry_unresolved=True)['counts'] == {'awaiting_evidence': 1}
    with sqlite3.connect(out / 'auto-proof.sqlite') as db:
        db.execute("update jobs set status='committed_local'")
    assert q.run_auto_proof(manifest, independent, out, retry_unresolved=True)['planned'] == 0
    assert json.loads((out / 'progress.json').read_text())['status'] == 'complete'
    write_rows(package / 'pages.jsonl', [{'page': 1, 'status': 'review_required', 'text_sha256': 'c' * 64}])
    assert q.run_auto_proof(manifest, independent, out)['planned'] == 0
