"""Native table candidates from actual rulings, not text background rectangles."""
from __future__ import annotations
import unicodedata
import re
import hashlib
import json
from collections import Counter


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


def find_native_tables(page):
    filtered=page.filter(is_ruling_object) if hasattr(page,'filter') else page
    found=list(filtered.find_tables())
    if filtered is not page:
        # Preserve a raw candidate if filtering did not explain its region.
        # This prevents a light/colored true grid from silently disappearing.
        for candidate in page.find_tables():
            x0,y0,x1,y1=candidate.bbox
            if not any(t.bbox[0]-.5<=x0 and t.bbox[1]-.5<=y0 and t.bbox[2]+.5>=x1 and t.bbox[3]+.5>=y1 for t in found):
                candidate._powerlex_detection='unresolved_raw_candidate_no_filtered_ruling_match'
                found.append(candidate)
    return found


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
                'text':value,'role_candidate':'header' if top==0 else 'data',
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
