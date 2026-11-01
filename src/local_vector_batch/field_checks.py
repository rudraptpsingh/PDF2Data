"""Declarative assertions over verified field bundles; no executable rules."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import re

from . import field_rules as fields

SCHEMA = 'pdf2data.field-check-rules.v1'
ASSERTIONS = {'count', 'expected_literals', 'expected_pages',
              'expected_captures', 'required_captures', 'forbidden_fragments', 'expected_typed_candidates'}


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise ValueError('Invalid ' + label + ' keys')


def _strings(value, label):
    if not isinstance(value, list) or len(value) > 1000 or any(
            not isinstance(x, str) or not x or len(x) > 100000 for x in value):
        raise ValueError('Invalid ' + label)


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Check rules exceed 2 MB')
    return raw


def load(path):
    raw = _read(path)
    config = fields._decode(raw)
    _keys(config, {'schema_version', 'id', 'version', 'target', 'checks'},
          {'schema_version', 'id', 'version', 'target', 'checks'}, 'check rules')
    if config['schema_version'] != SCHEMA or any(
            not isinstance(config[k], str) or not config[k] or len(config[k]) > 200 for k in ('id', 'version')):
        raise ValueError('Unknown check schema or invalid identity')
    target = config['target']
    _keys(target, {'rules_sha256', 'source_pdf_sha256', 'source_package_sha256'},
          {'rules_sha256'}, 'target')
    if any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in target.values()):
        raise ValueError('Check target needs exact SHA256 pins')
    checks = config['checks']
    if not isinstance(checks, list) or not 1 <= len(checks) <= 200:
        raise ValueError('Configure 1..200 checks')
    ids = set()
    for rule in checks:
        _keys(rule, {'id', 'field_id', *ASSERTIONS}, {'id', 'field_id', 'count'}, 'check')
        for key in ('id', 'field_id'):
            if not isinstance(rule[key], str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,79}', rule[key]):
                raise ValueError('Invalid check or field ID')
        if rule['id'] in ids:
            raise ValueError('Duplicate check ID')
        ids.add(rule['id'])
        count = rule['count']
        _keys(count, {'min', 'max'}, {'min', 'max'}, 'count')
        if any(type(v) is not int for v in count.values()) or not 0 <= count['min'] <= count['max'] <= 10000:
            raise ValueError('Count needs integer min/max in 0..10000')
        # Explicit negatives permit zero; content assertions must not pass vacuously.
        if count['min'] == 0 and set(rule) & (ASSERTIONS - {'count'}):
            raise ValueError('Content assertions require count.min >= 1; use count-only for negatives')
        for name in ('expected_literals', 'required_captures', 'forbidden_fragments'):
            if name in rule:
                _strings(rule[name], name)
                if not rule[name]:
                    raise ValueError(name + ' cannot be empty')
        if 'required_captures' in rule and len(rule['required_captures']) != len(set(rule['required_captures'])):
            raise ValueError('Required captures must be unique')
        if 'expected_pages' in rule:
            pages = rule['expected_pages']
            if not isinstance(pages, list) or not 1 <= len(pages) <= 1000 or any(
                    not isinstance(part, list) or not 1 <= len(part) <= 10000 or
                    any(type(p) is not int or p < 1 for p in part) for part in pages):
                raise ValueError('expected_pages needs an ordered page list per observation')
        if 'expected_captures' in rule:
            captures = rule['expected_captures']
            if not isinstance(captures, list) or not 1 <= len(captures) <= 1000:
                raise ValueError('Invalid expected_captures')
            for row in captures:
                if not isinstance(row, dict) or not 1 <= len(row) <= 32 or any(
                        not isinstance(k, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', k) or
                        (v is not None and (not isinstance(v, str) or len(v) > 100000)) for k, v in row.items()):
                    raise ValueError('Invalid expected capture row')
        if 'expected_typed_candidates' in rule:
            candidates=rule['expected_typed_candidates']
            if not isinstance(candidates,list) or not 1 <= len(candidates) <= 1000:
                raise ValueError('Invalid expected_typed_candidates')
            for value in candidates:
                if value is None:continue
                _keys(value,{'numerator','denominator'},{'numerator','denominator'},'exact rational candidate')
                if not isinstance(value['numerator'],str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,199})',value['numerator']) or not isinstance(value['denominator'],str) or not re.fullmatch(r'[1-9][0-9]{0,199}',value['denominator']):
                    raise ValueError('Expected candidate needs bounded exact rational strings')
    return config


def evaluate(data, config):
    """Pure assertion engine. Public command verifies source before calling this."""
    if any(data.get(key) != value for key, value in config['target'].items()):
        raise ValueError('Check target differs from source/rules pins')
    catalog = {f['id']: f for f in data['field_catalog']}
    results = []
    for rule in config['checks']:
        name = rule['field_id']
        if name not in catalog:
            raise ValueError('Unknown check field: ' + name)
        configured_captures = set(catalog[name].get('capture_groups', []))
        referenced = set(rule.get('required_captures', [])) | {
            k for row in rule.get('expected_captures', []) for k in row}
        if referenced - configured_captures:
            raise ValueError('Unknown check capture: ' + ', '.join(sorted(referenced - configured_captures)))
        rows = [r for r in data['observations'] if r['field_id'] == name]
        if 'expected_typed_candidates' in rule and catalog[name]['type']!='quantity':
            raise ValueError('Typed rational checks require a quantity field')
        if len(rows) > 10000:
            raise ValueError('More than 10000 observations for one check; narrow extraction rules')
        assertions = []

        def record(kind, expected, actual, passed):
            assertions.append(dict(assertion=kind, expected=expected, actual=actual, passed=bool(passed)))

        bounds = rule['count']
        record('count', bounds, len(rows), bounds['min'] <= len(rows) <= bounds['max'])
        if 'expected_literals' in rule:
            actual = [r['value_literal'] for r in rows]
            record('expected_literals', rule['expected_literals'], actual,
                   Counter(actual) == Counter(rule['expected_literals']))
        if 'expected_pages' in rule:
            actual = [[s['page'] for s in r.get('source_spans', [r.get('source_span')]) if s] for r in rows]
            record('expected_pages', rule['expected_pages'], actual, actual == rule['expected_pages'])
        if 'expected_typed_candidates' in rule:
            actual=[r.get('typed_candidate') for r in rows]
            record('expected_typed_candidates',rule['expected_typed_candidates'],actual,actual==rule['expected_typed_candidates'])
        if 'expected_captures' in rule:
            expected = rule['expected_captures']
            actual = [{k: rows[i].get('captures', {}).get(k, {}).get('value_literal')
                       for k in wanted} for i, wanted in enumerate(expected) if i < len(rows)]
            matched = len(rows) == len(expected) and actual == expected and all(
                k in rows[i].get('captures', {}) for i, wanted in enumerate(expected) for k in wanted)
            record('expected_captures', expected, actual, matched)
        if 'required_captures' in rule:
            missing = [dict(observation=r['id'], capture=k) for r in rows for k in rule['required_captures']
                       if r.get('captures', {}).get(k, {}).get('status') != 'matched' or
                       not r.get('captures', {}).get(k, {}).get('value_literal')]
            record('required_captures', rule['required_captures'], missing, bool(rows) and not missing)
        if 'forbidden_fragments' in rule:
            hits = [dict(observation=r['id'], fragment=fragment) for r in rows
                    for fragment in rule['forbidden_fragments'] if fragment in r['value_literal']]
            record('forbidden_fragments', rule['forbidden_fragments'], hits, bool(rows) and not hits)
        results.append(dict(id=rule['id'], field_id=name, passed=all(a['passed'] for a in assertions), assertions=assertions))
    covered = {r['field_id'] for r in config['checks']}
    return dict(schema_version='pdf2data.field-check-report.v1', passed=all(r['passed'] for r in results),
                checks_passed=sum(r['passed'] for r in results), checks_total=len(results), results=results,
                unchecked_fields=sorted(set(catalog) - covered), target=config['target'],
                evaluation_scope='configured_assertions_only', semantic_formula_accuracy_measured=False,
                source_quality_approval_granted=False, semantic_ownership_approved=False,
                calculation_allowed=False, publication_eligible=False)


def check(bundle, checks, output, timeout=30):
    bundle, checks, output = (Path(p).resolve() for p in (bundle, checks, output))
    raw = _read(checks)
    config = load(checks)
    if checks.read_bytes() != raw:
        raise ValueError('Check rules changed while loading')
    if output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output):
        raise ValueError('Use a new check output directory outside the field bundle')
    # Configuration cannot disable or replace native source verification.
    manifest_raw = (bundle/'manifest.json').read_bytes()
    manifest = fields._decode(manifest_raw)
    package = Path(manifest['source_package']).resolve()
    if output.is_relative_to(package) or package.is_relative_to(output):
        raise ValueError('Check output must be outside the immutable source package')
    data_raw = (bundle/'fields.json').read_bytes()
    fields.verify(bundle, timeout)
    if sha256(data_raw).hexdigest() != manifest['files']['fields.json']:
        raise ValueError('Field bundle changed after verification')
    result = evaluate(fields._decode(data_raw), config)
    if checks.read_bytes() != raw or (bundle/'manifest.json').read_bytes() != manifest_raw or any(
            fields.file_sha256(bundle/n) != h for n, h in manifest['files'].items()) or any(
            fields.file_sha256(package/n) != h for n, h in manifest['source_files'].items()):
        raise ValueError('Check rules or source changed during evaluation')
    result.update(checks_id=config['id'], checks_version=config['version'], checks_sha256=sha256(raw).hexdigest(),
                  field_manifest_sha256=sha256(manifest_raw).hexdigest(),
                  source_pdf_sha256=fields._decode(data_raw)['source_pdf_sha256'],
                  evaluator_sha256=fields.file_sha256(Path(__file__)))
    import json
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 16 * 1024 * 1024:
        raise ValueError('Check report exceeds 16 MB; narrow the checks')
    output.mkdir(parents=True)
    (output/'checks.json').write_bytes(raw)
    fields.write(output/'report.json', result)
    return result
