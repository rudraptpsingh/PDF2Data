"""Exact native-view to raw-PDF rectangles for independent source-cell reads.

A PDF /Rotate entry changes displayed geometry, not PDFium text-page coordinates.
This mapper changes only the reader rectangle. It never changes a reading,
strips whitespace, chooses a higher-scoring interpretation, or approves a cell.
"""
from __future__ import annotations
import math
import hashlib
import json
import re


def _rotation0(box,w,h):
    x0,y0,x1,y1=box;return [x0,h-y1,x1,h-y0]
def _rotation90(box,w,h):
    x0,y0,x1,y1=box;return [y0,x0,y1,x1]
def _rotation180(box,w,h):
    x0,y0,x1,y1=box;return [w-x1,y0,w-x0,y1]
def _rotation270(box,w,h):
    x0,y0,x1,y1=box;return [w-y1,h-x1,w-y0,h-x0]

ROTATION_INVERSES={0:_rotation0,90:_rotation90,180:_rotation180,270:_rotation270}


def native_box_to_pdf_box(box,*,rotation,raw_pdf_bbox,native_size,native_origin=(0,0),dimension_tolerance=.5):
    """Use the declared right-angle page rotation; unsupported frames fail closed."""
    if type(rotation)is not int or rotation not in ROTATION_INVERSES:
        raise ValueError('Unsupported source page rotation')
    if len(box)!=4 or len(raw_pdf_bbox)!=4 or len(native_size)!=2 or len(native_origin)!=2:
        raise ValueError('Incomplete source coordinate metadata')
    if any(type(v)not in(int,float)or not math.isfinite(v)for v in[*box,*raw_pdf_bbox,*native_size,*native_origin]):
        raise ValueError('Nonfinite source coordinate metadata')
    if tuple(native_origin)!=(0,0)or tuple(raw_pdf_bbox[:2])!=(0,0):
        raise ValueError('Translated/cropped source frame requires an explicit coordinate proof')
    width,height=raw_pdf_bbox[2]-raw_pdf_bbox[0],raw_pdf_bbox[3]-raw_pdf_bbox[1]
    if width<=0 or height<=0:raise ValueError('Invalid raw PDF page dimensions')
    expected=(height,width)if rotation in(90,270)else(width,height)
    if any(abs(a-b)>dimension_tolerance for a,b in zip(native_size,expected)):
        raise ValueError('Native/raw source page dimensions disagree')
    x0,y0,x1,y1=map(float,box)
    if not(0<=x0<x1<=native_size[0]and 0<=y0<y1<=native_size[1]):
        raise ValueError('Source read rectangle is outside native page or empty')
    raw=ROTATION_INVERSES[rotation]([x0,y0,x1,y1],width,height)
    if not(0<=raw[0]<raw[2]<=width and 0<=raw[1]<raw[3]<=height):
        raise ValueError('Mapped source rectangle is outside raw PDF page')
    return raw


def source_read_rectangle(native_page,pdfium_page,box):
    rotation=native_page.rotation
    if type(rotation)is not int or rotation!=pdfium_page.get_rotation():
        raise ValueError('Independent source page rotations disagree')
    native_size=[float(native_page.width),float(native_page.height)]
    if any(abs(a-b)>.5 for a,b in zip(native_size,pdfium_page.get_size())):
        raise ValueError('Independent displayed source dimensions disagree')
    raw_pdf_bbox=list(pdfium_page.get_bbox())
    raw=native_box_to_pdf_box(box,rotation=rotation,raw_pdf_bbox=raw_pdf_bbox,
                             native_size=native_size,native_origin=native_page.bbox[:2])
    return {'contract_version':'pdf2data.metadata-bound-source-rectangle.v1',
            'native_view_bbox':list(map(float,box)),'raw_pdf_bbox':raw_pdf_bbox,
            'pdfium_read_bbox':raw,'page_rotation_degrees':rotation,
            'native_view_dimensions':native_size,'transformation_basis':'declared_PDF_Rotate_inverse_only',
            'text_modified':False,'cell_or_page_approval':False}


def read_source_box(native_page,pdfium_page,text_page,box):
    receipt=source_read_rectangle(native_page,pdfium_page,box)
    left,bottom,right,top=receipt['pdfium_read_bbox']
    return text_page.get_text_bounded(left=left,bottom=bottom,right=right,top=top),receipt


