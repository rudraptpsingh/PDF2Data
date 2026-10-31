"""Independent pypdf operator-position witnesses for repeated single-line cells.

This proves a row/cell context only. Native glyph/SPACE/MCID/clip/paint ownership
and the exact complete-page projection remain mandatory in the caller. No bbox
is manufactured for glyph ink: origins and advances below come from PDF text
operators, decoded CMaps and their effective text matrices.
"""
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re

CONTRACT = 'pdf2data.independent-source-cell-row-context.v1'


class RowContextUnavailable(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def rules():
    path = Path(__file__).with_name('native_cell_row_context_rules.json')
    value = json.loads(path.read_text())
    if value.get('contract_version') != 'pdf2data.independent-cell-row-context-rules.v1':
        raise RowContextUnavailable('unknown_row_context_policy')
    return value, sha(path.read_bytes())


def _stat(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def capture_operator_page(source, page, expected_sha):
    """Fresh auxiliary capture using the selected installed pypdf implementation."""
    from pypdf import PdfReader
    import pypdf
    from pypdf.generic import ContentStream
    from pypdf._text_extraction._layout_mode._fixed_width_page import recurse_to_target_op, set_font_operation
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager
    source = Path(source).resolve(); before = _stat(source)
    if sha(source.read_bytes()) != expected_sha:
        raise RowContextUnavailable('operator_source_hash_changed')
    library = Path(pypdf.__file__).parent
    def implementation_sha():
        return sha(b''.join(str(p.relative_to(library)).encode() + p.read_bytes() for p in sorted(library.rglob('*.py'))))
    dependency_before = implementation_sha()
    reader = PdfReader(source); target = reader.pages[page - 1]
    media, crop = [float(x) for x in target.mediabox], [float(x) for x in target.cropbox]
    if (target.rotation or media != crop or media[:2] != [0.0, 0.0] or
            any(not math.isfinite(x) for x in media) or media[2] <= 0 or media[3] <= 0 or
            float(target.get('/UserUnit', 1)) != 1):
        raise RowContextUnavailable('unsupported_operator_coordinate_frame')
    contents = target.get_contents()
    if contents is None:
        raise RowContextUnavailable('operator_page_has_no_content')
    content_sha = sha(contents.get_data()); sequence = ContentStream(contents, target.pdf, 'bytes').operations
    marked, shows, clips = [], [], []
    def operators():
        for index, (operands, op) in enumerate(sequence):
            if op in (b'BMC', b'BDC'):
                props = operands[1] if op == b'BDC' and len(operands) > 1 else None
                mcid = props.get('/MCID') if isinstance(props, dict) else None
                marked.append({'operator_index': index, 'tag': str(operands[0]) if operands else None,
                               'MCID': int(mcid) if type(mcid) is int or type(mcid).__name__ == 'NumberObject' else None,
                               'property_reference': str(props) if isinstance(props, str) else None})
            elif op == b'EMC':
                if not marked:raise RowContextUnavailable('unbalanced_source_marked_content')
                marked.pop()
            if op in (b'W', b'W*'):clips.append(index)
            pieces = operands[0] if op == b'TJ' and operands else [operands[-1]] if op in (b'Tj', b"'", b'"') and operands else []
            for piece_index, value in enumerate(pieces):
                if isinstance(value, (str, bytes)):
                    raw = value if isinstance(value, bytes) else value.encode('utf-8', 'surrogatepass')
                    shows.append({'operator_index': index, 'operator': op.decode('ascii'), 'piece_index': piece_index,
                                  'raw_operand_sha256': sha(raw), 'marked_content': list(marked)})
            yield operands, op
    stream = iter(operators()); state = TextStateManager(); groups, text_states = [], []
    # The actual pypdf decoder owns Tj/TJ text state, CMaps, font advances and
    # matrices. This outer loop is its documented-in-code layout entry loop.
    fonts = target._layout_mode_fonts()
    for operands, op in stream:
        if op in (b'BT', b'q'):
            a, b = recurse_to_target_op(stream, state, b'ET' if op == b'BT' else b'Q', fonts, False)
            groups.extend(a); text_states.extend(b)
        elif op == b'Tf':set_font_operation(state, fonts, operands)
        else:state.set_state_param(op, operands)
    if marked or len(shows) != len(text_states):
        raise RowContextUnavailable('source_operator_provenance_cardinality_changed')
    runs = []
    for provenance, s in zip(shows, text_states):
        value = s.value if isinstance(s.value,bytes) else s.value.encode('utf-8','surrogatepass')
        if sha(value) != provenance['raw_operand_sha256']:
            raise RowContextUnavailable('operator_text_piece_provenance_changed')
        runs.append({**provenance, 'text': s.text, 'origin_pdf': [s.tx, s.ty],
            'advance_end_pdf_x': s.displaced_tx, 'font': s.font.name, 'font_size': s.font_size,
            'font_interpretable': s.font.interpretable, 'text_rise': s.Ts,
            'effective_transform': list(s.transform), 'rotated': s.rotated, 'flip_vertical': s.flip_vertical})
    dependency_after = implementation_sha()
    if _stat(source) != before or sha(source.read_bytes()) != expected_sha or dependency_before != dependency_after:
        raise RowContextUnavailable('operator_source_changed_during_capture')
    return {'contract_version': 'pdf2data.pypdf-source-operator-page.v1', 'source_pdf_sha256': expected_sha,
        'page': page, 'width': media[2], 'height': media[3], 'rotation': 0,
        'content_stream_sha256': content_sha, 'reader': 'pypdf_original_Tj_TJ_text_state_and_CMap',
        'reader_version': version('pypdf'), 'reader_implementation_sha256': dependency_after,
        'groups': groups, 'text_show_operators': runs, 'clip_operator_indices': clips,
        'ink_bbox_inferred': False, 'clip_or_visibility_approved': False,
        'source_quality_clearance': False, 'answer_publication_eligible': False}


def _compact(value):
    return ''.join(c for c in value if not c.isspace())


def _line_in_box(capture, bounds, policy):
    if (not isinstance(bounds, list) or len(bounds) != 4 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)):
        raise RowContextUnavailable('invalid_cell_bounds')
    x0, y0, x1, y1 = bounds; tolerance = policy['coordinate_tolerance_points']
    groups, runs = [], []
    for g in capture['groups']:
        if not g['text'].strip():continue
        if any(type(g[k]) not in (int, float) or not math.isfinite(g[k]) for k in ('tx','ty','displaced_tx','font_size')):
            raise RowContextUnavailable('nonfinite_source_operator_group')
        baseline = capture['height'] - g['ty']; left, right = g['tx'], g['displaced_tx']
        if y0 < baseline < y1 and min(right, x1) > max(left, x0):
            if left < x0 - tolerance or right > x1 + tolerance:
                raise RowContextUnavailable('third_reader_group_crosses_source_cell')
            groups.append(g)
    if len(groups) != 1:
        raise RowContextUnavailable('third_reader_cell_not_one_complete_positioned_line')
    group = groups[0]; baseline = capture['height'] - group['ty']
    for run in capture['text_show_operators']:
        if not run['text'].strip():continue
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in [*run['origin_pdf'],run['advance_end_pdf_x'],run['font_size'],run['text_rise'],*run.get('effective_transform',[])]):
            raise RowContextUnavailable('nonfinite_source_operator_text_state')
        left, bottom = run['origin_pdf']; right = run['advance_end_pdf_x']; top = capture['height'] - bottom
        if y0 < top < y1 and min(right, x1) > max(left, x0):
            if left < x0 - tolerance or right > x1 + tolerance:
                raise RowContextUnavailable('third_reader_operator_crosses_source_cell')
            if (run['font_interpretable'] is not True or run['rotated'] is not False or run['flip_vertical'] is not False or
                    run['text_rise'] != 0 or abs(top - baseline) > tolerance or
                    abs(run['font_size'] - group['font_size']) > tolerance):
                raise RowContextUnavailable('third_reader_script_font_or_2D_text_not_plain_line')
            runs.append(run)
    if not runs or _compact(''.join(r['text'] for r in runs)) != _compact(group['text']):
        raise RowContextUnavailable('third_reader_line_operator_order_or_coverage_changed')
    if policy['ascii_plain_line_only'] and any(ord(c) > 127 for c in group['text']):
        raise RowContextUnavailable('non_ASCII_plain_line_requires_separate_script_proof')
    return {'text': group['text'], 'baseline_top_points': baseline, 'source_operator_group': group,
            'operator_indices': [r['operator_index'] for r in runs], 'text_show_operators': runs}


