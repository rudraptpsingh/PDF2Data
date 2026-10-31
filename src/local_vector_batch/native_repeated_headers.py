"""Prove repeated source top-band blocks without dropping their literal text."""
import json,re
from collections import Counter
from pathlib import Path
from .native_metadata_rows import compact,bbox,canonical,sha


def partition(line,native,page,rule):
    if native.page_number!=page or line['bottom']>native.height*rule['maximum_bottom_page_fraction']or line['bottom']-line['top']>rule['maximum_line_height_points']:raise ValueError('Repeated header outside bounded source top band')
    from .text_script import native_font_script_evidence
    from .quality_gate import load_policy
    from .native_tables import glyph_audit
    if native_font_script_evidence(line['chars'],load_policy())['required_scripts']or any(glyph_audit(g['text'])['issues']for g in line['chars']):raise ValueError('Repeated header has unresolved source font/glyph mapping')
    ordered=sorted((g for g in line['chars']if g['text'].strip()),key=lambda g:g['x0']);splits=[(a['x1']+b['x0'])/2 for a,b in zip(ordered,ordered[1:])if b['x0']-a['x1']>max(rule['minimum_gap_points'],rule['minimum_gap_font_multiple']*max(a.get('size',0),b.get('size',0)))]
    if not rule['minimum_blocks']<=len(splits)+1<=rule['maximum_blocks']:raise ValueError('Repeated header needs bounded separated source blocks')
    groups=[[]for _ in range(len(splits)+1)]
    for index,char in enumerate(line['chars']):
        if any(char['x0']<s<char['x1']for s in splits):raise ValueError('Header glyph crosses source block boundary')
        groups[sum((char['x0']+char['x1'])/2>=s for s in splits)].append((index,char))
    from pdfplumber.utils import extract_text
    texts=[extract_text([g for _,g in group],layout=False)for group in groups]
    if compact(''.join(texts))!=compact(line['text']):raise ValueError('Repeated header partition changes source text')
    numbers=[i for i,t in enumerate(texts)if re.fullmatch(r'[0-9]{1,5}',compact(t))]
    if len(numbers)!=1 or numbers[0]not in(0,len(groups)-1):raise ValueError('Repeated header needs one isolated edge page-number block')
    num=numbers[0];bounds=[bbox([g for _,g in group])for group in groups];edge=rule['maximum_edge_page_number_fraction']
    if (num==0 and bounds[num][2]>native.width*edge)or(num==len(groups)-1 and bounds[num][0]<native.width*(1-edge)):raise ValueError('Observed page number is not at source edge')
    stable=[t for i,t in enumerate(texts)if i!=num]
    letters=[re.findall('[A-Za-z]',text)for text in stable]
    headings=[i for i,chars in enumerate(letters)if len(chars)>=rule['minimum_stable_latin_letters']and ''.join(chars)==''.join(chars).upper()]
    if not headings or any(len(chars)>rule['maximum_auxiliary_latin_letters']for i,chars in enumerate(letters)if i not in headings)or set(re.findall('[A-Za-z]+',line['text'].lower())).intersection(rule['operative_words']):raise ValueError('Repeated top-band content is not configured nonoperative header syntax')
    return {'groups':groups,'texts':texts,'boxes':bounds,'page_number_index':num,'printed_page_number':int(compact(texts[num]))}


