"""Source-local table evidence and explicit, unapproved context associations.

This sidecar references physical cells; it never fills blanks, joins grids,
translates labels or makes a legal/financial metric authoritative.
"""
from hashlib import sha256
import json
import re

from .table_headers import first_row_header_candidate

SCHEMA = 'pdf2data.table-context.v1'
UNIT = re.compile(r'\b(?:INR|Rs\.?|rupees|lakh|lakhs|crore|crores|MW|MWh|kW|kWh|paise|percent)\b|₹|%|रुपये|लाख|करोड़', re.I)
PERIOD = re.compile(r'\b(?:FY|financial year|as[ -]of|week(?:ly| ending)?|month|dated|w\.e\.f\.)\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{4}[-–]\d{2,4}\b|वित्तीय|दिनांक|माह', re.I)
NOTE = re.compile(r'^\s*(?:note\b|footnote\b|[*†‡]|टिप्पणी)', re.I)


def source_lines(layout,page):
    if layout.get('lines'):return layout['lines']
    from .document_blocks import glyph_lines,compact,union
    selected={}
    for line in page['text'].split('\n'):
        selected.setdefault(compact(line),[]).append(line)
    result=[]
    for group in glyph_lines(layout):
        raw=''.join(g['text']for g in group);matches=selected.get(compact(raw),[])
        # Keep the selected line's exact whitespace only when it corresponds
        # uniquely to this physical glyph line. Otherwise retain raw glyph text.
        if len(matches)==1:raw=matches[0]
        b=union([g['bbox']for g in group])
        result.append(dict(text=raw,x0=b[0],top=b[1],x1=b[2],bottom=b[3]))
    return result


def text_evidence(raw, page_text):
    """Cell-local text survives even when selected page text is ambiguous."""
    if raw is None:
        return dict(raw_text=None, raw_text_sha256=None, cell_text_span=None,
                    selected_page_evidence=dict(status='blank_source_cell', spans=[]))
    text = str(raw)
    positions = [m.start() for m in re.finditer(re.escape(text), page_text)] if text else []
    exact = dict(char_start=0, char_length=len(text), exact_quote=text,
                 offset_encoding='unicode_codepoint')
    page = dict(status='exact_unique' if len(positions)==1 else
                'ambiguous_multiple_occurrences' if positions else 'not_in_selected_text', spans=[])
    if len(positions)==1:
        page['spans'] = [{**exact, 'char_start':positions[0]}]
    return dict(raw_text=raw, raw_text_sha256=sha256(text.encode('utf-8')).hexdigest(),
                cell_text_span=exact, selected_page_evidence=page)


