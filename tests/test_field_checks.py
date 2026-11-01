from copy import deepcopy
from pathlib import Path
import json
import pytest

from local_vector_batch import field_rules as fields
from local_vector_batch import field_checks as checks
from test_field_captures import data

CASES = json.loads((Path(__file__).parent/'fixtures/field-check-cases.json').read_text(encoding='utf-8'))['cases']


def config(*rules):
    return dict(schema_version=checks.SCHEMA, id='source-checks', version='1',
                target=dict(rules_sha256='c'*64), checks=list(rules))


def put(tmp_path, value):
    path = tmp_path/'checks.json'
    fields.write(path, value)
    return path


@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
def test_json_cases_drive_extraction_and_assertions(tmp_path, case):
    rules = dict(schema_version=fields.SCHEMA, fields_schema_version=fields.FIELDS_V2,
                 id='test', version='1', fields=[case['field']])
    observed = fields.compute(data(case['text']), rules, 'c'*64, 'generic')
    conf = checks.load(put(tmp_path, config(case['check'])))
    before = deepcopy(observed)
    report = checks.evaluate(observed, conf)
    assert report['passed'] is case['passed']
    assert report['unchecked_fields'] == []
    assert observed == before
    assert report['publication_eligible'] is False
    assert report['calculation_allowed'] is False


@pytest.mark.parametrize('change', [
    {'count': {'min': True, 'max': 1}}, {'count': {'min': 2, 'max': 1}},
    {'count': {'min': 0, 'max': 1}, 'forbidden_fragments': ['raw']},
    {'count': {'min': 0, 'max': 0}, 'expected_literals': []},
    {'expected_pages': [[True]]}, {'expected_captures': [{'bad-name': 'x'}]},
    {'expected_captures': [{'a': 1}]}, {'required_captures': ['a', 'a']},
    {'count': {'min': 0, 'max': 10001}}, {'expression': 'import os'},
    {'publication_eligible': True}, {'expected_literals': '30 days'},
])
def test_invalid_check_rules_are_rejected(tmp_path, change):
    rule = dict(id='one', field_id='x', count=dict(min=1, max=1))
    rule.update(change)
    with pytest.raises(ValueError):
        checks.load(put(tmp_path, config(rule)))


def test_duplicate_keys_ids_unknown_fields_and_stale_pins_fail(tmp_path):
    rule = CASES[0]['check']
    path = put(tmp_path, config(rule, rule))
    with pytest.raises(ValueError, match='Duplicate check'):
        checks.load(path)
    path.write_text('{"schema_version":"a","schema_version":"b"}')
    with pytest.raises(ValueError, match='Duplicate'):
        checks.load(path)
    observed = dict(rules_sha256='c'*64, field_catalog=[], observations=[])
    with pytest.raises(ValueError, match='Unknown check field'):
        checks.evaluate(observed, config(rule))
    observed['rules_sha256'] = 'd'*64
    with pytest.raises(ValueError, match='target differs'):
        checks.evaluate(observed, config(rule))
    observed = dict(rules_sha256='c'*64, field_catalog=[dict(id=rule['field_id'])], observations=[])
    with pytest.raises(ValueError, match='Unknown check capture'):
        checks.evaluate(observed, config(rule))


def test_multipart_page_order_and_unchecked_fields_are_explicit():
    rule = dict(id='one', field_id='x', count=dict(min=1, max=1), expected_pages=[[1, 2]])
    observed = dict(rules_sha256='c'*64, field_catalog=[dict(id='x'), dict(id='other')],
                    observations=[dict(field_id='x', source_spans=[dict(page=1), dict(page=2)])])
    report = checks.evaluate(observed, config(rule))
    assert report['passed'] and report['unchecked_fields'] == ['other']
    observed['observations'][0]['source_spans'].reverse()
    assert checks.evaluate(observed, config(rule))['passed'] is False


@pytest.fixture
def bundle(tmp_path):
    from test_legal_l1 import source_fixture
    from local_vector_batch.legal_l1 import extract_document
    package = tmp_path/'source'
    extract_document(source_fixture(tmp_path), package)
    rules = dict(schema_version=fields.SCHEMA, fields_schema_version=fields.FIELDS_V2,
                 id='reply', version='1', fields=[dict(id='reply', type='text',
                 pattern=r'(?P<value>within (?P<duration>two weeks))', capture_groups=['duration'])])
    path = tmp_path/'rules.json'
    fields.write(path, rules)
    out = tmp_path/'fields'
    fields.extract(package, path, out)
    return out


def test_actual_cli_checks_bundle_freezes_rules_and_returns_failure_exit(bundle, tmp_path, capsys):
    from local_vector_batch.cli import main
    extracted = json.loads((bundle/'fields.json').read_text())
    rule = dict(id='reply-occurrences', field_id='reply', count=dict(min=2, max=2),
                expected_literals=['within two weeks']*2, expected_pages=[[1], [2]],
                required_captures=['duration'])
    conf = config(rule)
    conf['target'] = {k: extracted[k] for k in ('rules_sha256', 'source_pdf_sha256', 'source_package_sha256')}
    path = put(tmp_path, conf)
    out = tmp_path/'pass'
    assert main(['fields', 'check', '--bundle', str(bundle), '--checks', str(path),
                 '--output', str(out), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['passed'] and report['checks_passed'] == 1
    assert (out/'checks.json').read_bytes() == path.read_bytes()
    conf['checks'][0]['count'] = dict(min=4, max=4)
    put(tmp_path, conf)
    assert main(['fields', 'check', '--bundle', str(bundle), '--checks', str(path),
                 '--output', str(tmp_path/'fail'), '--json']) == 3
    assert json.loads(capsys.readouterr().out)['passed'] is False
    conf['target']['rules_sha256'] = 'f'*64
    put(tmp_path, conf)
    rejected = tmp_path/'rejected'
    assert main(['fields', 'check', '--bundle', str(bundle), '--checks', str(path),
                 '--output', str(rejected), '--json']) == 2
    assert not rejected.exists()


def test_tampered_bundle_cannot_be_approved_by_empty_negative_check(bundle, tmp_path):
    extracted = json.loads((bundle/'fields.json').read_text())
    extracted['observations'] = []
    fields.write(bundle/'fields.json', extracted)
    manifest = json.loads((bundle/'manifest.json').read_text())
    manifest['files']['fields.json'] = fields.file_sha256(bundle/'fields.json')
    fields.write(bundle/'manifest.json', manifest)
    conf = config(dict(id='negative', field_id='reply', count=dict(min=0, max=0)))
    conf['target']['rules_sha256'] = extracted['rules_sha256']
    with pytest.raises(ValueError, match='re-derivation'):
        checks.check(bundle, put(tmp_path, conf), tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_schema_validates_json_driven_cases_and_rejects_unknown_assertions(tmp_path):
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads((Path(__file__).parents[1]/'schemas/field-check-rules.schema.json').read_text())
    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)
    for case in CASES:
        validator.validate(config(case['check']))
    conf = config(CASES[0]['check'])
    conf['checks'][0]['expression'] = 'anything'
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(conf)