def prove_row_context(capture, cell, anchor, processed, selected_page, raw_native_page, third_layout):
    """Pure, typed context witness. No source quality is granted by this object."""
    policy, policy_sha = rules()
    if capture.get('contract_version') != 'pdf2data.pypdf-source-operator-page.v1' or capture.get('rotation') != 0:
        raise RowContextUnavailable('unverified_operator_capture_frame')
    if (not processed or '\n' in processed or '\r' in processed or '\n' in cell['text'] or '\r' in cell['text'] or
            cell.get('row_span') != 1 or anchor.get('row_span') != 1 or cell['row'] != anchor['row']):
        raise RowContextUnavailable('bounded_context_initial_single_line_row_scope')
    if (anchor['id'].rsplit(':r',1)[0] != cell['id'].rsplit(':r',1)[0] or
            anchor.get('column_span') != 1 or anchor['column'] + 1 != cell['column']):
        raise RowContextUnavailable('anchor_outside_same_complete_source_grid')
    a, b = anchor['bbox'], cell['bbox']; tolerance = policy['coordinate_tolerance_points']
    if (anchor['column'] >= cell['column'] or a[2] > b[0] or
            abs(a[1] - b[1]) > tolerance or abs(a[3] - b[3]) > tolerance):
        raise RowContextUnavailable('anchor_is_not_same_physical_left_row')
    if not re.fullmatch(policy['row_anchor_pattern'], anchor['text'].strip()):
        raise RowContextUnavailable('row_anchor_is_not_configured_literal_identifier')
    target = _line_in_box(capture, b, policy); row_anchor = _line_in_box(capture, a, policy)
    if target['text'].split() != processed.split() or row_anchor['text'].strip() != anchor['text'].strip():
        raise RowContextUnavailable('independent_row_or_cell_literal_differs')
    if abs(target['baseline_top_points'] - row_anchor['baseline_top_points']) > tolerance:
        raise RowContextUnavailable('anchor_and_cell_have_different_operator_baselines')
    words = r'\s+'.join(re.escape(t) for t in processed.split())
    pattern = r'(?<!\S)' + re.escape(anchor['text'].strip()) + r'\s+(' + words + r')(?!\S)'
    selected = list(re.finditer(pattern, selected_page))
    if len(selected) != 1:
        raise RowContextUnavailable('selected_row_anchored_context_missing_or_ambiguous')
    raw_words = r'\s+'.join(re.escape(t) for t in cell['text'].split())
    raw_hits = list(re.finditer(r'(?<!\S)' + raw_words + r'(?!\S)', raw_native_page))
    if len(raw_hits) != 1:
        raise RowContextUnavailable('raw_native_cell_context_missing_or_ambiguous')
    hit = selected[0]; start, stop = hit.span(1)
    witness = {'contract_version': CONTRACT, 'status': 'source_row_context_verified',
        'source_pdf_sha256': capture['source_pdf_sha256'], 'page': capture['page'],
        'cell_id': cell['id'], 'cell_bbox': b, 'table_geometry_sha256': cell['table_geometry_sha256'],
        'raw_cell_sha256': sha(cell['text']), 'processed_tokens': processed.split(),
        'selected_page_sha256': sha(selected_page), 'raw_native_page_sha256': sha(raw_native_page),
        'third_layout_sha256': sha(third_layout), 'anchor_cell_id': anchor['id'], 'anchor_bbox': a,
        'anchor_literal': anchor['text'].strip(), 'independent_cell': target, 'independent_row_anchor': row_anchor,
        'operator_capture_sha256': sha(canonical(capture)), 'content_stream_sha256': capture['content_stream_sha256'],
        'reader': capture['reader'], 'reader_version': capture['reader_version'],
        'reader_implementation_sha256': capture['reader_implementation_sha256'],
        'source_clip_operator_indices_preserved': capture['clip_operator_indices'],
        'selected_page_context': {'codepoint_span': [start, stop], 'exact_quote': selected_page[start:stop],
            'exact_quote_sha256': sha(selected_page[start:stop]), 'full_text_sha256': sha(selected_page),
            'basis': 'same_source_independent_operator_row_anchor; no global target-phrase uniqueness claim'},
        'rule_configuration_sha256': policy_sha, 'implementation_sha256': sha(Path(__file__).read_bytes()),
        'ink_bbox_inferred': False, 'clip_or_glyph_ownership_approved': False,
        'raw_text_modified': False, 'source_quality_clearance': False, 'legal_review': 'not_run',
        'answer_publication_eligible': False}
    witness['witness_sha256'] = sha(canonical(witness))
    return witness


