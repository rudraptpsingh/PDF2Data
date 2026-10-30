"""Independently prove two actual header metadata blocks; never waive a gap."""
import hashlib,json,re
from pathlib import Path

KIND='source_bound_header_reference_date_blocks.v2'
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def compact(value):return ''.join(c for c in value if not c.isspace())
def bbox(chars):return[min(c['x0']for c in chars),min(c['top']for c in chars),max(c['x1']for c in chars),max(c['bottom']for c in chars)]
def load_rules():
    path=Path(__file__).with_suffix('.json');rules=json.loads(path.read_text())
    if rules.get('schema_version')!='pdf2data.native-header-metadata-rules.v1':raise ValueError('Unknown metadata rule schema')
    return rules,sha(path.read_bytes())

def source_blocks(line,native_page,physical_page,table_boxes,rule,*,prior_header_tables=()):
    """Qualify exactly two source-glyph partitions in a bounded first-page header."""
    if physical_page not in rule['physical_pages']or line['bottom']>native_page.height*rule['max_bottom_fraction']or line['bottom']-line['top']>rule['max_line_height_points']:raise ValueError('Metadata row outside bounded first-page header')
    if not table_boxes and not rule.get('allow_verified_zero_table_page',False):raise ValueError('Metadata row requires source table registry')
    allowed=[tuple(t['bbox'])for t in prior_header_tables]
    for box in table_boxes:
        if line['bottom']<box[1]:continue
        if tuple(box)not in allowed or box[3]>=line['top']:
            raise ValueError('Metadata row overlaps or follows an unproved/non-header source table')
    glyphs=line['chars'];ordered=sorted((c for c in glyphs if c['text'].strip()),key=lambda c:c['x0'])
    splits=[(a['x1']+b['x0'])/2 for a,b in zip(ordered,ordered[1:])if b['x0']-a['x1']>max(rule['minimum_gap_points'],rule['minimum_gap_font_multiple']*max(a.get('size',0),b.get('size',0)))]
    if len(splits)!=rule['required_blocks']-1:raise ValueError('Metadata requires exactly two bounded source blocks')
    split=splits[0];groups=[[],[]]
    for index,char in enumerate(glyphs):
        if char['x0']<split<char['x1']:raise ValueError('Source glyph crosses metadata block boundary')
        group=0 if(char['x0']+char['x1'])/2<split else 1
        groups[group].append((index,char))
    from pdfplumber.utils import extract_text
    texts=[extract_text([c for _,c in group],layout=False)for group in groups]
    if not re.fullmatch(rule['reference_pattern'],compact(texts[0]),re.I)or not re.search(rule['reference_required_pattern'],texts[0]):raise ValueError('Left header block is not configured reference-number syntax')
    if not re.search(rule['reference_issuer_pattern'],compact(texts[0]),re.I):raise ValueError('Reference identifier outside configured issuer syntax')
    words=set(re.findall(r'[A-Za-z]+',texts[0].lower()))
    if words.intersection(rule['reference_excluded_words']):raise ValueError('Reference metadata contains operative/prose content')
    if not any(re.fullmatch(pattern,compact(texts[1]),re.I)for pattern in rule['date_patterns']):raise ValueError('Right header block is not configured date-field syntax')
    if compact(''.join(texts))!=compact(line['text']):raise ValueError('Metadata blocks lose, duplicate or reorder original selected line')
    return groups,texts

def prove_prior_contact_headers(line,native_page,physical_page,table_boxes,header_tables,textpage,independent_layout,pdfium_page,rule):
    """Only complete, nonoverlapping native contact-header grids may precede No./Date.

    Caller still validates every table's complete cell proof and page ownership.
    This additional predicate independently rereads the prior header's original
    cells. It does not discard the grid or turn addresses into legal fields.
    """
    prior=[box for box in table_boxes if box[1]<=line['bottom']]
    if not prior:return []
    config=rule.get('prior_contact_header')
    if not config or pdfium_page is None:raise ValueError('Prior header table needs explicit fresh source frame')
    if physical_page not in rule['physical_pages']:raise ValueError('Prior contact header outside configured first page')
    from .native_tables import find_native_tables,table_shape,glyph_audit
    from .native_table_cell_spacing import read_source_box,map_cell_reader
    from .native_consensus import agrees
    found=find_native_tables(native_page);proofs=[]
    for box in prior:
        if (box[3]>=line['top'] or box[1]<0 or box[3]>native_page.height*config['max_bottom_fraction']
            or box[3]-box[1]>native_page.height*config['max_height_fraction']
            or box[2]-box[0]<native_page.width*config['minimum_width_fraction']):
            raise ValueError('Prior source table overlaps metadata or exceeds contact-header band')
        candidates=[t for t in(header_tables or[])if t['bbox']==box]
        actual=[t for t in found if list(t.bbox)==box]
        if len(candidates)!=1 or len(actual)!=1:raise ValueError('Prior header geometry missing, stale or ambiguous')
        table=candidates[0];cells=table['cell_spans'];fresh=actual[0].extract()
        shape=table_shape(actual[0],fresh,table['id'])
        if (table.get('page')!=physical_page or table.get('source_unchanged')is not True
            or table['column_count']!=1 or not config['minimum_rows']<=table['row_count']<=config['maximum_rows']
            or table.get('unresolved_grid_slots') or shape['table_geometry_sha256']!=table['table_geometry_sha256']
            or [(c['row'],c['column'],c['bbox'],c['text'])for c in cells]!=[(c['row'],c['column'],c['bbox'],c['text'])for c in shape['cell_spans']]):
            raise ValueError('Prior source contact grid shape/text differs')
        literal='\n'.join(c['text']or'' for c in cells)
        if (any(re.search(pattern,literal,re.I)is None for pattern in config['required_patterns'])
            or set(re.findall(r'[A-Za-z]+',literal.lower())).intersection(config['excluded_words'])):
            raise ValueError('Prior source table is not configured contact-header grammar')
        readings=[]
        for cell in cells:
            text=cell['text']or'';bounds=cell['bbox'];mapping=cell.get('independent_reader_mapping',{})
            if (cell.get('readers_agree')is not True or glyph_audit(text)['issues']
                or cell.get('glyph_audit',{}).get('issues')or cell.get('independent_glyph_audit',{}).get('issues')
                or cell.get('selected_cell_sha256')!=sha(text)
                or cell.get('independent_cell_sha256')!=sha(cell.get('independent_text',''))
                or not agrees(text,mapping.get('mapped_text',''),exact_characters=True)):
                raise ValueError('Prior source header cell lacks complete fresh reader/glyph proof')
            raw,frame=read_source_box(native_page,pdfium_page,textpage,[bounds[0]+.2,bounds[1]+.2,bounds[2]-.2,bounds[3]-.2])
            reread=map_cell_reader(native_page,pdfium_page,textpage,bounds,text,raw,independent_layout)
            if not agrees(text,reread['mapped_text'],exact_characters=True):raise ValueError('Fresh prior-header cell reading differs')
            readings.append({'cell_id':cell['id'],'bbox':bounds,'selected_cell_sha256':sha(text),'independent_raw_sha256':sha(raw),'reader_mapping_sha256':sha(canonical(reread))})
        proofs.append({'bbox':box,'table_version_id':table['table_version_id'],'source_pdf_sha256':table['source_pdf_sha256'],
                       'table_geometry_sha256':table['table_geometry_sha256'],'raw_contact_text_sha256':sha(literal),
                       'cells':readings,'classification':'bounded_source_contact_header_only','table_structure_preserved':True})
    return proofs

