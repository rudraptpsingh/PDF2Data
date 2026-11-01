"""Native table candidates from actual rulings, not text background rectangles."""
from __future__ import annotations
import unicodedata
import re
import hashlib
import json
from collections import Counter
from .table_headers import first_row_header_candidate


def is_ruling_object(obj: dict) -> bool:
    if obj.get('object_type')!='rect':return True
    if obj.get('stroke'):return True
    # PDF producers often emit a filled shading rectangle behind EVERY text
    # line. Its edges are not table borders. Keep thin dark filled rules only.
    color=obj.get('non_stroking_color',0)
    if isinstance(color,(int,float)):dark=color<.35
    elif isinstance(color,(tuple,list)) and len(color)==3:dark=max(color)<.35
    elif isinstance(color,(tuple,list)) and len(color)==4:dark=color[3]>.65
    else:dark=False
    return dark and min(obj.get('width',0),obj.get('height',0))<=2


def find_native_tables(page, *, white_fill_candidates=None):
    filtered=page.filter(is_ruling_object) if hasattr(page,'filter') else page
    found=list(filtered.find_tables())
    if filtered is not page:
        # Preserve a raw candidate if filtering did not explain its region.
        # This prevents a light/colored true grid from silently disappearing.
        for candidate in page.find_tables():
            x0,y0,x1,y1=candidate.bbox
            if not any(t.bbox[0]-.5<=x0 and t.bbox[1]-.5<=y0 and t.bbox[2]+.5>=x1 and t.bbox[3]+.5>=y1 for t in found):
                candidate._powerlex_detection='unresolved_raw_candidate_no_filtered_ruling_match'
                if white_fill_candidates is not None:
                    proof=white_fill_single_column_proof(page,candidate)
                    if proof is not None:white_fill_candidates.append(proof)
                found.append(candidate)
    return found


def candidate_source_diagnostic(page, found):
    """Expose the source objects behind a candidate, without approving a table."""
    box=list(found.bbox)
    def intersects(obj):
        return min(box[2],obj['x1'])+.5>=max(box[0],obj['x0']) and min(box[3],obj['bottom'])+.5>=max(box[1],obj['top'])
    def record(obj):
        return {'bbox':[float(obj[k])for k in ('x0','top','x1','bottom')],
                **{k:obj.get(k)for k in ('object_type','stroke','fill','linewidth','stroking_color','non_stroking_color')}}
    rects=[r for r in page.rects if intersects(r)]
    lines=[r for r in page.lines if intersects(r)]
    curves=[r for r in page.curves if intersects(r)]
    return {'contract_version':'pdf2data.native-table-candidate-source-diagnostic.v1',
            'detection_method':getattr(found,'_powerlex_detection','source_rulings_excluding_fill_only_text_shading'),
            'bbox':box,'intersecting_rectangles':[record(r)for r in rects],
            'fill_only_rectangles_excluded_by_ruling_filter':sum(not is_ruling_object(r)for r in rects),
            'retained_ruling_rectangles':sum(is_ruling_object(r)for r in rects),
            'intersecting_lines':[record(r)for r in lines],
            'intersecting_curves':[record(r)for r in curves],
            'source_quality_clearance':False,'semantic_table_proven':False}


