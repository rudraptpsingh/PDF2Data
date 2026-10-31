"""Source-operator bounded multiline cells with exact noncontiguous page spans.

Adjacent table columns remain in the original page. This witness never erases
them or treats a font advance as an ink bounding box. Whole operator pieces,
same-grid row anchors, exact literals and final page equality are required.
"""
import json
import math
from pathlib import Path
import re

from .native_cell_row_context import RowContextUnavailable, canonical, sha, capture_operator_page

CONTRACT='pdf2data.independent-multiline-cell-context.v1'


def rules():
    path=Path(__file__).with_name('native_cell_multiline_context_rules.json');value=json.loads(path.read_text())
    if value.get('contract_version')!='pdf2data.independent-multiline-cell-context-rules.v1':
        raise RowContextUnavailable('unknown_multiline_context_policy')
    return value,sha(path.read_bytes())


def operator_lines(capture,bounds,policy):
    """Consume complete actual text-show pieces, never a guessed clipped part."""
    x0,y0,x1,y1=bounds;tol=policy['baseline_tolerance_points'];lines=[];foreign_advances=[]
    for r in capture['text_show_operators']:
        x,y=r['origin_pdf'];end=r['advance_end_pdf_x'];top=capture['height']-y
        if any(not isinstance(v,(int,float))or not math.isfinite(v)for v in [x,y,end,r['font_size'],r['text_rise']]):
            raise RowContextUnavailable('nonfinite_operator_position')
        if not y0<top<y1:continue
        if not x0<=x<x1:
            if r['text'].strip()and x<x0<end:foreign_advances.append(r)
            continue
        if r['text'].strip()and(r['font_interpretable']is not True or r['rotated']is not False or r['flip_vertical']is not False or r['text_rise']!=0):
            raise RowContextUnavailable('operator_font_script_or_text_rise_unproved')
        line=next((a for a in lines if abs(a['baseline_top_points']-top)<=tol),None)
        if line is None:line={'baseline_top_points':top,'operators':[]};lines.append(line)
        line['operators'].append(r)
    lines.sort(key=lambda a:a['baseline_top_points']);result=[];sizes=[]
    for line in lines:
        pieces=sorted(line['operators'],key=lambda r:(r['operator_index'],r['piece_index']))
        text=''.join(r['text']for r in pieces).strip()
        if not text:continue
        if any(ord(c)>127 for c in text):raise RowContextUnavailable('multiline_script_requires_separate_proof')
        sizes.extend(r['font_size']for r in pieces if r['text'].strip())
        result.append({**line,'operators':pieces,'text':text,
            'advance_overhangs_preserved':[{'operator_index':r['operator_index'],'origin_pdf':r['origin_pdf'],
                'advance_end_pdf_x':r['advance_end_pdf_x']}for r in pieces if r['advance_end_pdf_x']>x1],
            'foreign_origin_advance_intersections_preserved':[r for r in foreign_advances if abs(capture['height']-r['origin_pdf'][1]-line['baseline_top_points'])<=tol],
            'advance_is_not_ink_bbox':True,'glyph_and_ink_ownership_approved':False})
    if not result or sizes and max(sizes)-min(sizes)>tol:
        raise RowContextUnavailable('multiline_symbol_or_fontsize_layout_not_plain_text')
    return result


def _row_segment(text,anchor,next_anchor,expected,suffixes):
    def starts(label):return list(re.finditer(r'(?m)^[^\S\r\n]*'+re.escape(label)+r'[^\S\r\n]+',text))
    first,after=starts(anchor),starts(next_anchor)
    candidates=[]
    for start in first:
        following=[v for v in after if v.start()>start.start()]
        if not following:continue
        # The nearest following independently proved next-row label bounds
        # this candidate. A document heading with the same number is retained
        # and cannot qualify unless its complete exact field lines also match.
        segment=(start.start(),following[0].start())
        try:_match_lines(text,expected,segment,suffixes)
        except RowContextUnavailable:continue
        candidates.append(segment)
    if len(candidates)!=1:
        raise RowContextUnavailable('primary_row_boundary_missing_or_ambiguous')
    return candidates[0]


