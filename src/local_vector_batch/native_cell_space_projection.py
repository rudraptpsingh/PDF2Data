"""Typed processed-cell view for a misplaced leading source SPACE object.

The raw matrix is preserved. Only a table-reader separator caused by a SPACE
painted inside an adjacent letter run can be removed in the additive view.
Geometry, PDFium, unique full-page and pypdf contexts must all agree. This never
edits page text, numbers, operators, source objects or the original cell value.
"""
import hashlib
import json
import math
from pathlib import Path
import re


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def rules():
    path = Path(__file__).with_name('native_cell_space_projection_rules.json')
    value = json.loads(path.read_text())
    if value.get('schema_version') != 'pdf2data.misplaced-source-space-projection-rules.v1':
        raise ValueError('Unknown misplaced SPACE projection policy')
    return value, sha(path.read_bytes())


def compact(value):
    return ''.join(c for c in value if not c.isspace())


def separators(value):
    result = {}; count = 0
    for match in re.finditer(r'\s+|\S', value):
        if match.group().isspace():
            result[count] = (match.start(), match.end(), match.group())
        else:
            count += 1
    return result


def box(glyph):
    return [glyph[k] for k in ('x0', 'top', 'x1', 'bottom')]


def intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _context(value, full):
    tokens = re.findall(r'\S+', value)
    if not tokens:
        return None
    pattern = r'(?<!\S)' + r'\s+'.join(re.escape(t) for t in tokens) + r'(?!\S)'
    hits = list(re.finditer(pattern, full))
    if len(hits) != 1:
        return None
    hit = hits[0]
    return {'codepoint_span': [hit.start(), hit.end()], 'exact_quote': hit.group(),
            'exact_quote_sha256': sha(hit.group()), 'full_text_sha256': sha(full)}