def unique_cell_context(selected,source_lines,third,*,max_context_chars=2000):
    """A unique ordered source-line context, allowing neighboring table columns.

    All native cell characters must be accounted for. Repeated/ambiguous third
    reader occurrences fail; no approximate text similarity selects a match.
    Returned offsets index the untouched independent reader string.
    """
    compact=lambda s:''.join(c for c in s if not c.isspace())
    flat=compact(third);positions=[i for i,c in enumerate(third)if not c.isspace()]
    anchors=[compact(line['text'])for line in source_lines if line['text'].strip()]
    if not anchors or ''.join(anchors)!=compact(selected):anchors=[compact(selected)]
    if not anchors[0]or len(anchors)>64:return None
    occurrences=[[m.start()for m in re.finditer(re.escape(a),flat)]for a in anchors]
    if any(not xs or len(xs)>64 for xs in occurrences):return None
    complete=[];work=0
    def visit(index,first,end,spans):
        nonlocal work
        work+=1
        if len(complete)>1 or work>4096:return
        if index==len(anchors):complete.append(spans);return
        for start in occurrences[index]:
            stop=start+len(anchors[index])
            if start<end or stop-first>max_context_chars:continue
            visit(index+1,first,stop,spans+[(start,stop)])
    for start in occurrences[0]:visit(1,start,start+len(anchors[0]),[(start,start+len(anchors[0]))])
    if work>4096 or len(complete)!=1:return None
    spans=[[positions[lo],positions[hi-1]+1]for lo,hi in complete[0]]
    return {'basis':'unique_ordered_exact_native_cell_lines_in_independent_layout',
            'line_spans':spans,'raw_context_sha256':hashlib.sha256(third[spans[0][0]:spans[-1][1]].encode()).hexdigest(),
            'max_context_chars':max_context_chars}