def _match_lines(text,expected,segment,suffixes):
    start,stop=segment;line_records=[];offset=start
    for line in text[start:stop].splitlines(keepends=True):
        body=line.rstrip('\r\n');line_records.append((offset,body));offset+=len(line)
    choices=[]
    for index,value in enumerate(expected):
        tokens=re.findall(r'\S+',value)
        if not tokens:raise RowContextUnavailable('empty_multiline_cell_line')
        pattern=r'(?<!\S)'+r'[^\S\r\n]+'.join(re.escape(t)for t in tokens)
        matches=[]
        for line_index,(base,body)in enumerate(line_records):
            if index==0 and line_index!=0:continue
            for m in re.finditer(pattern,body):
                tail=body[m.end():]
                if tail and not tail[0].isspace()and not any(tail.startswith(s)for s in suffixes[index]):continue
                matches.append({'line_index':line_index,'codepoint_span':[base+m.start(),base+m.end()],
                    'exact_quote':m.group(),'exact_quote_sha256':sha(m.group())})
        choices.append(matches)
    paths=[[]]
    for matches in choices:
        next_paths=[]
        for path in paths:
            for candidate in matches:
                if not path or candidate['line_index']>path[-1]['line_index']:
                    next_paths.append([*path,candidate])
                    if len(next_paths)>128:raise RowContextUnavailable('multiline_context_path_ambiguity')
        paths=next_paths
    if len(paths)!=1:raise RowContextUnavailable('multiline_line_spans_missing_or_ambiguous')
    return paths[0]


def _column_boundary_joins(capture,cell,peers,lines,foreign,raw_spans,selected_spans,raw_page,selected,policy):
    """Serialization-only joins between proved distinct table cells, not words."""
    if not policy['allow_bound_intercell_delimiter_space_joins']:return []
    joins=[];tol=policy['baseline_tolerance_points'];space_pattern='[ '+chr(160)+']*'
    def delimiter(page,span,text):
        end=span['codepoint_span'][1];m=re.match('('+space_pattern+')'+re.escape(text),page[end:])
        return None if m is None else (end,end+len(m[1]),end+m.end())
    def append_gap(raw_start,raw_end,selected_start,selected_end,left,right,operator_evidence):
        if raw_end>raw_start and selected_end==selected_start:
            exact=raw_page[raw_start:raw_end]
            if any(ord(c)not in policy['boundary_whitespace_codepoints']for c in exact):return
            joins.append({'source_codepoint_span':[raw_start,raw_end],'exact_original_separator':exact,
                'exact_original_separator_sha256':sha(exact),'selected_zero_gap_position':selected_start,
                'left_cell_id':left,'right_cell_id':right,'source_operator_evidence':operator_evidence,
                'basis':'same_source_row_distinct_table_cell_delimiter_serialization_only',
                'source_objects_modified':False,'source_quality_clearance':False})
    for index,line in enumerate(lines):
        delimiters=[f for f in foreign if abs(f['line']['baseline_top_points']-line['baseline_top_points'])<=tol]
        if len(delimiters)!=1:continue
        d=delimiters[0];literal=d['line']['text'].strip();raw=delimiter(raw_page,raw_spans[index],literal);view=delimiter(selected,selected_spans[index],literal)
        if raw is None or view is None:continue
        append_gap(raw[0],raw[1],view[0],view[1],cell['id'],d['cell_id'],d)
        delimiter_peer=next((p for p in peers if p['id']==d['cell_id']),None)
        if delimiter_peer is None:continue
        right=[p for p in peers if p['row']==cell['row']and p['column']==delimiter_peer['column']+delimiter_peer['column_span']and
               p['id'].rsplit(':r',1)[0]==cell['id'].rsplit(':r',1)[0]and p.get('text','').strip()]
        if len(right)!=1:continue
        try:right_lines=operator_lines(capture,right[0]['bbox'],policy)
        except RowContextUnavailable:continue
        first=right_lines[0]
        if abs(first['baseline_top_points']-line['baseline_top_points'])>tol:continue
        if first['text'].split()!=right[0]['text'].splitlines()[0].split():continue
        pattern='('+space_pattern+')('+r'[^\S\r\n]+'.join(re.escape(t)for t in first['text'].split())+')'
        a=re.match(pattern,raw_page[raw[2]:].split('\n',1)[0]);b=re.match(pattern,selected[view[2]:].split('\n',1)[0])
        if a is None or b is None:continue
        append_gap(raw[2],raw[2]+len(a[1]),view[2],view[2]+len(b[1]),d['cell_id'],right[0]['id'],
            {'left_source_cell':d,'right_source_cell':{'cell_id':right[0]['id'],'bbox':right[0]['bbox'],'line':first}})
    return joins


