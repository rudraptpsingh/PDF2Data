"""Opt-in native-parent projections with exact multipart page anchors.

No furniture removal, cross-parent joining or semantic acceptance. A projection
is an indexing view of existing native spans, never replacement source text.
"""
from datetime import datetime
from hashlib import sha256
import re

from .document_blocks import digest, span
from .document_metadata import field_geometry

SCHEMA = 'pdf2data.configured-fields.v3'


def validate_field(field):
    if field.get('scope') != 'native_unit_candidate' or field.get('extractor', 'regex') != 'regex':
        raise ValueError('Fields v3 requires explicit native_unit_candidate scope and regex extractor')
    for name in ('native_unit_ids', 'native_unit_labels'):
        values = field.get(name, [])
        if not isinstance(values, list) or len(values) > 1000 or any(not isinstance(v, str) or not v for v in values) or len(values) != len(set(values)):
            raise ValueError('Use at most 1000 unique nonempty native unit selectors')


class Projection:
    def __init__(self, unit, index, pages):
        self.unit = unit; self.index = index; self.pages = pages; self.text = ''; self.parts = []
        last = None
        for source in unit['spans']:
            page = pages[source['page']]; a = source['char_start']; b = a + source['char_length']
            if not 0 <= a < b <= len(page['text']) or source['page_text_sha256'] != page['text_sha256'] or page['text'][a:b] != source['exact_quote']:
                raise ValueError('Native unit span differs from selected source')
            if last and (source['page'] < last[0] or (source['page'] == last[0] and a < last[1])):
                raise ValueError('Native unit spans are reordered or overlap')
            lo = len(self.text); self.text += source['exact_quote']
            self.parts.append((lo, len(self.text), source)); last = (source['page'], b)
        if len(self.text) > 500000 or len(self.parts) > 1000:
            raise ValueError('Native unit exceeds multipart projection budget; narrow selectors')

    def evidence(self, a, b):
        if not 0 <= a < b <= len(self.text):raise ValueError('Multipart interval outside native unit')
        pieces = []
        for lo, hi, source in self.parts:
            left, right = max(a, lo), min(b, hi)
            if left < right:
                pieces.append(span(self.pages[source['page']], source['char_start'] + left - lo, source['char_start'] + right - lo))
        if ''.join(s['exact_quote'] for s in pieces) != self.text[a:b]:raise ValueError('Multipart mapping lost source characters')
        return pieces

    def seams(self, pieces):
        seams = []
        for before, after in zip(pieces, pieces[1:]):
            end = before['char_start'] + before['char_length']
            if before['page'] == after['page']:
                omitted = [self.pages[before['page']]['text'][end:after['char_start']]]
            else:
                if after['page'] != before['page'] + 1:raise ValueError('Multipart match skips a physical page')
                omitted = [self.pages[before['page']]['text'][end:], self.pages[after['page']]['text'][:after['char_start']]]
            seams.append(dict(from_page=before['page'], from_char_end=end, to_page=after['page'], to_char_start=after['char_start'],
                omitted_native_parent_gap=[dict(characters=len(t), text_sha256=sha256(t.encode()).hexdigest()) for t in omitted],
                join='exact_native_span_concatenation_no_separator', furniture_state='not_removed_or_classified_by_field_extractor',
                semantic_continuation_approved=False))
        return seams


