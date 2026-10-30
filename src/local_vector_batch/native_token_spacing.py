"""Reconstitute layout-inserted token spaces using exact original glyph geometry.

Only a uniquely source-bound line read by PDFium and the native extractor can
authorize horizontal layout spaces inside one native token. Source text, real
word gaps, table cells and line boundaries are never rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass,asdict
import hashlib
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class SpacingPolicy:
    max_gap_em:float=.16
    max_gap_points:float=2.0
    min_vertical_overlap:float=.6
    max_line_chars:int=2000


LEARNED_REPAIR_REGISTRY={
    'split_numeric_token':{'changes':'independent_layout_horizontal_whitespace_only','requires':'exact_source_token_glyph_geometry_and_unique_independent_context'},
    'split_operator_word':{'changes':'independent_layout_horizontal_whitespace_only','requires':'exact_source_token_glyph_geometry_and_unique_independent_context'},
    'split_native_word':{'changes':'independent_layout_horizontal_whitespace_only','requires':'exact_source_token_glyph_geometry_and_unique_independent_context'},
    'source_bound_pdfium_control_hyphen_mapping':{'changes':'independent_pdfium_reader_mapping_only','requires':'exact_native_glyph_line_and_unique_pypdf_layout_context'},
}


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()


def compact(value):
    return ''.join(c for c in value if not c.isspace())


def _intersects(a,b):
    return min(a[2],b[2])>max(a[0],b[0])and min(a[3],b[3])>max(a[1],b[1])


def _boundary(left,right,policy):
    gap=right['x0']-left['x1'];size=min(float(left['size']),float(right['size']))
    overlap=max(0,min(left['bottom'],right['bottom'])-max(left['top'],right['top']))
    height=min(left['bottom']-left['top'],right['bottom']-right['top'])
    if size<=0 or height<=0 or gap<-.25*size or gap>min(policy.max_gap_points,policy.max_gap_em*size)or overlap/height<policy.min_vertical_overlap:
        return None
    return {'left_bbox':[left[k]for k in ('x0','top','x1','bottom')],
            'right_bbox':[right[k]for k in ('x0','top','x1','bottom')],
            'source_gap_points':gap,'source_gap_em':gap/size,'vertical_overlap_fraction':overlap/height}


def _spacing_boundaries(actual,source_token,glyphs,policy,start,allow_newlines=False):
    spaces=list(re.finditer(r'\s+'if allow_newlines else r'[ \t]+',actual));boundaries=[]
    for space in spaces:
        position=len(compact(actual[:space.start()]))
        if not 0<position<len(source_token):return None
        left,right=source_token[position-1],source_token[position]
        if any(g['text'].isspace()for g in glyphs[left[2]+1:right[2]]):return None
        proof=_boundary(left[1],right[1],policy)
        if proof is None:return None
        boundaries.append({**proof,'independent_char_start':start+space.start(),
                           'independent_char_length':space.end()-space.start()})
    return boundaries


def reconstitute_line(native_line,glyphs,pdfium_raw,layout_text,*,table_boxes=(),policy=None,selected_line_start=None):
    from .native_consensus import agrees,repair_control_hyphens
    from .quality_gate import load_policy
    policy=policy or SpacingPolicy()
    if not native_line or len(native_line)>policy.max_line_chars:
        return {'repairs':[],'holds':['unsupported_source_line_length']}
    stream=''.join(g['text']for g in glyphs)
    if compact(stream)!=compact(native_line):return {'repairs':[],'holds':['native_line_glyph_inventory_disagrees']}
    mapped,mapping=repair_control_hyphens(pdfium_raw,native_line,layout_text)
    tokens=list(re.finditer(r'\S+',native_line))
    if not tokens:return {'repairs':[],'holds':[]}
    pattern=r'(?<!\S)'+r'[ \t]+'.join('(?P<t'+str(i)+'>'+r'[ \t]*'.join(re.escape(c)for c in token.group())+')'for i,token in enumerate(tokens))+r'(?!\S)'
    contexts=list(re.finditer(pattern,layout_text))
    if len(contexts)!=1:return {'repairs':[],'holds':['independent_line_context_missing_or_ambiguous']}
    context=contexts[0];nonwhite=[]
    for index,glyph in enumerate(glyphs):
        for char in glyph['text']:
            if not char.isspace():nonwhite.append((char,glyph,index))
    pdfium_spacing=[]
    if not agrees(native_line,mapped,exact_characters=True):
        # PDFium can insert a line separator before a raised ordinal suffix.
        # Validate each such boundary against the same physical glyph word;
        # an actual new line, word gap or table cell cannot authorize it.
        pdfium_pattern=r'(?<!\S)'+r'\s+'.join('(?P<t'+str(i)+'>'+r'\s*'.join(re.escape(c)for c in token.group())+')'for i,token in enumerate(tokens))+r'(?!\S)'
        contexts_pdfium=list(re.finditer(pdfium_pattern,mapped))
        if len(contexts_pdfium)!=1 or mapped[:contexts_pdfium[0].start()].strip()or mapped[contexts_pdfium[0].end():].strip():
            return {'repairs':[],'holds':['independent_pdfium_source_line_disagrees']}
        witness=contexts_pdfium[0]
        for i,token in enumerate(tokens):
            actual=witness.group('t'+str(i));source_start=len(compact(native_line[:token.start()]));source_token=nonwhite[source_start:source_start+len(token.group())]
            boundaries=_spacing_boundaries(actual,source_token,glyphs,policy,witness.start('t'+str(i)),allow_newlines=True)
            if boundaries is None:return {'repairs':[],'holds':['pdfium_spacing_is_not_a_contiguous_source_word']}
            if boundaries:pdfium_spacing.append({'source_token':token.group(),'raw_reader_token':actual,'boundaries':boundaries})
    operators={word.casefold()for word in load_policy()['operators']if re.fullmatch(r'[A-Za-z]+',word)}
    repairs=[];holds=[]
    for i,token in enumerate(tokens):
        actual=context.group('t'+str(i));spaces=list(re.finditer(r'[ \t]+',actual))
        if not spaces:continue
        source_start=len(compact(native_line[:token.start()]));source_token=nonwhite[source_start:source_start+len(token.group())]
        if ''.join(c for c,_,_ in source_token)!=token.group():
            holds.append('source_token_mapping_unavailable');continue
        box=[min(g['x0']for _,g,_ in source_token),min(g['top']for _,g,_ in source_token),
             max(g['x1']for _,g,_ in source_token),max(g['bottom']for _,g,_ in source_token)]
        if any(_intersects(box,b)for b in table_boxes):
            holds.append('table_token_requires_cell_proof');continue
        boundaries=_spacing_boundaries(actual,source_token,glyphs,policy,context.start('t'+str(i)))
        if boundaries is None:
            holds.append('real_or_ambiguous_source_token_gap');continue
        kind='split_numeric_token'if any(c.isdigit()for c in token.group())else'split_operator_word'if token.group().casefold()in operators else'split_native_word'
        repairs.append({'kind':kind,'source_token':token.group(),'selected_line_span':[token.start(),token.end()],
                        'selected_span':None if selected_line_start is None else[selected_line_start+token.start(),selected_line_start+token.end()],
                        'independent_span':[context.start('t'+str(i)),context.end('t'+str(i))],
                        'independent_raw_token':actual,'source_bbox':box,'boundaries':boundaries,
                        'source_line_sha256':digest(native_line),'pdfium_raw_line_sha256':digest(pdfium_raw),
                        'pdfium_mapped_line_sha256':digest(mapped),'reader_mapping_repairs':mapping,
                        'pdfium_source_token_spacing_proofs':pdfium_spacing,
                        'unique_independent_context_sha256':digest(context.group()),'policy':asdict(policy),
                        'selected_text_modified':False})
    return {'repairs':repairs,'holds':holds}


def reconstitute_page(source_pdf,physical_page,selected_text,*,policy=None):
    import pdfplumber
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from .native_tables import find_native_tables
    source_pdf=Path(source_pdf);policy=policy or SpacingPolicy()
    layout=PdfReader(source_pdf).pages[physical_page-1].extract_text(extraction_mode='layout')or''
    repairs=[];holds=[]
    with pdfplumber.open(source_pdf)as pdf,pdfium.PdfDocument(source_pdf)as independent:
        native=pdf.pages[physical_page-1];view=independent[physical_page-1];text_page=view.get_textpage()
        try:
            if selected_text!=native.extract_text():raise ValueError('Selected text is not the exact fresh native extraction')
            boxes=[list(t.bbox)for t in find_native_tables(native)]
            for line in native.extract_text_lines(return_chars=True):
                x0,top,x1,bottom=[line[k]for k in ('x0','top','x1','bottom')]
                from .native_table_cell_spacing import read_source_box
                raw,_=read_source_box(native,view,text_page,[max(0,x0-.2),max(0,top-.2),min(native.width,x1+.2),min(native.height,bottom+.2)])
                start=selected_text.find(line['text'])if selected_text.count(line['text'])==1 else None
                result=reconstitute_line(line['text'],line['chars'],raw,layout,table_boxes=boxes,policy=policy,selected_line_start=start)
                if result['repairs']and start is None:
                    holds.append('selected_page_line_context_ambiguous');continue
                repairs.extend(result['repairs']);holds.extend(result['holds'])
        finally:text_page.close();view.close()
    spans=[]
    for repair in repairs:
        spans.extend((b['independent_char_start'],b['independent_char_start']+b['independent_char_length'])for b in repair['boundaries'])
    if len(spans)!=len(set(spans)):raise ValueError('Duplicate or overlapping space repair scope')
    corrected=layout
    for lo,hi in sorted(spans,reverse=True):
        if not re.fullmatch(r'[ \t]+',layout[lo:hi]):raise ValueError('Repair would alter non-horizontal-whitespace source reader content')
        corrected=corrected[:lo]+corrected[hi:]
    return {'kind':'source_geometry_bound_layout_token_spacing.v1','source_pdf_sha256':digest(source_pdf.read_bytes()),
            'page':physical_page,'page_text_sha256':digest(selected_text),'independent_raw_sha256':digest(layout),
            'independent_corrected_sha256':digest(corrected),'independent_raw_text':layout,'independent_corrected_text':corrected,
            'repairs':repairs,'unresolved_line_diagnostics':sorted(set(holds)),
            'source_text_modified':False,'page_quality_cleared':False,'publication_eligible':False,
            'engine_sha256':digest(Path(__file__).read_bytes()),'policy':asdict(policy)}
