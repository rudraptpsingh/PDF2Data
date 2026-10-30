"""Independently read literal empty form fields above proved native tables."""
import re,json,hashlib
from pathlib import Path
from .native_metadata_rows import compact,bbox,canonical,sha


def prove_blank_form_header(line,native,page,table_boxes,textpage,layout,*,pdfium_page=None,header_tables=None,table_proofs=None):
    config_path=Path(__file__).with_suffix('.json');config=json.loads(config_path.read_text())
    if config['contract_version']!='pdf2data.blank-source-form-header-fields.v1':raise ValueError('Unknown blank form-field rule')
    if native.page_number!=page:raise ValueError('Blank form physical page differs from source frame')
    if not table_boxes or not header_tables or not table_proofs or pdfium_page is None:raise ValueError('Blank form header requires complete proved source tables')
    if line['bottom']>native.height*config['maximum_bottom_page_fraction']or line['bottom']-line['top']>config['maximum_line_height_points']or line['bottom']>=min(box[1]for box in table_boxes):raise ValueError('Blank form field outside bounded pre-table header')
    for table in header_tables:
        proofs=[p for p in table_proofs if p['table_version_id']==table['table_version_id']]
        if table.get('page')!=page or len(proofs)!=1 or proofs[0]['page']!=page or proofs[0]['source_pdf_sha256']!=table['source_pdf_sha256']or proofs[0]['status']!='validated'or len(proofs[0]['cells'])!=len(table['cell_spans']):raise ValueError('Blank form table proof incomplete')
        actual={c['id']:c for c in proofs[0]['cells']}
        for cell in table['cell_spans']:
            if cell['id']not in actual or actual[cell['id']]['status']!='validated'or any(actual[cell['id']][k]!=cell[k]for k in('bbox','row','column','selected_cell_sha256','independent_cell_sha256')):raise ValueError('Blank form source-cell binding differs')
    glyphs=line['chars'];ordered=sorted((g for g in glyphs if g['text'].strip()),key=lambda g:g['x0']);gaps=[(a['x1']+b['x0'])/2 for a,b in zip(ordered,ordered[1:])if b['x0']-a['x1']>max(config['minimum_gap_points'],config['minimum_gap_font_multiple']*max(a.get('size',0),b.get('size',0)))]
    if len(gaps)!=1:raise ValueError('Blank form header needs exactly two source blocks')
    split=gaps[0];groups=[[],[]]
    for i,char in enumerate(glyphs):
        if char['x0']<split<char['x1']:raise ValueError('Blank form glyph crosses block boundary')
        groups[int((char['x0']+char['x1'])/2>=split)].append((i,char))
    from pdfplumber.utils import extract_text
    texts=[extract_text([g for _,g in group],layout=False)for group in groups]
    labels=[key for key,pattern in config['label_patterns'].items()if re.fullmatch(pattern,texts[0],re.I)]
    if len(labels)!=1 or not re.fullmatch('_{1,'+str(config['maximum_blank_marker_characters'])+'}',compact(texts[1])):raise ValueError('Source row is not a configured label plus literal blank underscores')
    if compact(''.join(texts))!=compact(line['text']):raise ValueError('Blank form block partition changes source line')
    matches=[]
    for i,raw in enumerate(layout.splitlines()):
        blocks=[part for part in re.split(r' {'+str(config['layout_separator_min_spaces'])+r',}',raw.strip())if part]
        if len(blocks)==2 and all(compact(a)==compact(b)for a,b in zip(texts,blocks)):matches.append((i,raw))
    if len(matches)!=1:raise ValueError('Independent blank form row missing, changed or ambiguous')
    from .native_table_cell_spacing import read_source_box
    proofs=[]
    for index,(group,text)in enumerate(zip(groups,texts)):
        box=bbox([g for _,g in group]);raw,frame=read_source_box(native,pdfium_page,textpage,[max(0,box[0]-.2),max(0,box[1]-.2),min(native.width,box[2]+.2),min(native.height,box[3]+.2)])
        if compact(raw)!=compact(text):raise ValueError('Independent blank form block differs')
        proofs.append({'block_index':index,'bbox':box,'selected_text':text,'selected_text_sha256':sha(text),'independent_raw_text':raw,'independent_raw_sha256':sha(raw),'independent_source_rectangle':frame,'source_glyph_members':[{'line_glyph_index':i,'text':g['text'],'bbox':[g[k]for k in('x0','top','x1','bottom')]}for i,g in group]})
    return {'kind':'source_bound_literal_blank_form_header.v1','rule_configuration_sha256':sha(config_path.read_bytes()),'label_candidate':labels[0],'source_line_text':line['text'],'source_line_text_sha256':sha(line['text']),'blocks':proofs,'literal_blank_marker_preserved':True,'field_value':None,'field_state':'unfilled_literal_underscores','independent_layout_line_index':matches[0][0],'independent_layout_line_sha256':sha(matches[0][1]),'proved_table_versions':[t['table_version_id']for t in header_tables],'source_text_modified':False,'field_semantics_reviewed':False,'legal_effect_inferred':False}
