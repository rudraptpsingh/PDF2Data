"""Semantic selection must not rewrite evidence or convert coverage into approval."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from local_vector_batch import semantic_fields as sf


def scope():
    return dict(schema_version=sf.SCOPE, profile=dict(schema_version=sf.PROFILE, id='example', version='1',
        field_ids=['cod'], roles=['condition', 'document_metadata'], unresolved_dependencies=['principal-rule']),
        atoms=[dict(id='a', field_id='cod', source_parts=[dict(page=1, exact_quote='COD within two months'),
            dict(page=2, exact_quote='after effectiveness of GNA')], selected_value_parts=[],
            source_quality_observations=[dict(page=2, outcome='repair_required')])],
        selected_source_parts=2, semantic_ownership_approved=False)


def result(s=None):
    s = s or scope(); request = sf.request_document(s)
    body = dict(schema_version=sf.RESPONSE, scope_sha256=sf.digest(s),
                selections=[dict(atom_id='a', role='condition')], unresolved_atom_ids=[])
    return dict(schema_version='askmill.result.v2', status='completed', complete=True,
                domain_acceptance='unverified', model='example', revision='pinned', receipt_id='receipt', replayed=False,
                response_validation=dict(valid=True, schema_sha256=request['response_schema']['sha256']),
                text=json.dumps(body))


def test_source_parts_hold_and_missing_dependencies_survive_projection():
    s = scope(); before = deepcopy(s); request = sf.request_document(s)
    projected = sf.project(s, request, result(s))
    assert projected['candidates'][0]['atom'] == s['atoms'][0]
    assert projected['candidates'][0]['atom']['source_quality_observations'][0]['outcome'] == 'repair_required'
    assert projected['source_parts_accounted'] == 2
    assert projected['unresolved_dependencies'] == ['principal-rule']
    assert not projected['semantic_ownership_approved'] and not projected['calculation_allowed']
    assert not projected['runner_receipt_integrity_verified']
    assert s == before


@pytest.mark.parametrize('patch', [
    {'complete': False, 'status': 'incomplete'},
    {'status': 'schema_invalid'},
    {'schema_version': 'askmill.result.v1'},
    {'domain_acceptance': 'approved'},
    {'response_validation': {'valid': False}},
    {'response_validation': {'valid': True, 'schema_sha256': 'wrong'}},
])
def test_completion_schema_and_acceptance_failures_are_separate(patch):
    s = scope(); r = result(s); r.update(patch)
    with pytest.raises(ValueError): sf.project(s, sf.request_document(s), r)


@pytest.mark.parametrize('change', ['omission', 'duplicate', 'overlap', 'unknown', 'role', 'quote', 'scope', 'fence', 'duplicate_key'])
def test_generated_literals_bad_ids_and_partial_accounting_are_refused(change):
    s = scope(); r = result(s); v = json.loads(r['text'])
    if change == 'omission': v['selections'] = []
    if change == 'duplicate': v['selections'] *= 2
    if change == 'overlap': v['unresolved_atom_ids'] = ['a']
    if change == 'unknown': v['selections'][0]['atom_id'] = 'invented'
    if change == 'role': v['selections'][0]['role'] = 'legally_eligible'
    if change == 'quote': v['selections'][0]['literal'] = 'invented formula or date'
    if change == 'scope': v['scope_sha256'] = 'wrong'
    r['text'] = json.dumps(v)
    if change == 'fence': r['text'] = '```json\n' + r['text'] + '\n```'
    if change == 'duplicate_key': r['text'] = r['text'].replace('"selections":', '"selections": [], "selections":', 1)
    with pytest.raises(ValueError): sf.project(s, sf.request_document(s), r)


def test_unresolved_atoms_are_retained_and_not_treated_as_absent():
    s = scope(); r = result(s); v = json.loads(r['text']); v.update(selections=[], unresolved_atom_ids=['a'])
    r['text'] = json.dumps(v); out = sf.project(s, sf.request_document(s), r)
    assert out['candidates'] == [] and out['unresolved_atoms'] == s['atoms']
    assert not out['section_completeness_established']


@pytest.mark.parametrize('value', [[], None, {'response_validation': None}, {'response_validation': []}])
def test_invalid_result_shapes_fail_without_attribute_errors(value):
    with pytest.raises(ValueError): sf.project(scope(), sf.request_document(scope()), value)


def test_prompt_and_response_schema_pin_the_exact_ids_roles_and_scope():
    s = scope(); request = sf.request_document(s)
    assert request['schema_version'] == 'askmill.request.v2'
    schema = request['response_schema']['document']
    assert schema['properties']['scope_sha256']['const'] == sf.digest(s)
    assert schema['properties']['selections']['items']['properties']['atom_id']['enum'] == ['a']
    assert schema['properties']['selections']['items']['additionalProperties'] is False
    assert 'repair_required' not in request['prompt']  # ledger stays local, not extra model input
    assert 'after effectiveness of GNA' in request['prompt']


@pytest.mark.parametrize('field,value', [('roles', []), ('roles', ['x', 'x']), ('field_ids', []),
    ('role_descriptions', {'unknown': 'bad'}), ('unresolved_dependencies', 'not-array'), ('extra', True)])
def test_configurable_profile_fails_closed(field, value):
    profile = scope()['profile']; profile[field] = value
    with pytest.raises(ValueError): sf.profile_document(profile)


def test_output_cannot_mutate_any_source_or_job_directory(tmp_path):
    original = tmp_path / 'original'; original.mkdir()
    with pytest.raises(ValueError): sf.isolated_output(original / 'new', original)
    with pytest.raises(ValueError): sf.isolated_output(tmp_path, original)
    assert sf.isolated_output(tmp_path / 'separate', original) == tmp_path / 'separate'


def test_reopen_detects_job_tamper_before_model_execution(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'; bundle.mkdir(); package = tmp_path / 'package'; package.mkdir()
    fields = dict(schema_version='pdf2data.configured-fields.v3', field_catalog=[dict(id='cod')],
        source_pdf_sha256='a'*64, source_package_sha256='b'*64,
        observations=[dict(id='a', field_id='cod', match_source_spans=[dict(page=1, exact_quote='literal')],
                           source_spans=[dict(page=1, exact_quote='literal')])])
    sf.write(bundle / 'fields.json', fields); sf.write(bundle / 'manifest.json', {'source_package': str(package)})
    profile = tmp_path / 'profile.json'; sf.write(profile, scope()['profile'])
    calls = []; monkeypatch.setattr(sf, 'verify_fields', lambda p: calls.append(p))
    job = tmp_path / 'job'; sf.prepare(bundle, profile, job)
    assert sf.reopen(job)[0]['atoms'][0]['source_parts'][0]['exact_quote'] == 'literal'
    assert len(calls) == 2
    (job / 'request.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'): sf.reopen(job)


def test_cli_exposes_bounded_commands():
    from local_vector_batch.cli import parser
    args = parser().parse_args(['semantic-fields', 'run', '--job', 'job', '--askmill', 'askmill',
                               '--profile', 'general', '--store', 'receipts', '--output', 'run'])
    assert args.semantic_operation == 'run' and args.job == Path('job')


def test_request_is_accepted_by_released_askmill_v2_contract():
    askmill = pytest.importorskip('askmill')
    request = sf.request_document(scope())
    parsed = askmill.Request.model_validate(request)
    assert parsed.response_schema.sha256 == request['response_schema']['sha256']
    assert parsed.model_dump() == request


def test_runner_receipt_is_bound_to_exact_request_result_and_settlement(tmp_path):
    from hashlib import sha256
    def runner_digest(value):
        return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    def envelope(path, content):
        sf.write(path, {'content': content, 'sha256': runner_digest(content)})
    request = sf.request_document(scope()); r = result()
    r['session_id'] = '00000000-0000-0000-0000-000000000000'
    key = dict(request=request, profile={'model': 'fixture'}, runtime={'version': 'fixture'})
    r['receipt_id'] = runner_digest(key)
    content = dict(schema_version='askmill.receipt.v2', key=r['receipt_id'], **key,
                   result=r, raw_response={'message': {'content': r['text']}})
    receipt = tmp_path / (r['receipt_id'] + '.json'); envelope(receipt, content)
    settlement = tmp_path / 'settlements' / (r['session_id'] + '.json'); settlement.parent.mkdir()
    closed = dict(session_id=r['session_id'], worker_reaped=True, quarantined=False,
                  external_request_settlement='acknowledged'); envelope(settlement, closed)
    assert sf.runner_receipt(r, request, tmp_path)['receipt_sha256'] == sf.file_sha256(receipt)
    assert sf.runner_receipt({**r, 'replayed': True}, request, tmp_path)['receipt_sha256'] == sf.file_sha256(receipt)
    changed = deepcopy(request); changed['prompt'] += 'changed'
    with pytest.raises(ValueError, match='identity'): sf.runner_receipt(r, changed, tmp_path)
    envelope(settlement, {**closed, 'quarantined': True})
    with pytest.raises(ValueError, match='settlement'): sf.runner_receipt(r, request, tmp_path)


def test_runner_receipt_ids_cannot_escape_the_store(tmp_path):
    r = result(); r.update(receipt_id='../outside', session_id='bad')
    with pytest.raises(ValueError, match='IDs'): sf.runner_receipt(r, sf.request_document(scope()), tmp_path)