def prove_multiline_context(capture,cell,anchor,next_anchor,foreign_cells,processed,selected_page,raw_native_page,third_layout):
    policy,policy_sha=rules();raw_lines=cell['text'].splitlines();processed_lines=processed.splitlines()
    if (not 2<=len(raw_lines)<=policy['maximum_lines']or len(processed_lines)!=len(raw_lines)or
            any(not v.strip()for v in raw_lines)or len(cell['text'])>policy['maximum_cell_codepoints']):
        raise RowContextUnavailable('outside_multiline_plain_cell_scope')
    if capture.get('contract_version')!='pdf2data.pypdf-source-operator-page.v1'or capture.get('rotation')!=0:
        raise RowContextUnavailable('multiline_operator_frame_unverified')
    prefix=cell['id'].rsplit(':r',1)[0]
    if (cell['row_span']!=1 or cell['column']!=1 or cell['column_span']!=1 or anchor['row']!=cell['row']or
            not cell['row']<next_anchor['row']<=cell['row']+policy['maximum_anchor_gap_rows']or any(a['column']!=0 or a['row_span']!=1 or
            a['id'].rsplit(':r',1)[0]!=prefix for a in (anchor,next_anchor))):
        raise RowContextUnavailable('multiline_anchor_outside_same_grid_row')
    intervening=[]
    for row in range(cell['row']+1,next_anchor['row']):
        matches=[c for c in foreign_cells if c['id'].rsplit(':r',1)[0]==prefix and c['row']==row and c['column']==0]
        if len(matches)!=1 or matches[0].get('text','').strip()or matches[0]['row_span']!=1:
            raise RowContextUnavailable('intervening_row_anchor_not_exactly_empty')
        # Freeze the source observation: preview later annotates these mutable
        # same-page cells with independent reads and must not mutate a witness.
        intervening.append({k:json.loads(json.dumps(matches[0][k]))for k in
            ('id','row','column','row_span','column_span','bbox','text')})
    if any(not re.fullmatch(policy['row_anchor_pattern'],a['text'].strip())for a in(anchor,next_anchor)):
        raise RowContextUnavailable('multiline_row_anchor_literal_not_supported')
    tol=policy['baseline_tolerance_points'];bounds=cell['bbox']
    if (abs(anchor['bbox'][1]-bounds[1])>tol or abs(anchor['bbox'][3]-bounds[3])>tol or
            anchor['bbox'][2]>bounds[0]or next_anchor['bbox'][1]<bounds[3]-tol):
        raise RowContextUnavailable('multiline_row_anchor_geometry_differs')
    lines=operator_lines(capture,bounds,policy);a=operator_lines(capture,anchor['bbox'],policy);b=operator_lines(capture,next_anchor['bbox'],policy)
    if (len(a)!=1 or len(b)!=1 or a[0]['text'].strip()!=anchor['text'].strip()or b[0]['text'].strip()!=next_anchor['text'].strip()or
            abs(a[0]['baseline_top_points']-lines[0]['baseline_top_points'])>tol or len(lines)!=len(processed_lines)):
        raise RowContextUnavailable('multiline_independent_row_operator_anchor_differs')
    if any(l['text'].split()!=v.split()for l,v in zip(lines,processed_lines)):
        raise RowContextUnavailable('multiline_operator_literal_or_order_differs')
    suffixes=[[]for _ in lines];foreign=[]
    for peer in foreign_cells:
        if peer['row']!=cell['row']or peer['id'].rsplit(':r',1)[0]!=prefix or peer['column']<=cell['column']:continue
        peer_lines=[v.strip()for v in peer.get('text','').splitlines()if v.strip()]
        if not peer_lines or any(v not in policy['adjacent_foreign_delimiters']for v in peer_lines):continue
        for foreign_line in operator_lines(capture,peer['bbox'],policy):
            text=foreign_line['text'].strip()
            if text not in policy['adjacent_foreign_delimiters']:continue
            for index,line in enumerate(lines):
                if abs(line['baseline_top_points']-foreign_line['baseline_top_points'])<=tol:
                    suffixes[index].append(text);foreign.append({'cell_id':peer['id'],'bbox':peer['bbox'],'line':foreign_line})
    selected_segment=_row_segment(selected_page,anchor['text'].strip(),next_anchor['text'].strip(),processed_lines,suffixes)
    raw_segment=_row_segment(raw_native_page,anchor['text'].strip(),next_anchor['text'].strip(),raw_lines,suffixes)
    selected_spans=_match_lines(selected_page,processed_lines,selected_segment,suffixes)
    raw_spans=_match_lines(raw_native_page,raw_lines,raw_segment,suffixes)
    offset=0
    for record,line in zip(raw_spans,cell['text'].splitlines(keepends=True)):
        record['raw_cell_codepoint_span']=[offset,offset+len(line.rstrip('\r\n'))];offset+=len(line)
    boundary_joins=_column_boundary_joins(capture,cell,foreign_cells,lines,foreign,raw_spans,selected_spans,
        raw_native_page,selected_page,policy)
    witness={'contract_version':CONTRACT,'status':'source_multiline_cell_context_verified',
        'source_pdf_sha256':capture['source_pdf_sha256'],'page':capture['page'],'cell_id':cell['id'],'cell_bbox':bounds,
        'table_geometry_sha256':cell['table_geometry_sha256'],'raw_cell_sha256':sha(cell['text']),
        'processed_tokens':processed.split(),'selected_page_sha256':sha(selected_page),'raw_native_page_sha256':sha(raw_native_page),
        'third_layout_sha256':sha(third_layout),'anchor_cell_id':anchor['id'],'anchor_literal':anchor['text'].strip(),
        'next_anchor_cell_id':next_anchor['id'],'next_anchor_literal':next_anchor['text'].strip(),
        'intervening_empty_anchor_cells_preserved':intervening,
        'independent_row_anchor':a[0],'independent_next_row_anchor':b[0],
        'independent_cell':{'text':'\n'.join(l['text']for l in lines),'source_operator_lines':lines,
            'no_contiguous_whole_page_quote_claim':True},'independent_foreign_delimiters':foreign,
        'selected_page_context':{'kind':'ordered_noncontiguous_exact_cell_line_spans','line_spans':selected_spans,
            'assembled_cell_text':'\n'.join(s['exact_quote']for s in selected_spans),'full_text_sha256':sha(selected_page),
            'no_contiguous_whole_cell_page_span_claim':True},'raw_page_line_spans':raw_spans,
        'source_column_boundary_space_joins':boundary_joins,
        'operator_capture_sha256':sha(canonical(capture)),'content_stream_sha256':capture['content_stream_sha256'],
        'reader':capture['reader'],'reader_version':capture['reader_version'],'reader_implementation_sha256':capture['reader_implementation_sha256'],
        'source_clip_operator_indices_preserved':capture['clip_operator_indices'],'rule_configuration_sha256':policy_sha,
        'implementation_sha256':sha(Path(__file__).read_bytes()),'ink_bbox_inferred':False,'clip_or_glyph_ownership_approved':False,
        'raw_source_or_selected_text_modified':False,'source_quality_clearance':False,'legal_review':'not_run','answer_publication_eligible':False}
    witness['witness_sha256']=sha(canonical(witness));return witness