def propose_processed_cell(raw, independent, page_glyphs, third, selected_page, *,
                           cell_bbox, source_pdf_sha256, page, cell_id,
                           table_geometry_sha256, raw_native_page=None, row_context=None, row_context_factory=None):
    """Pure candidate proof. A fresh raster/paint ownership check is additional.

    Original glyph order is retained. Any nonblank glyph crossing the cell,
    source letter change, real interword SPACE, ambiguous context, digit or
    operator-adjacent separator prevents this narrow projection.
    """
    policy, policy_sha = rules()
    if not raw or len(raw) > policy['maximum_cell_codepoints'] or compact(raw) != compact(independent):
        return None
    wanted = separators(raw); present = separators(independent); plain = compact(raw)
    removed = [k for k in wanted if 0 < k < len(plain) and k not in present]
    if not removed or len(removed) > policy['maximum_removed_separators']:
        return None
    if any(0 < k < len(plain) and k not in wanted for k in present):
        return None
    glyphs = []; tolerance = policy['coordinate_tolerance_points']
    for index, glyph in enumerate(page_glyphs):
        bounds = box(glyph)
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds):
            return None
        if not intersects(bounds, cell_bbox):
            continue
        if glyph['text'].strip() and not all((
                cell_bbox[0] - tolerance <= bounds[0], bounds[2] <= cell_bbox[2] + tolerance,
                cell_bbox[1] - tolerance <= bounds[1], bounds[3] <= cell_bbox[3] + tolerance)):
            return None
        if not (cell_bbox[0] <= (bounds[0] + bounds[2]) / 2 <= cell_bbox[2]
                and cell_bbox[1] <= (bounds[1] + bounds[3]) / 2 <= cell_bbox[3]):
            continue
        record = {'source_glyph_index': index, 'text': glyph['text'], 'bbox': bounds,
                  'font': glyph.get('fontname', glyph.get('font')), 'size': glyph.get('size')}
        if not isinstance(record['size'], (int, float)) or record['size'] <= 0:
            return None
        glyphs.append(record)
    nonwhite = [g for g in glyphs if g['text'].strip()]
    if any(len(g['text']) != 1 for g in nonwhite) or ''.join(g['text'] for g in nonwhite) != plain:
        return None
    witnesses = []; used_spaces = set()
    for k in removed:
        lo, hi, separator = wanted[k]
        if any(ord(c) not in policy['raw_separator_codepoints'] for c in separator):
            return None
        left, right = nonwhite[k - 1], nonwhite[k]
        if not re.fullmatch(r'[A-Za-z]', left['text']) or not re.fullmatch(r'[A-Za-z]', right['text']):
            return None
        if left['font'] != right['font'] or abs(left['size'] - right['size']) > tolerance:
            return None
        a, b = left['bbox'], right['bbox']; em = min(left['size'], right['size'])
        gap = b[0] - a[2]; height = min(a[3] - a[1], b[3] - b[1])
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        if (height <= 0 or overlap / height < policy['minimum_vertical_overlap_fraction'] or
                gap < -policy['maximum_overlap_em'] * em or
                gap > min(policy['maximum_gap_points'], policy['maximum_gap_em'] * em)):
            return None
        # A real source separator emitted between these letters cannot be
        # erased, even when another misplaced SPACE happens to overlap them.
        if any(g['text'].isspace() and left['source_glyph_index'] < g['source_glyph_index'] < right['source_glyph_index']
               for g in glyphs):
            return None
        matches = []
        for g in glyphs:
            if len(g['text']) != 1 or ord(g['text']) not in policy['source_space_codepoints']:
                continue
            bounds = g['bbox']; center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
            if (g['source_glyph_index'] >= left['source_glyph_index'] or
                    g['font'] != left['font'] or abs(g['size'] - em) > tolerance or
                    not 0 < bounds[2] - bounds[0] <= policy['maximum_space_width_em'] * em or
                    not a[0] - tolerance <= bounds[0] <= b[0] + tolerance):
                continue
            inside_letter = any(r[0] + tolerance < center[0] < r[2] - tolerance
                                and r[1] <= center[1] <= r[3] for r in (a, b))
            if inside_letter and min(bounds[3], a[3], b[3]) - max(bounds[1], a[1], b[1]) >= policy['minimum_vertical_overlap_fraction'] * height:
                matches.append(g)
        if len(matches) != 1 or matches[0]['source_glyph_index'] in used_spaces:
            return None
        used_spaces.add(matches[0]['source_glyph_index'])
        witnesses.append({'raw_separator_span': [lo, hi], 'raw_exact_separator': separator,
                          'nonwhitespace_boundary': k, 'left_source_glyph': left,
                          'right_source_glyph': right, 'misplaced_leading_source_space': matches[0],
                          'source_gap_points': gap, 'source_gap_em': gap / em,
                          'vertical_overlap_fraction': overlap / height})
    processed = raw
    for witness in reversed(witnesses):
        lo, hi = witness['raw_separator_span']; processed = processed[:lo] + processed[hi:]
    from .native_consensus import agrees
    if not agrees(processed, independent, exact_characters=True):
        return None
    selected_context = _context(processed, selected_page); third_context = _context(processed, third)
    used_row_context = None
    if not selected_context or not third_context:
        context_cell={'id':cell_id,'bbox':list(cell_bbox),'text':raw,'table_geometry_sha256':table_geometry_sha256}
        if row_context is None and row_context_factory is not None:row_context=row_context_factory(processed)
        if isinstance(row_context,dict)and row_context.get('contract_version')=='pdf2data.independent-multiline-cell-context.v1':
            from .native_cell_multiline_context import validate_multiline_context
            context_valid=validate_multiline_context(row_context,context_cell,processed,selected_page,raw_native_page,third,source_pdf_sha256,page)
        else:
            from .native_cell_row_context import validate_row_context
            context_valid=validate_row_context(row_context,context_cell,processed,selected_page,raw_native_page,third,source_pdf_sha256,page)
        if not context_valid:
            return None
        used_row_context=row_context
        selected_context=row_context['selected_page_context']
        third_context={'kind':'independent_source_operator_cell_row_context',
            'operator_capture_sha256':row_context['operator_capture_sha256'],
            'independent_cell':row_context['independent_cell'],
            'independent_row_anchor':row_context['independent_row_anchor'],
            'exact_quote':row_context['independent_cell']['text'],
            'exact_quote_sha256':sha(row_context['independent_cell']['text']),
            'source_pdf_sha256':source_pdf_sha256,'page':page,
            'whole_layout_text_sha256':sha(third),'no_contiguous_whole_cell_layout_span_claim':True}
    selected_reading=selected_context.get('assembled_cell_text',selected_context.get('exact_quote'))
    if not agrees(processed, selected_reading, exact_characters=True) or not agrees(processed, third_context['exact_quote'], exact_characters=True):
        return None
    proof = {'contract_version': 'pdf2data.source-bound-processed-cell.v1',
             'operation': 'misplaced_leading_source_SPACE_projection', 'status': 'candidate_pending_source_ink_ownership',
             'source_pdf_sha256': source_pdf_sha256, 'page': page, 'cell_id': cell_id,
             'cell_bbox': list(cell_bbox), 'table_geometry_sha256': table_geometry_sha256,
             'raw_text': raw, 'raw_cell_sha256': sha(raw), 'processed_text': processed,
             'processed_cell_sha256': sha(processed), 'independent_text': independent,
             'independent_cell_sha256': sha(independent), 'page_text_sha256': sha(selected_page),
             'selected_page_context': selected_context, 'third_reader_context': third_context,
             'raw_native_page_context': _context(raw, raw_native_page) if raw_native_page is not None else None,
             'raw_native_page_sha256': sha(raw_native_page) if raw_native_page is not None else None,
             'source_cell_glyphs_sha256': sha(canonical(glyphs)), 'source_space_witnesses': witnesses,
             'rule_configuration_sha256': policy_sha, 'implementation_sha256': sha(Path(__file__).read_bytes()),
             'raw_matrix_modified': False, 'page_text_modified': False, 'source_objects_modified': False,
             'source_quality_clearance': False, 'legal_review': 'not_run', 'answer_publication_eligible': False}
    if used_row_context is not None:proof['source_row_context']=used_row_context
    proof['processed_cell_version_id'] = sha(canonical(proof))
    return proof