def capture_source_row_context(native_page, cell, independent, selected_page, raw_native_page, third_layout, source_sha):
    """Fresh integration entry; only the trusted complete source-grid context is used."""
    from .native_tight_cell_ownership import _PAGE_SOURCE_CELLS
    if (not cell['text'] or not independent or '\n' in cell['text'] or '\r' in cell['text'] or
            '\n' in independent or '\r' in independent or _compact(cell['text']) != _compact(independent)):
        raise RowContextUnavailable('outside_single_plain_line_SPACE_context_scope')
    context = _PAGE_SOURCE_CELLS.get()
    source_name = getattr(getattr(getattr(native_page,'pdf',None),'stream',None),'name',None)
    if (context is None or context['native_page'] is not native_page or not source_name or
            context['source'] != Path(source_name).resolve() or context['page'] != native_page.page_number or
            context['source_sha256'] != source_sha):
        raise RowContextUnavailable('trusted_same_source_grid_context_missing')
    anchors = [c for c in context['cells'] if c.get('row') == cell['row'] and
               c.get('column') == cell['column'] - 1 and c['id'].rsplit(':r',1)[0] == cell['id'].rsplit(':r',1)[0]]
    if len(anchors) != 1 or anchors[0].get('readers_agree') is not True:
        raise RowContextUnavailable('unique_independently_agreeing_row_anchor_missing')
    if 'independent_operator_capture' not in context:
        context['independent_operator_capture'] = capture_operator_page(context['source'],context['page'],source_sha)
    captured = context['independent_operator_capture']
    return prove_row_context(captured,cell,anchors[0],' '.join(independent.split()),selected_page,raw_native_page,third_layout)