def capture_source_multiline_context(native_page,cell,processed,selected_page,raw_native_page,third_layout,source_sha):
    from .native_tight_cell_ownership import _PAGE_SOURCE_CELLS
    context=_PAGE_SOURCE_CELLS.get()
    if context is None or context['native_page']is not native_page or context['source_sha256']!=source_sha:
        raise RowContextUnavailable('trusted_multiline_source_grid_context_missing')
    prefix=cell['id'].rsplit(':r',1)[0];anchors=[];anchor_reads=[]
    from .native_table_cell_spacing import read_source_box
    later=[c for c in context['cells']if c['id'].rsplit(':r',1)[0]==prefix and c['column']==0 and
        cell['row']<c['row']<=cell['row']+rules()[0]['maximum_anchor_gap_rows']and(c.get('text')or'').strip()]
    if not later:raise RowContextUnavailable('unique_multiline_row_boundary_missing')
    next_row=min(c['row']for c in later)
    for row in [cell['row'],next_row]:
        matches=[c for c in context['cells']if c['id'].rsplit(':r',1)[0]==prefix and c['row']==row and c['column']==0]
        if len(matches)!=1:raise RowContextUnavailable('unique_multiline_row_boundary_missing')
        anchor=matches[0];x0,y0,x1,y1=anchor['bbox']
        reading,rectangle=read_source_box(native_page,context['pdfium_page'],context['text_page'],[x0+.2,y0+.2,x1-.2,y1-.2])
        if reading.split()!=anchor['text'].split():raise RowContextUnavailable('fresh_independent_row_boundary_literal_differs')
        anchors.append(anchor);anchor_reads.append({'cell_id':anchor['id'],'raw_independent_text':reading,
            'source_rectangle':rectangle,'source_quality_clearance':False})
    if 'independent_operator_capture'not in context:
        context['independent_operator_capture']=capture_operator_page(context['source'],context['page'],source_sha)
    result=prove_multiline_context(context['independent_operator_capture'],cell,*anchors,context['cells'],processed,
        selected_page,raw_native_page,third_layout)
    result['independent_anchor_source_rectangle_readings']=anchor_reads
    result['witness_sha256']=sha(canonical({k:v for k,v in result.items()if k!='witness_sha256'}))
    return result