def prove_header_metadata(line,native_page,physical_page,table_boxes,textpage,independent_layout,*,pdfium_page=None,header_tables=None):
    """Read PDFium per source block AND separately split pypdf layout row."""
    rules,rule_sha=load_rules();rule_id='header_reference_number_and_date';rule=rules['rules'][rule_id]
    if not table_boxes:
        from .native_tables import find_native_tables
        if header_tables!=[]or find_native_tables(native_page):raise ValueError('Zero-table metadata requires explicit freshly empty source table registry')
    prior_headers=prove_prior_contact_headers(line,native_page,physical_page,table_boxes,header_tables,textpage,independent_layout,pdfium_page,rule)
    groups,texts=source_blocks(line,native_page,physical_page,table_boxes,rule,prior_header_tables=prior_headers)
    matches=[]
    for index,raw in enumerate(independent_layout.splitlines()):
        blocks=[part for part in re.split(r' {'+str(rule['layout_separator_min_spaces'])+r',}',raw.strip())if part]
        if len(blocks)==len(texts)and all(compact(a)==compact(b)for a,b in zip(blocks,texts)):matches.append((index,raw,blocks))
    if len(matches)!=1:raise ValueError('Independent layout metadata blocks missing, changed or ambiguous')
    layout_index,layout_raw,layout_blocks=matches[0];proofs=[]
    from .native_consensus import agrees,repair_control_hyphens
    for block_index,((members),text,layout_text)in enumerate(zip(groups,texts,layout_blocks)):
        box=bbox([char for _,char in members])
        if pdfium_page is None:
            if getattr(native_page,'rotation',0):raise ValueError('Metadata rotated source needs explicit PDFium frame')
            raw=textpage.get_text_bounded(left=box[0]-.2,bottom=native_page.height-box[3]-.2,right=box[2]+.2,top=native_page.height-box[1]+.2)
        else:
            from .native_table_cell_spacing import read_source_box
            read_box=[max(0,box[0]-.2),max(0,box[1]-.2),min(native_page.width,box[2]+.2),min(native_page.height,box[3]+.2)]
            raw,_=read_source_box(native_page,pdfium_page,textpage,read_box)
        mapped,repairs=repair_control_hyphens(raw,text,layout_text)
        if compact(text)!=compact(mapped)or compact(layout_text)!=compact(text):raise ValueError('Independent metadata block content differs from original source')
        members_receipt=[{'line_glyph_index':i,'text':char['text'],'bbox':[char[k]for k in ('x0','top','x1','bottom')]}for i,char in members]
        proofs.append({'block_index':block_index,'field_kind':['reference_number','date'][block_index],'bbox':box,'selected_text':text,'selected_text_sha256':sha(text),'independent_raw_text':raw,'independent_raw_sha256':sha(raw),'independent_mapped_sha256':sha(mapped),'independent_layout_text_sha256':sha(layout_text),'reader_mapping_repairs':repairs,'literal_sequence_proof':'exact_nonwhitespace_codepoints_in_each_independently_bounded_block; no codepoint substitution','source_glyph_members':members_receipt,'source_glyph_members_sha256':sha(canonical(members_receipt))})
    all_indexes=[m['line_glyph_index']for p in proofs for m in p['source_glyph_members']]
    if sorted(all_indexes)!=list(range(len(line['chars']))):raise ValueError('Metadata proof does not partition all original line glyphs')
    return {'kind':KIND,'rule_id':rule_id,'rule_configuration_sha256':rule_sha,'source_line_text_sha256':sha(line['text']),'source_line_bbox':[line[k]for k in ('x0','top','x1','bottom')],'independent_layout_line_index':layout_index,'independent_layout_line_sha256':sha(layout_raw),'blocks':proofs,'preceding_contact_header_tables':prior_headers,'source_text_modified':False,'field_semantics_reviewed':False,'legal_effect_inferred':False}