def compute(data, rules, rules_sha, family, implementation):
    from .field_rules import capture_config, flags
    if not re.fullmatch(r'[a-f0-9]{64}', data.get('source_package_sha256', '')):
        raise ValueError('Fields v3 requires exact source package SHA256')
    pages = {p['page']: p for p in data['pages']}; layouts = {p['page']: p for p in data['layout']}
    if len(pages) != len(data['pages']) or any(sha256(p['text'].encode()).hexdigest() != p['text_sha256'] for p in pages.values()):
        raise ValueError('Multipart selected page identity changed')
    units = data['structure'].get('units')
    if not isinstance(units, list) or not units or len(units) > 10000:
        raise ValueError('Fields v3 needs 1..10000 retained native units')
    projections = [Projection(u, i, pages) for i, u in enumerate(units)]
    if sum(len(p.text) for p in projections) > 10000000:raise ValueError('Multipart source exceeds 10 million characters')
    known_ids = {p.unit['id'] for p in projections}; known_labels = {p.unit['label'] for p in projections}
    if len(known_ids) != len(units):raise ValueError('Duplicate native unit ID')
    observations = []; statuses = {}
    for field in rules['fields']:
        validate_field(field); names = capture_config(rules, field)
        if set(field.get('native_unit_ids', [])) - known_ids or set(field.get('native_unit_labels', [])) - known_labels:
            raise ValueError('Configured native unit selector does not resolve in this source')
        applicable = field.get('families', rules.get('families', []))
        if applicable and family not in applicable:
            statuses[field['id']] = dict(status='not_applicable_to_declared_family', matches=0); continue
        pattern = re.compile(field['pattern'], flags(field)); found = 0
        for projection in projections:
            unit = projection.unit
            if field.get('native_unit_ids') and unit['id'] not in field['native_unit_ids']:continue
            if field.get('native_unit_labels') and unit['label'] not in field['native_unit_labels']:continue
            for match in pattern.finditer(projection.text):
                a, b = match.span('value')
                if a < 0 or a == b:continue
                match_parts = projection.evidence(match.start(), match.end())
                if field.get('pages') and any(s['page'] not in field['pages'] for s in match_parts):continue
                parts = projection.evidence(a, b); literal = projection.text[a:b]; issues = []; typed = None
                if field['type'] in ('date', 'quantity'):
                    if len(parts) != 1:issues.append('multipart_scalar_normalization_not_established')
                    elif field['type'] == 'date':
                        try:typed = datetime.strptime(literal, field['date_format']).date().isoformat()
                        except (ValueError, KeyError):issues.append('date_does_not_match_declared_format')
                    else:
                        from .formula_observations import stated_number
                        try:
                            n = stated_number(literal); typed = dict(numerator=str(n.numerator), denominator=str(n.denominator))
                        except (ValueError, ArithmeticError):issues.append('ambiguous_numeric_literal')
                if field['type'] == 'formula':issues.append('formula_structure_and_calculation_not_established')
                captures = {}
                for name in names:
                    x, y = match.span(name)
                    if x < 0 or x == y:
                        captures[name] = dict(value_literal=None, source_spans=[], status='not_matched'); continue
                    if not a <= x < y <= b:raise ValueError('Named capture lies outside the value span')
                    captures[name] = dict(value_literal=projection.text[x:y], source_spans=projection.evidence(x, y), status='matched')
                unit_literal = match.groupdict().get('unit'); unit_parts = []
                if unit_literal:
                    x, y = match.span('unit')
                    if not a <= x < y <= b:raise ValueError('Printed unit lies outside the value span')
                    unit_parts = projection.evidence(x, y)
                outcomes = [q for q in data.get('quality', {}).get('results', []) if q['page'] in {s['page'] for s in match_parts}]
                row = dict(id='field-'+digest([data['source_pdf_sha256'], data['source_package_sha256'], rules_sha, field['id'], unit['id'], parts])[:24],
                    field_id=field['id'], type=field['type'], value_literal=literal, source_spans=parts, match_source_spans=match_parts,
                    captures=captures, typed_candidate=typed, unit_literal=unit_literal, unit_source_spans=unit_parts,
                    configured_unit_hint=field.get('unit'), validation_issues=issues,
                    native_parent=dict(id=unit['id'], label=unit['label'], source_pointer='/units/'+str(projection.index),
                        spans=unit['spans'], declared_dependencies=unit.get('dependencies', []), boundary_state=unit.get('boundary_state', 'unknown'),
                        quality_observations=[q for q in data.get('quality', {}).get('results', []) if q['page'] in {s['page'] for s in unit['spans']}]),
                    source_seams=projection.seams(parts), match_seams=projection.seams(match_parts),
                    geometry_parts=[dict(source_span=s, geometry=field_geometry(pages[s['page']], layouts.get(s['page'], {}), s['char_start'], s['char_start']+s['char_length'])) for s in parts],
                    source_pdf_sha256=data['source_pdf_sha256'], source_package_sha256=data['source_package_sha256'],
                    rule_set_id=rules['id'], rule_version=rules['version'], rules_sha256=rules_sha, status='candidate',
                    source_quality_observations=outcomes, capture_association='configured_native_parent_match_not_semantic_ownership',
                    projection='exact_native_span_concatenation_no_separator', content_verified=False, source_quality_approval_granted=False,
                    semantic_ownership_approved=False, calculation_allowed=False, publication_eligible=False)
                observations.append(row); found += 1
                if len(observations) > 50000:raise ValueError('More than 50000 multipart candidates; narrow rules')
        statuses[field['id']] = dict(status='candidates_found' if found else 'not_detected_not_proven_absent', matches=found)
    return dict(schema_version=SCHEMA, source_pdf_sha256=data['source_pdf_sha256'], source_package_sha256=data['source_package_sha256'],
        declared_family=family, family_is_verified_legal_scope=False, rule_set_id=rules['id'], rule_version=rules['version'], rules_sha256=rules_sha,
        field_catalog=rules['fields'], field_status=statuses, observations=observations, producer_implementation=implementation,
        native_units_accounted=len(units), semantic_completeness='not_established', source_quality_approval_granted=False,
        semantic_ownership_approved=False, publication_eligible=False)