def white_fill_single_column_proof(page, found):
    """Classify an invisible fill grid, retaining its complete raw evidence.

    This does not classify semantic tables. Multi-column or ambiguous candidates
    and any visible ruling/path evidence retain the ordinary table review gate.
    """
    if getattr(found,'_powerlex_detection',None)!='unresolved_raw_candidate_no_filtered_ruling_match':return None
    rows=getattr(found,'rows',[])
    if len(rows)<2 or any(len(row.cells)!=1 or row.cells[0] is None for row in rows):return None
    boxes=[list(row.cells[0])for row in rows]
    diagnostic=candidate_source_diagnostic(page,found)
    rects=diagnostic['intersecting_rectangles']
    if (len(rects)!=len(boxes) or diagnostic['retained_ruling_rectangles'] or
            diagnostic['intersecting_lines'] or diagnostic['intersecting_curves']):return None
    def white(color):
        if isinstance(color,(int,float)):return color==1
        if isinstance(color,(tuple,list)):
            return (len(color)in(1,3) and all(c==1 for c in color))or(len(color)==4 and all(c==0 for c in color))
        return False
    if any(not r['fill']or r['stroke']or not white(r['non_stroking_color'])for r in rects):return None
    matching=[];used=set();glyph_counts=[]
    for box in boxes:
        matches=[i for i,r in enumerate(rects)if max(abs(a-b)for a,b in zip(box,r['bbox']))<=.5]
        if len(matches)!=1 or matches[0]in used:return None
        used.add(matches[0]);matching.append(matches[0])
        glyphs=[g for g in page.chars if g.get('text','').strip()and
                box[0]<= (g['x0']+g['x1'])/2 <=box[2]and box[1]<=(g['top']+g['bottom'])/2<=box[3]]
        if not glyphs:return None
        # Each white rectangle must cover one physical text baseline, not a
        # multiline cell or a list whose row relationship is still ambiguous.
        if max(g['top']for g in glyphs)-min(g['top']for g in glyphs)>2:return None
        glyph_counts.append(len(glyphs))
    cells=found.extract()
    if len(cells)!=len(boxes)or any(len(row)!=1 or not isinstance(row[0],str)or not row[0].strip()for row in cells):return None
    return {'contract_version':'pdf2data.white-fill-single-column-candidate.v1',
            'bbox':[float(v)for v in found.bbox], 'raw_cells':cells,
            'source_cell_boxes':boxes,'source_rectangle_membership':matching,'nonempty_glyph_counts':glyph_counts,
            'source_object_diagnostic':diagnostic,'bbox_tolerance_points':.5,
            'disposition':'not_a_visible_source_ruling_grid_raw_observation_retained',
            'source_quality_clearance':False,'semantic_table_proven':False}


