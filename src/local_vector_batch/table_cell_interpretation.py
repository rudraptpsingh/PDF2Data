"""Bind additive interpreted cell candidates to original pixels and saved feedback.

This consumer does not approve source quality or mutate native text/tables. An
interpretation can preserve a complete logical cell when the review UI exposed
only a clipped slice or a short correction. Reviewer origin is a receipt claim,
not an authentication assertion. Full native and independent readings survive.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def validate_interpretation(question, snapshot, feedback, interpretation):
    """Pure source identity, feedback and complete-candidate checks."""
    if sha(canonical(snapshot['snapshot'])) != snapshot['snapshot_sha256']:
        raise ValueError('Snapshot content hash changed')
    source = snapshot['snapshot']
    for key in ('source_pdf_sha256', 'page'):
        if source[key] != question[key]:
            raise ValueError('Question source identity disagrees with snapshot')
    for key in ('source_pdf_sha256', 'page', 'page_text_sha256'):
        if source[key] != question['source_fingerprint'][key]:
            raise ValueError('Question/source snapshot identity changed')
    if snapshot['fingerprint'] != question['source_fingerprint']:
        raise ValueError('Snapshot extraction/proof fingerprint changed')
    tables = [t for t in source['tables'] if t['table_version_id'] == question['table_version_id']]
    if len(tables) != 1:
        raise ValueError('Missing/ambiguous source table')
    table = tables[0]
    cells = {c['id']: c for c in table['cell_spans']}
    target = cells[question['cell_id']]
    for key, value in [('selected_cell_sha256', target['text']),
                       ('independent_cell_sha256', target['independent_text'])]:
        if question[key] != sha(value):
            raise ValueError('Question reading hash changed')
    for key, value in [('table_geometry_sha256', table['table_geometry_sha256']),
                       ('bbox', target['bbox']), ('selected_text', target['text']),
                       ('independent_text', target['independent_text'])]:
        if question[key] != value:
            raise ValueError('Saved question cell content/geometry changed: ' + key)
    unique = {}
    for row in feedback:
        if row['cell_id'] != question['cell_id']:
            continue
        for key in ('source_pdf_sha256', 'page', 'table_version_id', 'cell_id', 'row', 'column',
                    'bbox', 'selected_cell_sha256', 'independent_cell_sha256',
                    'selected_text', 'independent_text'):
            if row[key] != question[key]:
                raise ValueError('Stale feedback: ' + key)
        if row.get('promotes_page') is not False:
            raise ValueError('Cell feedback cannot promote a page')
        stable = {k: v for k, v in row.items() if k != 'created_at'}
        unique[sha(canonical(stable))] = row
    if len(unique) != 1:
        raise ValueError('Missing/conflicting source-cell feedback')
    decision = next(iter(unique.values()))
    if interpretation['question_id'] != question['focus_key']:
        raise ValueError('Interpretation question changed')
    if interpretation.get('promotes_page') is not False:
        raise ValueError('Interpretation cannot approve a page')
    if interpretation.get('review_origin') != 'model_assisted_original_raster_with_recorded_user_clarification':
        raise ValueError('Unsupported interpretation origin')
    clarification = interpretation.get('user_clarification', {})
    if not clarification.get('quote') or clarification.get('channel') != 'trusted_current_thread_user_clarification_via_parent':
        raise ValueError('Missing explicit clarification provenance')
    if interpretation.get('reviewer_identity_authenticated') is not False:
        raise ValueError('No authentication claim supported')
    ids = interpretation['source_cell_ids']
    if len(ids) != len(set(ids)) or question['cell_id'] not in ids or any(x not in cells for x in ids):
        raise ValueError('Invalid source cell coverage')
    selected = [cells[x] for x in ids]
    action = interpretation['action']
    if action == 'logical_cell_continuation':
        ordered = sorted(selected, key=lambda c: c['row'])
        if (len(selected) < 2 or len({c['column'] for c in selected}) != 1 or
                [c['row'] for c in ordered] != list(range(ordered[0]['row'], ordered[-1]['row']+1)) or
                len({(c['bbox'][0], c['bbox'][2]) for c in selected}) != 1):
            raise ValueError('Logical continuation needs contiguous same-column source cells')
        raw = '\n'.join(c['text'] for c in ordered if c['text'])
        if interpretation['literal_text'] != raw:
            raise ValueError('Continuation must retain exact complete native cell lines')
    elif action == 'source_literal_and_typography':
        if len(selected) != 1:
            raise ValueError('Literal correction needs exactly one original cell')
        # Full-line review is required, rather than applying an abbreviated UI correction.
        lines = interpretation.get('complete_visual_lines')
        if not lines or interpretation['literal_text'] != '\n'.join(lines):
            raise ValueError('Literal must preserve the complete reviewed source-cell lines')
        if interpretation.get('complete_cell_visually_reviewed') is not True:
            raise ValueError('Short correction cannot replace complete source cell')
    else:
        raise ValueError('Unsupported candidate action')
    text = interpretation['literal_text']
    for style in interpretation.get('typography', []):
        start, end = style['start'], style['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != style['literal']:
            raise ValueError('Typography span is not exact')
        if style['kind'] != 'superscript' or not style.get('source_visual_basis'):
            raise ValueError('Unsupported/unbound typography')
    for association in interpretation.get('associations', []):
        linked = cells.get(association['source_cell_id'])
        if not linked or linked['text'] != association['source_literal']:
            raise ValueError('Association must bind an existing exact source cell')
    return {'contract_version': 'pdf2data.interpreted-table-cell-candidate.v1',
            'status': 'source_bound_interpreted_candidate', 'source_pdf_sha256': source['source_pdf_sha256'],
            'page': source['page'], 'original_page_text_sha256': source['page_text_sha256'],
            'table_version_id': table['table_version_id'], 'table_geometry_sha256': table['table_geometry_sha256'],
            'original_cells': selected, 'original_feedback': decision,
            'feedback_unique_count': 1, 'feedback_record_count': sum(r['cell_id'] == question['cell_id'] for r in feedback),
            'interpretation': interpretation, 'literal_sha256': sha(text),
            'source_text_modified': False, 'source_packages_modified': False,
            'source_quality_clearance': False, 'table_semantics_approved': False,
            'publication_eligible': False, 'legal_correctness_claimed': False,
            'limitations': ['Additive candidate only; requires registered reconstruction and fresh source/page gates.',
                            'Local UI origin and forwarded user clarification do not authenticate a reviewer account.']}


def build_candidates(queue, authored, output):
    """Freshly re-render original source crops, then freeze additive candidates."""
    import pdfplumber
    queue, output = Path(queue), Path(output)
    if output.exists():
        raise ValueError('Output must be a new immutable directory')
    rows = _rows(queue/'review-manifest.jsonl')
    feedback_path = queue/'table-cell-feedback.jsonl'
    feedback = _rows(feedback_path)
    authored = json.loads(Path(authored).read_text())
    if len(authored) != len(rows) or len({r['question_id'] for r in authored}) != len(authored):
        raise ValueError('Interpretations must cover each source question exactly once')
    prepared = []
    for row in rows:
        q = row['target_cell']
        if sha(Path(row['source_pdf']).read_bytes()) != q['source_pdf_sha256']:
            raise ValueError('Original PDF bytes changed')
        snapshot_path = Path(q['source_snapshot_path'])
        snapshot = json.loads(snapshot_path.read_text())
        matches = [r for r in authored if r['question_id'] == q['focus_key']]
        if len(matches) != 1:
            raise ValueError('Missing interpreted source question')
        item = matches[0]
        result = validate_interpretation(q, snapshot, feedback, item)
        box = item['review_crop_source_bbox']
        if len(box) != 4 or not (box[0] <= min(c['bbox'][0] for c in result['original_cells']) and
                               box[1] <= min(c['bbox'][1] for c in result['original_cells']) and
                               box[2] >= max(c['bbox'][2] for c in result['original_cells']) and
                               box[3] >= max(c['bbox'][3] for c in result['original_cells'])):
            raise ValueError('Review crop omits original cell coverage')
        with pdfplumber.open(row['source_pdf']) as pdf:
            crop = pdf.pages[q['page']-1].crop(tuple(box)).to_image(resolution=240)
            buffer = io.BytesIO(); crop.save(buffer, format='PNG'); pixels = buffer.getvalue()
        if sha(pixels) != item['review_crop_sha256']:
            raise ValueError('Reviewed original crop cannot be reproduced exactly')
        result['source_title'] = row['title']
        result['source_url'] = row['source_url']
        result['snapshot_sha256'] = sha(snapshot_path.read_bytes())
        result['source_crop'] = {'bbox': box, 'dpi': 240, 'sha256': sha(pixels)}
        prepared.append((result, pixels, snapshot_path.read_bytes()))
    output.mkdir(parents=True)
    for i, (result, pixels, snapshot) in enumerate(prepared, 1):
        (output/f'candidate-{i:03}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        (output/f'candidate-{i:03}-original.png').write_bytes(pixels)
        (output/f'candidate-{i:03}-snapshot.json').write_bytes(snapshot)
    (output/'feedback-original.jsonl').write_bytes(feedback_path.read_bytes())
    (output/'interpretations.json').write_text(json.dumps(authored, ensure_ascii=False, indent=2)+'\n')
    receipt = {'contract_version': 'pdf2data.interpreted-table-cells-receipt.v1',
               'candidate_count': len(prepared), 'raw_feedback_count': len(feedback),
               'source_text_modified': False, 'source_packages_modified': False,
               'page_quality_cleared': 0, 'publication_eligible': False,
               'files': {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir())}}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', required=True)
    parser.add_argument('--interpretations', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build_candidates(args.queue, args.interpretations, args.output), indent=2))
