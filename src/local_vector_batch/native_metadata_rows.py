"""Independently prove two actual header metadata blocks; never waive a gap."""
import hashlib,json,re
from pathlib import Path

KIND='source_bound_header_reference_date_blocks.v1'
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def compact(value):return ''.join(c for c in value if not c.isspace())
def bbox(chars):return[min(c['x0']for c in chars),min(c['top']for c in chars),max(c['x1']for c in chars),max(c['bottom']for c in chars)]
def load_rules():
    path=Path(__file__).with_suffix('.json');rules=json.loads(path.read_text())
    if rules.get('schema_version')!='pdf2data.native-header-metadata-rules.v1':raise ValueError('Unknown metadata rule schema')
    return rules,sha(path.read_bytes())

def source_blocks(line,native_page,physical_page,table_boxes,rule):
    """Qualify exactly two source-glyph partitions in a bounded first-page header."""
    if physical_page not in rule['physical_pages']or line['bottom']>native_page.height*rule['max_bottom_fraction']or line['bottom']-line['top']>rule['max_line_height_points']:raise ValueError('Metadata row outside bounded first-page header')
    if not table_boxes or line['bottom']>=min(box[1]for box in table_boxes):raise ValueError('Metadata row must precede every source table')
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

def prove_header_metadata(line,native_page,physical_page,table_boxes,textpage,independent_layout):
    """Read PDFium per source block AND separately split pypdf layout row."""
    rules,rule_sha=load_rules();rule_id='header_reference_number_and_date';rule=rules['rules'][rule_id]
    groups,texts=source_blocks(line,native_page,physical_page,table_boxes,rule)
    matches=[]
    for index,raw in enumerate(independent_layout.splitlines()):
        blocks=[part for part in re.split(r' {'+str(rule['layout_separator_min_spaces'])+r',}',raw.strip())if part]
        if len(blocks)==len(texts)and all(compact(a)==compact(b)for a,b in zip(blocks,texts)):matches.append((index,raw,blocks))
    if len(matches)!=1:raise ValueError('Independent layout metadata blocks missing, changed or ambiguous')
    layout_index,layout_raw,layout_blocks=matches[0];proofs=[]
    from .native_consensus import agrees,repair_control_hyphens
    for block_index,((members),text,layout_text)in enumerate(zip(groups,texts,layout_blocks)):
        box=bbox([char for _,char in members]);raw=textpage.get_text_bounded(left=box[0]-.2,bottom=native_page.height-box[3]-.2,right=box[2]+.2,top=native_page.height-box[1]+.2)
        mapped,repairs=repair_control_hyphens(raw,text,layout_text)
        if compact(text)!=compact(mapped)or compact(layout_text)!=compact(text):raise ValueError('Independent metadata block content differs from original source')
        members_receipt=[{'line_glyph_index':i,'text':char['text'],'bbox':[char[k]for k in ('x0','top','x1','bottom')]}for i,char in members]
        proofs.append({'block_index':block_index,'field_kind':['reference_number','date'][block_index],'bbox':box,'selected_text':text,'selected_text_sha256':sha(text),'independent_raw_text':raw,'independent_raw_sha256':sha(raw),'independent_mapped_sha256':sha(mapped),'independent_layout_text_sha256':sha(layout_text),'reader_mapping_repairs':repairs,'literal_sequence_proof':'exact_nonwhitespace_codepoints_in_each_independently_bounded_block; no codepoint substitution','source_glyph_members':members_receipt,'source_glyph_members_sha256':sha(canonical(members_receipt))})
    all_indexes=[m['line_glyph_index']for p in proofs for m in p['source_glyph_members']]
    if sorted(all_indexes)!=list(range(len(line['chars']))):raise ValueError('Metadata proof does not partition all original line glyphs')
    return {'kind':KIND,'rule_id':rule_id,'rule_configuration_sha256':rule_sha,'source_line_text_sha256':sha(line['text']),'source_line_bbox':[line[k]for k in ('x0','top','x1','bottom')],'independent_layout_line_index':layout_index,'independent_layout_line_sha256':sha(layout_raw),'blocks':proofs,'source_text_modified':False,'field_semantics_reviewed':False,'legal_effect_inferred':False}