def map_cell_reader(native_page,pdfium_page,text_page,box,selected,raw,secondary_layout,*,_allow_tight_ownership=True):
    """Apply registered reader-only rules with original glyph/third-reader proof.

    Raw strings are retained. No native extraction, word, number, operator or
    cell value is rewritten. Ambiguous source order, glyphs or real gaps hold.
    """
    from .native_consensus import agrees,repair_control_hyphens
    from .native_token_spacing import SpacingPolicy,_spacing_boundaries
    from .native_tables import glyph_audit
    compact=lambda s:''.join(c for c in s if not c.isspace())
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    policy=SpacingPolicy();crop=native_page.crop(tuple(box));glyphs=list(crop.chars)
    glyph_text=''.join(g['text']for g in glyphs)
    proof={'contract_version':'pdf2data.source-bound-cell-reader-mapping.v1','raw_text':raw,'mapped_text':raw,
           'raw_sha256':digest(raw),'mapped_sha256':digest(raw),'repairs':[],
           'native_glyphs_sha256':digest(json.dumps([{k:g.get(k)for k in('text','x0','top','x1','bottom','size')}for g in glyphs],sort_keys=True)),
           'secondary_layout_sha256':digest(secondary_layout),'source_bbox':list(box),
           'selected_text_modified':False,'page_quality_cleared':False,'legal_review':'not_run'}
    if not selected or len(selected)>policy.max_line_chars or compact(glyph_text)!=compact(selected):
        if _allow_tight_ownership and selected and len(selected)<=policy.max_line_chars and compact(raw)==compact(selected):
            try:
                from .native_tight_cell_ownership import try_owned_cell_reader
                recovered=try_owned_cell_reader(native_page,pdfium_page,text_page,box,selected,raw,secondary_layout,proof)
                if recovered:return recovered
            except (ImportError,ValueError,OSError,RuntimeError)as error:
                proof['tight_ownership_hold']=type(error).__name__+': '+str(error)
        return {**proof,'hold':'native_cell_glyph_order_disagrees'}
    mapped,repairs=repair_control_hyphens(raw,glyph_text,secondary_layout)
    if '\x02'in raw and not repairs and compact(raw.replace('\x02','-'))==compact(glyph_text):
        # A multiline cell need not be contiguous in a full-page table read.
        # Prove every control mapping on its own original geometric source line.
        witnesses=[]
        for line in crop.extract_text_lines(return_chars=True):
            chars=line.get('chars',[]);native=''.join(g['text']for g in sorted(chars,key=lambda g:g['x0']))
            if '-'not in native:continue
            rectangle=[max(box[0],line['x0']-.2),max(box[1],line['top']-.2),
                       min(box[2],line['x1']+.2),min(box[3],line['bottom']+.2)]
            text,coordinates=read_source_box(native_page,pdfium_page,text_page,rectangle)
            corrected,line_repairs=repair_control_hyphens(text,native,secondary_layout)
            needle=compact(native);full=compact(glyph_text)
            if line_repairs and needle and full.count(needle)==1:
                start=full.index(needle)
                witnesses.append({'native_line':native,'raw_text':text,'mapped_text':corrected,
                    'mapping_repairs':line_repairs,'source_rectangle':coordinates,
                    'cell_compact_control_positions':[start+i for i,c in enumerate(compact(text))if c=='\x02']})
        expected={i for i,c in enumerate(compact(raw))if c=='\x02'}
        actual=[i for w in witnesses for i in w['cell_compact_control_positions']]
        if expected==set(actual)and len(actual)==len(expected)and witnesses:
            mapped=raw.replace('\x02','-');repairs=[{'kind':'source_bound_pdfium_control_hyphen_mapping',
                'basis':'every_control_glyph_has_exact_native_line_and_unique_pypdf_context',
                'raw_text':raw,'corrected_text':mapped,'line_witnesses':witnesses}]
    if not agrees(selected,mapped,exact_characters=True):
        tokens=list(re.finditer(r'\S+',selected));pattern=r'^\s*'+r'\s+'.join(
            '(?P<t'+str(i)+'>'+r'\s*'.join(re.escape(c)for c in token.group())+')'
            for i,token in enumerate(tokens))+r'\s*$'
        match=re.fullmatch(pattern,mapped)if tokens else None
        # The independent third reader must uniquely account for this complete
        # cell context; a repeated token alone never authorizes a join.
        context=unique_cell_context(selected,crop.extract_text_lines(return_chars=True),secondary_layout,
                                    max_context_chars=policy.max_line_chars)if match else None
        if match and context:
            nonwhite=[(c,g,i)for i,g in enumerate(glyphs)for c in g['text']if not c.isspace()]
            boundaries=[]
            for i,token in enumerate(tokens):
                start=len(compact(selected[:token.start()]));source_token=nonwhite[start:start+len(token.group())]
                found=_spacing_boundaries(match.group('t'+str(i)),source_token,glyphs,policy,match.start('t'+str(i)),allow_newlines=True)
                if found is None:boundaries=[];break
                boundaries.extend(found)
            if boundaries:
                corrected=mapped
                for boundary in sorted(boundaries,key=lambda b:b['independent_char_start'],reverse=True):
                    lo=boundary['independent_char_start'];hi=lo+boundary['independent_char_length']
                    if not corrected[lo:hi].isspace():raise ValueError('Cell spacing rule would alter source content')
                    corrected=corrected[:lo]+corrected[hi:]
                if agrees(selected,corrected,exact_characters=True):
                    repairs.append({'kind':'source_geometry_bound_cell_reader_spacing','boundaries':boundaries,
                        'basis':'same_native_word_glyph_adjacency_and_unique_pypdf_cell_context','third_reader_context':context,
                        'raw_text':mapped,'corrected_text':corrected});mapped=corrected
    if not agrees(selected,mapped,exact_characters=True):
        from .native_cell_space_witness import restore_explicit_source_spaces
        original_glyphs=getattr(native_page,'chars',None)
        within=[];partial=False
        if original_glyphs is not None:
            for original in original_glyphs:
                if not original.get('text','').strip()and not original.get('text','').isspace():continue
                if min(original['x1'],box[2])>max(original['x0'],box[0])+.01 and min(original['bottom'],box[3])>max(original['top'],box[1])+.01:
                    if original.get('text','').strip()and not(box[0]-.01<=original['x0']and original['x1']<=box[2]+.01 and box[1]-.01<=original['top']and original['bottom']<=box[3]+.01):partial=True
                    within.append(original)
        restored=restore_explicit_source_spaces(selected,mapped,within,secondary_layout)if within and not partial else None
        if restored and agrees(selected,restored['corrected_text'],exact_characters=True):
            repairs.append(restored);mapped=restored['corrected_text']
    if repairs and agrees(selected,mapped,exact_characters=True)and not glyph_audit(mapped)['issues']:
        proof.update(mapped_text=mapped,mapped_sha256=digest(mapped),repairs=repairs,status='source_proved')
    return proof