def validate_multiline_context(w,cell,processed,selected,raw_page,third,source_sha,page):
    if not isinstance(w,dict):return False
    _,policy_sha=rules()
    expected={'contract_version':CONTRACT,'status':'source_multiline_cell_context_verified','source_pdf_sha256':source_sha,
        'page':page,'cell_id':cell['id'],'cell_bbox':cell['bbox'],'table_geometry_sha256':cell['table_geometry_sha256'],
        'raw_cell_sha256':sha(cell['text']),'processed_tokens':processed.split(),'selected_page_sha256':sha(selected),
        'raw_native_page_sha256':sha(raw_page),'third_layout_sha256':sha(third),'rule_configuration_sha256':policy_sha,
        'implementation_sha256':sha(Path(__file__).read_bytes()),'ink_bbox_inferred':False,'clip_or_glyph_ownership_approved':False,
        'raw_source_or_selected_text_modified':False,'source_quality_clearance':False,'legal_review':'not_run','answer_publication_eligible':False}
    return all(w.get(k)==v for k,v in expected.items())and w.get('witness_sha256')==sha(canonical({k:v for k,v in w.items()if k!='witness_sha256'}))


def mapped_space_deletions(projection,raw_page):
    """Map only existing proved literal SPACE edits through exact line spans."""
    from .native_cell_space_projection import separators,rules as space_rules,compact
    w=projection['source_row_context'];policy,_=space_rules()
    if w.get('contract_version')!=CONTRACT or w['raw_native_page_sha256']!=sha(raw_page):
        raise ValueError('Multiline page-edit source identity differs')
    if w.get('witness_sha256')!=sha(canonical({k:v for k,v in w.items()if k!='witness_sha256'})):
        raise ValueError('Multiline context witness changed')
    result=[]
    for source_witness in projection['source_space_witnesses']:
        lo,hi=source_witness['raw_separator_span'];matches=[r for r in w['raw_page_line_spans']if
            r['raw_cell_codepoint_span'][0]<=lo<hi<=r['raw_cell_codepoint_span'][1]]
        if len(matches)!=1:raise ValueError('Proved SPACE does not have one complete source-cell line')
        line=matches[0];start,stop=line['codepoint_span'];quote=raw_page[start:stop]
        if sha(quote)!=line['exact_quote_sha256']or quote!=line['exact_quote']:
            raise ValueError('Multiline raw page span changed')
        before=line['raw_cell_codepoint_span'][0];boundary=source_witness['nonwhitespace_boundary']-len(compact(projection['raw_text'][:before]))
        actual=separators(quote)
        if boundary not in actual:raise ValueError('Source line has no proved SPACE separator')
        a,b,space=actual[boundary]
        if not space or any(ord(c)not in policy['raw_separator_codepoints']for c in space):
            raise ValueError('Multiline page edit cannot change newline or content')
        result.append({'source_codepoint_span':[start+a,start+b],'exact_original_separator':space,
            'cell_id':projection['cell_id'],'processed_cell_version_id':projection['processed_cell_version_id']})
    for boundary in w.get('source_column_boundary_space_joins',[]):
        a,b=boundary['source_codepoint_span'];exact=raw_page[a:b]
        if (not exact or exact!=boundary['exact_original_separator']or sha(exact)!=boundary['exact_original_separator_sha256']or
                any(ord(c)not in policy['raw_separator_codepoints']for c in exact)):
            raise ValueError('Intercell boundary edit cannot change source content')
        result.append({'source_codepoint_span':[a,b],'exact_original_separator':exact,
            'cell_id':projection['cell_id'],'processed_cell_version_id':projection['processed_cell_version_id'],
            'basis':'source_bound_distinct_cell_delimiter_space_join','left_cell_id':boundary['left_cell_id'],
            'right_cell_id':boundary['right_cell_id']})
    return result