def prove_processed_cell(native_page, pdfium_page, text_page, cell, independent, third, selected_page, source_sha, *, context_diagnostic=None):
    """Registered fresh-source wrapper; no supplied proof skips ink/paint checks."""
    from .native_tight_cell_ownership import _complete_source_cells, _PAGE_SOURCE_CELLS, _prepare_ownership_page
    proposal = propose_processed_cell(cell['text'] or '', independent, native_page.chars, third, selected_page,
                                      cell_bbox=cell['bbox'], source_pdf_sha256=source_sha,
                                      page=native_page.page_number, cell_id=cell['id'],
                                      table_geometry_sha256=cell['table_geometry_sha256'],
                                      raw_native_page=native_page.extract_text() or '')
    if proposal is None:
        from .native_cell_row_context import capture_source_row_context,RowContextUnavailable
        def context_for(processed):
            if '\n'in(cell['text']or'')or'\r'in(cell['text']or''):
                from .native_cell_multiline_context import capture_source_multiline_context
                return capture_source_multiline_context(native_page,cell,processed,selected_page,native_page.extract_text()or'',third,source_sha)
            return capture_source_row_context(native_page,cell,independent,selected_page,native_page.extract_text()or'',third,source_sha)
        try:
            proposal=propose_processed_cell(cell['text']or'',independent,native_page.chars,third,selected_page,
                cell_bbox=cell['bbox'],source_pdf_sha256=source_sha,page=native_page.page_number,cell_id=cell['id'],
                table_geometry_sha256=cell['table_geometry_sha256'],raw_native_page=native_page.extract_text()or'',row_context_factory=context_for)
        except RowContextUnavailable as error:
            if context_diagnostic is not None:
                context_diagnostic.update(kind='independent_row_context_not_proved',reason=str(error),
                    source_pdf_sha256=source_sha,page=native_page.page_number,cell_id=cell['id'],
                    source_quality_clearance=False,answer_publication_eligible=False)
            return None
        if proposal is None:
            if context_diagnostic is not None:context_diagnostic.update(kind='source_projection_predicate_still_not_proved',
                source_quality_clearance=False,answer_publication_eligible=False)
            return None
    source_name = getattr(getattr(getattr(native_page, 'pdf', None), 'stream', None), 'name', None)
    if not source_name:
        return None
    complete, by_box = _complete_source_cells(native_page, source_name, native_page.page_number)
    matched = by_box.get(tuple(cell['bbox']), [])
    if len(matched) != 1 or any(other is not matched[0] and intersects(other['bbox'], cell['bbox']) for other in complete):
        return None
    context = _PAGE_SOURCE_CELLS.get()
    if (context is None or context['native_page'] is not native_page or context['source'] != Path(source_name).resolve()
            or context['page'] != native_page.page_number):
        return None
    if context['prepared'] is None:
        context['prepared'] = _prepare_ownership_page(source_name, native_page.page_number, native_page,
                                                     pdfium_page, text_page, third)
    report = _projection_context(context['prepared'], native_page, cell, source_sha)
    if report is None:
        return None
    proposal['source_ink_ownership'] = report
    proposal['status'] = 'source_proved'
    proposal['processed_cell_version_id'] = sha(canonical({k: v for k, v in proposal.items() if k != 'processed_cell_version_id'}))
    return proposal