def evaluate(bundle, references, output):
    """Exact parent plus ordered-span labels; no implicit first-page reference."""
    import json
    from pathlib import Path
    from .field_rules import _decode, source_data, write
    from .hashing import file_sha256
    bundle = Path(bundle).resolve(); output = Path(output).resolve()
    if output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output):
        raise ValueError('Use a new evaluation directory outside the immutable field bundle')
    data = _decode((bundle/'fields.json').read_bytes()); manifest = _decode((bundle/'manifest.json').read_bytes())
    reference = _decode(Path(references).read_bytes())
    for key in ('source_pdf_sha256', 'source_package_sha256', 'rules_sha256'):
        if reference.get(key) != data.get(key):raise ValueError('Multipart reference source/rules version differs: '+key)
    if not reference.get('reviewer') or reference.get('reference_scope') not in ('human_source_review', 'development_fixture'):
        raise ValueError('Declare reviewer and reference provenance')
    source = source_data(Path(manifest['source_package']), data['source_pdf_sha256'])
    pages = {p['page']:p for p in source['pages']}; units = {u['id']:u for u in source['structure']['units']}
    def key(parts, parent, literal):
        if parent not in units or not isinstance(parts, list) or not parts:raise ValueError('Reference needs native parent and ordered source spans')
        for s in parts:
            if s.get('offset_encoding', 'codepoint') != 'codepoint':raise ValueError('Use Unicode codepoint reference offsets')
            page = pages[s['page']]; a = s['char_start']; text = s['exact_quote']
            if not isinstance(a, int) or a < 0 or not text or s['page_text_sha256'] != page['text_sha256'] or page['text'][a:a+len(text)] != text:
                raise ValueError('Multipart reference is not anchored to source')
            if s.get('char_length', len(text)) != len(text):raise ValueError('Multipart reference length differs')
            if not any(t['page'] == s['page'] and t['char_start'] <= a and a+len(text) <= t['char_start']+t['char_length'] for t in units[parent]['spans']):
                raise ValueError('Multipart reference lies outside native parent')
        if ''.join(s['exact_quote'] for s in parts) != literal:raise ValueError('Reference literal differs from ordered parts')
        if any((b['page'], b['char_start']) < (a['page'], a['char_start']+len(a['exact_quote'])) for a,b in zip(parts,parts[1:])):
            raise ValueError('Reference spans reordered or overlap')
        return (parent, tuple((s['page'],s['char_start'],len(s['exact_quote']),s['page_text_sha256'],s['exact_quote']) for s in parts), literal)
    known = {f['id'] for f in data['field_catalog']}; seen = set(); results = []
    for field in reference['fields']:
        name = field['field_id']
        if name not in known or name in seen:raise ValueError('Reference field unknown or duplicated')
        seen.add(name); observed = {}
        for r in data['observations']:
            if r['field_id'] == name:
                k = key(r['source_spans'], r['native_parent']['id'], r['value_literal'])
                if k in observed:raise ValueError('Duplicate multipart observation identity')
                observed[k] = r
        expected = {}; mismatches = []
        for item in field['expected']:
            k = key(item['source_spans'], item['native_unit_id'], item['value_literal'])
            if k in expected:raise ValueError('Duplicate multipart reference')
            expected[k] = item
            if k in observed:
                for property_name in ('typed_candidate', 'unit_literal', 'captures'):
                    if property_name in item and item[property_name] != observed[k].get(property_name):
                        mismatches.append(dict(property=property_name, expected=item[property_name], actual=observed[k].get(property_name)))
        actual_set, expected_set = set(observed), set(expected)
        results.append(dict(field_id=name, true_positive=len(actual_set & expected_set), false_positive=len(actual_set - expected_set),
                            false_negative=len(expected_set - actual_set), value_mismatches=mismatches))
    if not results:raise ValueError('Label at least one field, including empty negative cases')
    tp = sum(r['true_positive'] for r in results); fp = sum(r['false_positive'] for r in results); fn = sum(r['false_negative'] for r in results)
    if any(file_sha256(Path(manifest['source_package'])/name) != h for name,h in manifest['source_files'].items()):
        raise ValueError('Multipart evaluation source changed')
    result = dict(schema_version='pdf2data.field-evaluation.v3', fields_evaluated=len(results), unlabelled_fields=len(known-seen),
        true_positive=tp, false_positive=fp, false_negative=fn, precision=tp/(tp+fp) if tp+fp else 1., recall=tp/(tp+fn) if tp+fn else 1.,
        passed=not (fp or fn or any(r['value_mismatches'] for r in results)), results=results,
        reference_scope=reference['reference_scope'], reviewer=reference['reviewer'], references_sha256=file_sha256(Path(references)),
        source_pdf_sha256=data['source_pdf_sha256'], source_package_sha256=data['source_package_sha256'], rules_sha256=data['rules_sha256'],
        semantic_completeness='not_established', semantic_formula_accuracy_measured=False, publication_eligible=False)
    output.mkdir(parents=True); write(output/'evaluation.json', result); return result
