"""Independently retain literal form labels and blank marker glyphs.

This explains a source layout gap; it neither erases markers nor fills a field.
It is used only after complete native source-table cells have been verified.
"""
import json,re
from pathlib import Path
from .native_metadata_rows import compact,bbox,sha


def prove_literal_form_row(line,native,page,table_boxes,textpage,layout,*,pdfium_page=None,header_tables=None):
    config_path=Path(__file__).with_suffix('.json');cfg=json.loads(config_path.read_text())
    if cfg.get('contract_version')!='pdf2data.literal-source-form-row-rules.v1':raise ValueError('Unknown literal form row rule')
    if pdfium_page is None or native.page_number!=page:raise ValueError('Literal form row requires exact independent source page')
    if line['bottom']-line['top']>cfg['maximum_line_height_points']:raise ValueError('Literal form row exceeds single-row height')
    original_bbox=bbox(line['chars'])
    if any(min(original_bbox[2],b[2])>max(original_bbox[0],b[0])and min(original_bbox[3],b[3])>max(original_bbox[1],b[1])for b in table_boxes):raise ValueError('Literal form row intersects native table geometry')
    ordered=sorted((g for g in line['chars']if g['text'].strip()),key=lambda g:g['x0'])
    splits=[(a['x1']+b['x0'])/2 for a,b in zip(ordered,ordered[1:])if b['x0']-a['x1']>max(cfg['minimum_gap_points'],cfg['minimum_gap_font_multiple']*max(a.get('size',0),b.get('size',0)))]
    if len(splits)!=1:raise ValueError('Literal form row needs exactly two source blocks')
    split=splits[0];groups=[[],[]]
    for i,g in enumerate(line['chars']):
        if g['x0']<split<g['x1']:raise ValueError('Literal form source glyph crosses block boundary')
        groups[int((g['x0']+g['x1'])/2>=split)].append((i,g))
    from pdfplumber.utils import extract_text
    texts=[extract_text([g for _,g in group],layout=False)for group in groups]
    labels=[label for label,pattern in cfg['label_patterns'].items()if re.fullmatch(pattern,texts[0],re.I)]
    marker=compact(texts[1])
    if len(labels)!=1 or not 1<=len(marker)<=cfg['maximum_marker_characters']or re.fullmatch(cfg['marker_pattern'],marker)is None:raise ValueError('Source row is not a configured literal label plus unchanged blank markers')
    if compact(''.join(texts))!=compact(line['text']):raise ValueError('Literal form row partition changes original text')
    matches=[]
    for index,raw in enumerate(layout.splitlines()):
        blocks=[part for part in re.split(r' {'+str(cfg['layout_separator_min_spaces'])+r',}',raw.strip())if part]
        if len(blocks)==2 and all(compact(a)==compact(b)for a,b in zip(blocks,texts)):matches.append((index,raw))
    if not matches:raise ValueError('Independent layout does not retain literal label/markers as two blocks')
    # Repeated identical rows are valid only with equal source/layout counts;
    # every occurrence is independently bounded by its own PDFium rectangle.
    native_matching=sum(compact(candidate['text'])==compact(line['text'])for candidate in native.extract_text_lines())
    if len(matches)!=native_matching:raise ValueError('Repeated literal form row source/layout cardinality differs')
    from .native_table_cell_spacing import read_source_box
    proofs=[];pad=cfg['source_box_padding_points']
    for group,text in zip(groups,texts):
        b=bbox([g for _,g in group]);raw,frame=read_source_box(native,pdfium_page,textpage,[max(0,b[0]-pad),max(0,b[1]-pad),min(native.width,b[2]+pad),min(native.height,b[3]+pad)])
        if compact(raw)!=compact(text):raise ValueError('Independent source rectangle differs from literal form block')
        proofs.append({'bbox':b,'selected_literal':text,'selected_literal_sha256':sha(text),'independent_literal':raw,'independent_literal_sha256':sha(raw),'independent_source_rectangle':frame,'source_glyph_members':[{'line_glyph_index':i,'literal':g['text'],'bbox':[g[k]for k in ('x0','top','x1','bottom')]}for i,g in group]})
    return {'kind':'source_bound_literal_form_label_and_markers.v1','rule_configuration_sha256':sha(config_path.read_bytes()),'label_syntax_candidate':labels[0],'source_line_literal':line['text'],'source_line_literal_sha256':sha(line['text']),'blocks':proofs,'matching_independent_layout_rows':[{'index':i,'sha256':sha(raw)}for i,raw in matches],'literal_marker_glyphs':marker,'literal_markers_preserved':True,'field_value_inferred':False,'source_text_modified':False,'source_quality_cleared_by_this_rule':False,'legal_effect_inferred':False}