def _projection_context(prepared, native_page, cell, source_sha):
    """Exact original cell/glyph context; complete raster stays a page gate.

    Unlike a guessed glyph-run envelope, the ORIGINAL cell bounds remain the
    ownership limit. Tight independent glyphs may not cross it. Known table
    paths are retained for the final full-page raster proof, never masked here.
    """
    from .native_tight_cell_ownership import supported_contexts, visible_source_color, ink_count
    from .native_marked_clip_witness import marked_clip_contexts, prove_marked_glyph_clips
    from .text_script import native_font_script_evidence
    from .quality_gate import load_policy
    bounds = cell['bbox']; precision = 0.0001
    if prepared['source_sha'] != source_sha or prepared['annotation_guard']['has_annotations']:
        return None
    if prepared['unsupported'] or not supported_contexts(
            [d for d in prepared['paint']['drawings'] if d['type'] != 'clip'], bounds, prepared['policy']):
        return None
    native_ids = [i for i, g in enumerate(native_page.chars) if g['text'].strip() and
                  bounds[0] <= (g['x0'] + g['x1']) / 2 <= bounds[2] and
                  bounds[1] <= (g['top'] + g['bottom']) / 2 <= bounds[3]]
    native_glyphs = [native_page.chars[i] for i in native_ids]
    if native_font_script_evidence(native_glyphs, load_policy())['required_scripts']:
        return None
    for kind in ('images', 'curves'):
        if any(intersects(box(obj), bounds) for obj in getattr(native_page, kind, [])):
            return None
    if any(g['text'].strip() and intersects(g['bbox'], bounds) and
           (g['visibility_state'] != 'retained_visible_or_unproven' or not visible_source_color(g.get('color')))
           for g in prepared['visibility']):
        return None
    selected = []
    for native_index in native_ids:
        matches = [g for g in prepared['characters'] if g['native_matches'] == [native_index]]
        if len(matches) != 1:
            return None
        independent = matches[0]; tight = independent['tight_bbox']
        if not (bounds[0] - precision <= tight[0] <= tight[2] <= bounds[2] + precision and
                bounds[1] - precision <= tight[1] <= tight[3] <= bounds[3] + precision):
            return None
        if ink_count(prepared['ink'], tight, prepared['scale']) == 0:
            return None
        if any(fill['fill'] != [1., 1., 1.] and intersects(fill['effective_bbox'], tight)
               for fill in prepared['fills']):
            return None
        # A real foreign stroke through glyph ink is not an allowed grid mask.
        for line in native_page.lines:
            width = float(line.get('linewidth', 0)); b = box(line)
            if width <= 0:
                continue
            if intersects([b[0] - width/2, b[1] - width/2, b[2] + width/2, b[3] + width/2], tight):
                return None
        for rectangle in native_page.rects:
            if not rectangle.get('stroke'):
                continue
            width = float(rectangle.get('linewidth', 0)); b = box(rectangle)
            if width <= 0:
                return None
            points = rectangle.get('pts')
            if points and any(not (abs(x-b[0]) <= precision or abs(x-b[2]) <= precision)
                              or not (abs(y-b[1]) <= precision or abs(y-b[3]) <= precision) for x, y in points):
                return None
            edges = ([b[0]-width/2,b[1]-width/2,b[0]+width/2,b[3]+width/2],
                     [b[2]-width/2,b[1]-width/2,b[2]+width/2,b[3]+width/2],
                     [b[0]-width/2,b[1]-width/2,b[2]+width/2,b[1]+width/2],
                     [b[0]-width/2,b[3]-width/2,b[2]+width/2,b[3]+width/2])
            if any(intersects(edge, tight) for edge in edges):
                return None
        selected.append({'source_glyph_index': native_index, 'mcid': native_page.chars[native_index].get('mcid'),
                         'bbox': tight, 'pdfium_character_index': independent['pdfium_index']})
    if 'source_marked_clip_contexts' not in prepared:
        from pypdf import PdfReader
        source_page = PdfReader(prepared['source_path']).pages[prepared['page_number']-1]
        prepared['source_marked_clip_contexts'] = marked_clip_contexts(source_page.get_contents().operations, native_page.height)
    clip_proof = prove_marked_glyph_clips(prepared['source_marked_clip_contexts'], selected, tolerance=precision)
    if clip_proof is None:
        return None
    return {'contract_version': 'pdf2data.processed-cell-source-context.v1',
            'status': 'verified_source_cell_projection_context', 'source_pdf_sha256': source_sha,
            'page': native_page.page_number, 'original_cell_bbox': bounds,
            'source_cell_coordinate_and_visibility_verified': True,
            'native_source_glyph_indices': native_ids, 'independent_tight_glyphs': selected,
            'source_marked_clip_witness': clip_proof, 'source_raster_sha256': prepared['raster_sha'],
            'source_paint_snapshot_sha256': prepared['paint_snapshot_sha256'],
            'reader_versions': prepared['reader_versions'],
            'complete_page_raster_accounting': 'required_separately_by_registered_region_decoder',
            'pixel_mask_applied': False, 'source_objects_modified': False,
            'source_quality_clearance': False, 'legal_review': 'not_run', 'answer_publication_eligible': False}