def independently_read(source,native,page,line,parts,view,tp,layout):
    matches=[(i,row)for i,row in enumerate(layout.splitlines())if compact(row)==compact(line['text'])]
    if len(matches)!=1:raise ValueError('Independent source header row missing, changed or ambiguous')
    from .native_table_cell_spacing import read_source_box
    blocks=[]
    for index,(group,text,box)in enumerate(zip(parts['groups'],parts['texts'],parts['boxes'])):
        raw,frame=read_source_box(native,view,tp,[max(0,box[0]-.2),max(0,box[1]-.2),min(native.width,box[2]+.2),min(native.height,box[3]+.2)])
        if compact(raw)!=compact(text):raise ValueError('Independent source header block differs')
        blocks.append({'block_index':index,'bbox':box,'selected_text':text,'selected_text_sha256':sha(text),'independent_raw_text':raw,'independent_raw_sha256':sha(raw),'independent_source_rectangle':frame,'source_glyph_members':[{'line_glyph_index':i,'text':g['text'],'bbox':[g[k]for k in('x0','top','x1','bottom')]}for i,g in group]})
    from .native_paint_visibility import capture,opaque_fills,classify_glyphs,rules,intersect
    from .native_tight_cell_ownership import supported_contexts
    paint=capture(source,page);policy=rules()['rules']['later_opaque_rectangular_fill'];fills,unsupported=opaque_fills(paint['drawings'],policy);region=bbox(line['chars']);glyphs=[g for g in classify_glyphs(paint['glyphs'],fills,policy)if intersect(g['bbox'],region)]
    if Counter(compact(''.join(g['text']for g in glyphs)))!=Counter(compact(line['text'])):raise ValueError('Repeated header independent visible-glyph inventory differs')
    if unsupported or not supported_contexts(paint['drawings'],region,policy)or any(g['visibility_state']not in('retained_visible_or_unproven','retained_raw_whitespace')for g in glyphs):raise ValueError('Repeated header source visibility remains unresolved')
    if any(g['color']in(1,1.0,[1.,1.,1.])for g in glyphs if g['text'].strip()):raise ValueError('White header glyph needs separate contrast proof')
    return {'physical_page':page,'source_line_text_sha256':sha(line['text']),'source_line_bbox':region,'blocks':blocks,'printed_page_number_literal':parts['texts'][parts['page_number_index']],'independent_layout_line_index':matches[0][0],'independent_layout_line_sha256':sha(matches[0][1]),'source_paint_sha256':sha(canonical(paint)),'scope':'header_blocks_only; reference body quality not promoted'}


def prove_repeated_source_header(line,native,page,table_boxes,textpage,layout,*,pdfium_page=None,header_tables=None,table_proofs=None):
    config_path=Path(__file__).with_suffix('.json');rule=json.loads(config_path.read_text());target=partition(line,native,page,rule)
    if pdfium_page is None or any(line['bottom']>=box[1]for box in table_boxes):raise ValueError('Repeated header must precede source tables with explicit independent frame')
    source_name=getattr(native.pdf.stream,'name',None)
    if not source_name:raise ValueError('Original PDF path required for source-header repetition')
    source=Path(source_name);pages=native.pdf.pages;radius=rule['reference_page_radius'];references=[]
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from .native_tables import find_native_tables
    with pdfium.PdfDocument(source)as other:
        reader=PdfReader(source)
        for n in sorted((n for n in range(max(1,page-radius),min(len(pages),page+radius)+1)if n!=page),key=lambda n:abs(n-page)):
            pg=pages[n-1]
            if abs(pg.width-native.width)>.01 or abs(pg.height-native.height)>.01:continue
            candidates=[]
            for candidate in pg.extract_text_lines(return_chars=True):
                if candidate['bottom']>pg.height*rule['maximum_bottom_page_fraction']:continue
                try:parts=partition(candidate,pg,n,rule)
                except ValueError:continue
                if len(parts['texts'])!=len(target['texts'])or parts['page_number_index']!=target['page_number_index']:continue
                num=target['page_number_index'];tol=rule['geometry_tolerance_points']
                if parts['printed_page_number']-target['printed_page_number']!=n-page:continue
                if any(compact(a)!=compact(b)for i,(a,b)in enumerate(zip(parts['texts'],target['texts']))if i!=num):continue
                if any(any(abs(a-b)>tol for a,b in zip(box,target['boxes'][i]))for i,box in enumerate(parts['boxes'])if i!=num):continue
                edge=0 if num==0 else 2
                if abs(parts['boxes'][num][edge]-target['boxes'][num][edge])>tol:continue
                if any(candidate['bottom']>=t.bbox[1]for t in find_native_tables(pg)):continue
                candidates.append((candidate,parts))
            if len(candidates)>1:raise ValueError('Repeated source header position is ambiguous')
            if len(candidates)==1:
                candidate,parts=candidates[0];view=other[n-1];tp=view.get_textpage()
                try:reference=independently_read(source,pg,n,candidate,parts,view,tp,reader.pages[n-1].extract_text(extraction_mode='layout')or'')
                finally:tp.close();view.close()
                references.append(reference);break
    if not references:raise ValueError('No independently matching repeated source header in bounded page window')
    target_proof=independently_read(source,native,page,line,target,pdfium_page,textpage,layout)
    return {'kind':'source_bound_repeated_page_header_blocks.v1','rule_configuration_sha256':sha(config_path.read_bytes()),'classification':'observed_repeated_source_page_header_layout; all literals retained','target':target_proof,'references':references,'physical_page_number_sequence_observed':True,'reference_body_quality_promoted':False,'source_text_modified':False,'text_removed':False,'header_semantics_reviewed':False,'legal_effect_inferred':False}