def resolve_cached_candidates(page, archived):
    """Recompute exclusions from original objects; cached labels grant no proof."""
    excluded=[]
    find_native_tables(page,white_fill_candidates=excluded)
    resolved=[]
    for table in archived:
        matches=[p for p in excluded if [round(v,2)for v in p['bbox']]==[round(v,2)for v in table['bbox']]
                 and p['raw_cells']==table['cells'] and len(p['source_cell_boxes'])==table.get('row_count')
                 and table.get('column_count')==1
                 and [[round(v,2)for v in box]for box in p['source_cell_boxes']]==[c['bbox']for c in table.get('cell_spans',[])]]
        if len(matches)==1:
            resolved.append({'archived_table_id':table['id'],
                'archived_table_sha256':hashlib.sha256(json.dumps(table,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                'source_proof':matches[0]})
    return {'contract_version':'pdf2data.source-bound-table-candidate-resolution.v1',
            'raw_candidate_count':len(archived),'layout_gate_table_count':len(archived)-len(resolved),
            'excluded_ruling_candidates':resolved,'archived_tables_modified':False,
            'source_quality_clearance':False,'semantic_table_proven':False}


def glyph_audit(text: str) -> dict:
    issues=[]
    for match in re.finditer(r'\(cid:\d+\)|/g\d{4,6}',text):
        issues.append({'char_start':match.start(),'char_length':len(match.group()),'literal':match.group(),'reason':'unresolved_font_glyph'})
    for index,char in enumerate(text):
        category=unicodedata.category(char)
        if char=='\ufffd' or category in {'Co','Cs'} or (category=='Cc' and char not in '\r\n\t'):
            issues.append({'char_start':index,'char_length':1,'literal':char,'codepoint':f'U+{ord(char):04X}',
                'reason':'replacement_or_unverified_font_mapping'})
    for match in re.finditer(r'[^\W\d_]+',text):
        scripts={next((s for s in ('LATIN','CYRILLIC','GREEK','DEVANAGARI','ARABIC') if s in unicodedata.name(c,'')),None) for c in match.group()}
        scripts.discard(None)
        if 'LATIN' in scripts and scripts.intersection({'CYRILLIC','GREEK'}):
            issues.append({'char_start':match.start(),'char_length':len(match.group()),'literal':match.group(),
                'reason':'mixed_script_word_requires_source_comparison','scripts':sorted(scripts)})
    return {'status':'repair_required' if issues else 'no_invalid_codepoints_detected',
            'issues':issues,'non_ascii_characters':sum(ord(c)>127 for c in text),
            'unicode_policy':'Preserve valid scripts/currency/mathematics; non-ASCII alone is not an error.'}


def table_shape(found, cells: list[list], table_id: str) -> dict:
    """Retain merged-cell geometry without copying values into covered blanks."""
    native_rows=getattr(found,'rows',None)
    if native_rows is None:return {}
    boxes=[box for row in native_rows for box in row.cells if box is not None]
    xs=sorted(set(round(float(x),2) for b in boxes for x in (b[0],b[2])))
    ys=sorted(set(round(float(y),2) for b in boxes for y in (b[1],b[3])))
    spans=[];seen=set()
    header_row=first_row_header_candidate(cells)
    # pdfplumber row positions may omit boundaries that occur only inside a
    # vertically merged cell. Place values by physical box coordinates.
    geometry_grid=[[None for _ in range(len(xs)-1)] for _ in range(len(ys)-1)]
    for r,row in enumerate(native_rows):
        for c,box in enumerate(row.cells):
            if box is None:continue
            b=tuple(round(float(v),2) for v in box)
            if b in seen:continue
            seen.add(b);lo=xs.index(b[0]);hi=xs.index(b[2]);top=ys.index(b[1]);bottom=ys.index(b[3])
            value=cells[r][c] if r<len(cells) and c<len(cells[r]) else None
            geometry_grid[top][lo]=value
            spans.append({'id':f'{table_id}:r{top}:c{lo}','row':top,'column':lo,
                'row_span':bottom-top,'column_span':hi-lo,'bbox':list(b),
                'text':value,'role_candidate':('header' if header_row==0 else 'unresolved') if top==0 else 'data',
                'status':'source_cell_candidate','glyph_audit':glyph_audit(value or '')})
    if Counter(str(v) for row in cells for v in row if v not in (None,''))!=Counter(str(c['text']) for c in spans if c['text'] not in (None,'')):
        raise ValueError('native geometry cannot map every populated extracted cell')
    grid_hash=hashlib.sha256(json.dumps({'cells':cells,'geometry_grid':geometry_grid,'mapping_version':2,'cell_geometry':sorted(seen)},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    table_version=f'{table_id}:v{grid_hash[:24]}'
    covered=set()
    for cell in spans:
        cell['id']=f"{table_version}:r{cell['row']}:c{cell['column']}"
        for r in range(cell['row'],cell['row']+cell['row_span']):
            for c in range(cell['column'],cell['column']+cell['column_span']):
                if (r,c) in covered:raise ValueError('overlapping native table cell spans')
                covered.add((r,c))
    unresolved=[{'row':r,'column':c} for r in range(len(ys)-1) for c in range(len(xs)-1) if (r,c) not in covered]
    return {'cells':geometry_grid,'extracted_cells':cells,
        'grid_mapping':'source_box_coordinates; original extractor rows retained in extracted_cells',
        'table_version_id':table_version,'table_geometry_sha256':grid_hash,'unresolved_grid_slots':unresolved,
        'row_count':len(ys)-1,'column_count':len(xs)-1,'cell_spans':spans,
        'structure_status':'source_geometry_candidate_requires_cell_review',
        'detection_method':getattr(found,'_powerlex_detection','source_rulings_excluding_fill_only_text_shading'),
        'header_associations':'candidate_only','blank_cell_policy':'never_inherit_or_fill'}