def project_page_text(raw_native_page, projections, expected_selected_page):
    """Reproduce an existing selected page by exact proved SPACE deletions only.

    No prefix/suffix, punctuation, number, operator, newline or unrelated space
    is rewritten. Full-page equality is required after the disjoint edits.
    """
    policy, policy_sha = rules(); changes = []; source_sha = sha(raw_native_page)
    for projection in projections:
        if projection.get('raw_native_page_sha256') != source_sha:
            raise ValueError('Processed-cell raw page identity differs')
        if projection.get('source_row_context',{}).get('contract_version')=='pdf2data.independent-multiline-cell-context.v1':
            from .native_cell_multiline_context import mapped_space_deletions
            changes.extend(mapped_space_deletions(projection,raw_native_page));continue
        context = projection.get('raw_native_page_context')
        if context is None:
            # A table-only extraction artifact may already be absent from the
            # raw whole-page reader. Such a cell does not authorize page edits.
            if _context(projection['processed_text'], raw_native_page) is None:
                raise ValueError('Processed cell has no exact native page context')
            continue
        if context != _context(projection['raw_text'], raw_native_page):
            raise ValueError('Raw cell page context is stale or ambiguous')
        start, stop = context['codepoint_span']; quote = raw_native_page[start:stop]
        actual = separators(quote)
        for witness in projection['source_space_witnesses']:
            boundary = witness['nonwhitespace_boundary']
            if boundary not in actual:
                raise ValueError('Raw page has no proved source-cell separator')
            lo, hi, space = actual[boundary]
            if not space or any(ord(c) not in policy['raw_separator_codepoints'] for c in space):
                raise ValueError('Page projection cannot alter a newline or non-SPACE')
            changes.append({'source_codepoint_span': [start + lo, start + hi],
                            'exact_original_separator': space, 'cell_id': projection['cell_id'],
                            'processed_cell_version_id': projection['processed_cell_version_id']})
    ordered = sorted(changes, key=lambda row: row['source_codepoint_span'])
    if any(a['source_codepoint_span'][1] > b['source_codepoint_span'][0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Processed cell page-edit scopes overlap')
    view = raw_native_page
    for change in reversed(ordered):
        lo, hi = change['source_codepoint_span']; view = view[:lo] + view[hi:]
    if view != expected_selected_page:
        raise ValueError('Proved cell SPACE deletions do not reproduce complete selected page')
    return {'contract_version': 'pdf2data.source-bound-cell-page-view.v1',
            'raw_native_page_sha256': source_sha, 'selected_page_sha256': sha(view),
            'exact_source_SPACE_deletions': ordered, 'rule_configuration_sha256': policy_sha,
            'whole_selected_page_exactly_reproduced': True, 'raw_native_text_modified': False,
            'original_selected_page_modified': False, 'source_objects_modified': False,
            'legal_review': 'not_run', 'answer_publication_eligible': False}


def validate_processed_cell_version(proof, cell, source_sha, page, page_text_sha):
    """Bind a captured typed observation; the final decoder still reruns source.

    This check cannot grant page quality on its own. derive_regions reconstructs
    the complete projection and ink ownership from the actual original PDF.
    """
    if not isinstance(proof, dict):
        return False
    _, policy_sha = rules()
    report = proof.get('source_ink_ownership', {})
    expected = {'contract_version': 'pdf2data.source-bound-processed-cell.v1',
                'operation': 'misplaced_leading_source_SPACE_projection', 'status': 'source_proved',
                'source_pdf_sha256': source_sha, 'page': page, 'cell_id': cell['id'],
                'cell_bbox': cell['bbox'], 'raw_text': cell['text'] or '',
                'raw_cell_sha256': cell['selected_cell_sha256'],
                'independent_text': cell['independent_text'],
                'independent_cell_sha256': cell['independent_cell_sha256'],
                'page_text_sha256': page_text_sha, 'rule_configuration_sha256': policy_sha,
                'implementation_sha256': sha(Path(__file__).read_bytes()),
                'raw_matrix_modified': False, 'page_text_modified': False,
                'source_objects_modified': False, 'source_quality_clearance': False,
                'legal_review': 'not_run', 'answer_publication_eligible': False}
    if any(proof.get(key) != value for key, value in expected.items()):
        return False
    processed = proof.get('processed_text')
    if (not isinstance(processed, str) or compact(processed) != compact(proof['raw_text']) or
            sha(processed) != proof.get('processed_cell_sha256') or
            proof.get('processed_cell_version_id') != sha(canonical({k: v for k, v in proof.items() if k != 'processed_cell_version_id'}))):
        return False
    from .native_consensus import agrees
    if not agrees(processed, cell['independent_text'], exact_characters=True):
        return False
    return (report.get('status') == 'verified_source_cell_projection_context' and
            report.get('source_pdf_sha256') == source_sha and report.get('page') == page and
            report.get('original_cell_bbox') == cell['bbox'] and
            report.get('source_cell_coordinate_and_visibility_verified') is True and
            report.get('pixel_mask_applied') is False and report.get('source_quality_clearance') is False)