def validate_row_context(witness, cell, processed, selected_page, raw_native_page, third_layout, source_sha, page):
    if not isinstance(witness, dict) or not all(isinstance(x,str)for x in (processed,selected_page,raw_native_page,third_layout)):return False
    _, policy_sha = rules()
    expected = {'contract_version': CONTRACT, 'status': 'source_row_context_verified', 'source_pdf_sha256': source_sha,
        'page': page, 'cell_id': cell['id'], 'cell_bbox': cell['bbox'],
        'table_geometry_sha256': cell['table_geometry_sha256'], 'raw_cell_sha256': sha(cell['text']),
        'processed_tokens': processed.split(), 'selected_page_sha256': sha(selected_page),
        'raw_native_page_sha256': sha(raw_native_page), 'third_layout_sha256': sha(third_layout),
        'rule_configuration_sha256': policy_sha, 'implementation_sha256': sha(Path(__file__).read_bytes()),
        'ink_bbox_inferred': False, 'clip_or_glyph_ownership_approved': False, 'raw_text_modified': False,
        'source_quality_clearance': False, 'legal_review': 'not_run', 'answer_publication_eligible': False}
    return (all(witness.get(k) == v for k, v in expected.items()) and
            witness.get('witness_sha256') == sha(canonical({k:v for k,v in witness.items() if k != 'witness_sha256'})))