def iter_table_contexts(data):
    pages = {p['page']:p for p in data['pages']}
    layouts = {p['page']:p for p in data.get('layout', [])}
    tables = {t['id']:t for t in data.get('tables', [])}
    page_tables = {}
    for table in tables.values():page_tables.setdefault(table['page'],[]).append(table)
    links = data.get('document_model', {}).get('continuations', {}).get('links', [])
    inherited = {r['to_id']:r for r in links if r['kind']=='table_continuation_candidate'}
    header_cache = {}
    line_cache = {}
    for ordinal, table in enumerate(data.get('tables', [])):
        page = pages[table['page']]
        physical = table.get('cell_spans', [])
        cells = []
        for index, cell in enumerate(physical):
            cells.append(dict(id=cell.get('id') or f'{table["id"]}:cell:{index}',
                              row=cell['row'], column=cell['column'],
                              row_span=cell.get('row_span',1), column_span=cell.get('column_span',1),
                              bbox=cell.get('bbox'), role_candidate=cell.get('role_candidate','unresolved'),
                              source_pointer=f'/tables/{ordinal}/cell_spans/{index}',
                              **text_evidence(cell.get('text'),page['text'])))
        if not physical:
            # A reader can supply only a grid. Preserve every slot, but never
            # pretend it has cell-local source geometry.
            for row, values in enumerate(table.get('cells', [])):
                for column, value in enumerate(values):
                    cells.append(dict(id=f'{table["id"]}:r{row}:c{column}',row=row,column=column,
                                      row_span=1,column_span=1,bbox=None,role_candidate='unresolved',
                                      source_pointer=f'/tables/{ordinal}/cells/{row}/{column}',
                                      **text_evidence(value,page['text'])))
        origin = table
        continuation = inherited.get(table['id'])
        if continuation:
            origin = tables[continuation['header_context']['table_id']]
        header_row = first_row_header_candidate(origin.get('cells', []))
        headers = [c for c in cells if c['role_candidate']=='header' or header_row==0 and c['row']==0] if origin is table else header_cache.get(origin['id'], [])
        header_cache[table['id']] = [{k:h[k] for k in ('id','row','column','row_span','column_span')}for h in headers]
        by_row = {}
        for cell in cells:by_row.setdefault(cell['row'],[]).append(cell)
        for cell in cells:
            cell['column_header_candidates'] = [dict(table_id=origin['id'],cell_id=h['id'],page=origin['page'])
                for h in headers if (origin is not table or h['row']+h['row_span']<=cell['row'])
                and h['column']<=cell['column']<h['column']+h['column_span']]
            cell['left_row_context_cell_ids'] = [c['id'] for c in by_row[cell['row']]
                if c['column']+c['column_span']<=cell['column'] and c['raw_text'] not in (None,'')]
        candidates=[]
        for cell in cells:
            raw=str(cell['raw_text'] or '')
            roles=[name for name,pattern in [('unit',UNIT),('period',PERIOD),('footnote',NOTE)] if pattern.search(raw)]
            if roles:candidates.append(dict(location='source_cell',cell_id=cell['id'],roles=roles,
                                            association='scope_not_inferred'))
        box=table.get('bbox')
        if box:
            others=[t['bbox'] for t in page_tables[table['page']] if t['id']!=table['id'] and t.get('bbox')]
            if table['page'] not in line_cache:
                line_cache.clear()
                line_cache[table['page']]=source_lines(layouts.get(table['page'],{}),page)
            for line in line_cache[table['page']]:
                b=[line[k] for k in ('x0','top','x1','bottom')]
                above=0<=box[1]-b[3]<=72;below=0<=b[1]-box[3]<=72
                if not (above or below) or b[2]<=box[0] or b[0]>=box[2]:continue
                if any(o[0]<b[2] and o[2]>b[0] and o[1]<b[3] and o[3]>b[1] for o in others):continue
                raw=line['text'];roles=[name for name,pattern in [('unit',UNIT),('period',PERIOD),('footnote',NOTE)] if pattern.search(raw)]
                if roles:candidates.append(dict(location='nearby_source_line',page=table['page'],bbox=b,
                    relation='above_table' if above else 'below_table',roles=roles,
                    association='proximity_only_scope_not_inferred',**text_evidence(raw,page['text'])))
        warnings=['header_row_and_context_associations_are_candidates_not_semantic_clearance']
        if not headers:warnings.append('column_headers_unresolved')
        if not physical:warnings.append('cell_geometry_unavailable')
        if any(c['selected_page_evidence']['status'] in ('ambiguous_multiple_occurrences','not_in_selected_text') for c in cells):
            warnings.append('some_cells_have_no_unique_selected_page_quote_use_source_cell_and_image')
        if table.get('unresolved_grid_slots'):warnings.append('unresolved_grid_slots')
        yield dict(schema_version=SCHEMA,source_pdf_sha256=data['source_pdf_sha256'],physical_page=table['page'],
                   page_text_sha256=page['text_sha256'],table_id=table['id'],
                   table_version_id=table.get('table_version_id'),table_bbox=box,
                   source_artifact='data.json',
                   source_table_pointer=f'/tables/{ordinal}',header_source_table_id=origin['id'],
                   continuation_candidate_id=continuation['id'] if continuation else None,
                   cells=cells,context_candidates=candidates,quality_warnings=warnings,
                   blank_cell_policy='never_inherit_or_fill',semantic_review_required=True,
                   source_content_verified=False,publication_eligible=False)


def write_table_contexts(path, data):
    count=0
    with path.open('w',encoding='utf-8',newline='\n') as stream:
        for record in iter_table_contexts(data):
            stream.write(json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
            count+=1
    return count
