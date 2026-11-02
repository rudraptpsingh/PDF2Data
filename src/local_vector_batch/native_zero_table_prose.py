"""Closed source predicates before proving native prose without table regions."""
from collections import Counter
import hashlib,json
from pathlib import Path


def prove_zero_table_source(source,page_number,native_page,source_pdf_sha256):
    """Observe real absence and visible glyph ownership; never invent a grid."""
    from .native_tables import find_native_tables,glyph_audit
    from .native_paint_visibility import analyze
    from .native_tight_cell_ownership import supported_contexts
    from .text_script import native_font_script_evidence
    from .quality_gate import load_policy
    compact=lambda s:''.join(c for c in s if not c.isspace())
    if find_native_tables(native_page):raise ValueError('Zero-table region snapshot omits original native table geometry')
    if not compact(''.join(g['text']for g in native_page.chars)):raise ValueError('Zero-table native prose needs original nonempty glyphs')
    if any(glyph_audit(g['text'])['issues']for g in native_page.chars):raise ValueError('Zero-table native glyph mapping is unresolved')
    if native_font_script_evidence(native_page.chars,load_policy())['required_scripts']:raise ValueError('Native prose font mapping needs independent raster linguistic proof')
    paint=analyze(source,page_number,[])
    if paint['source']['source_pdf_sha256']!=source_pdf_sha256:raise ValueError('Zero-table paint source changed')
    policy=paint['policy']['rules']['later_opaque_rectangular_fill']
    objects=[*native_page.chars,*native_page.lines,*native_page.rects]
    content_box=[min(o['x0']for o in objects),min(o['top']for o in objects),max(o['x1']for o in objects),max(o['bottom']for o in objects)]
    # A rectangular clip wholly containing EVERY original glyph and path cannot
    # remove source content. Partial/malformed clips and transparency still hold.
    if not supported_contexts(paint['source']['drawings'],content_box,policy):raise ValueError('Zero-table native prose has unsupported clipping/transparency')
    glyphs=paint['classified_glyphs']
    held=[g for g in glyphs if g['visibility_state']not in('retained_visible_or_unproven','retained_raw_whitespace')]
    disjoint=None
    if (held and not paint['unsupported_paints'] and
        all(g['visibility_state']=='held_partial_occlusion'for g in held)):
        disjoint=_source_text_ink_disjoint_fills(source,page_number,native_page,paint)
    if paint['unsupported_paints']or(held and disjoint is None):raise ValueError('Zero-table native prose has occluded or unsupported source glyphs')
    def white(color):
        if isinstance(color,(float,int)):return color==1
        return isinstance(color,(tuple,list))and bool(color)and all(channel==1 for channel in color)
    if any(white(g['color'])for g in glyphs if g['text'].strip()):raise ValueError('White native text needs independent contrast/ink proof')
    if Counter(compact(''.join(g['text']for g in glyphs)))!=Counter(compact(''.join(g['text']for g in native_page.chars))):raise ValueError('Zero-table independent source glyph inventories disagree')
    return {'kind':'source_verified_zero_native_tables_and_visible_prose.v1','source_pdf_sha256':source_pdf_sha256,
            'page':page_number,'source_content_bbox':content_box,'fresh_native_table_count':0,'source_glyph_count':len(native_page.chars),
            'paint_visibility_proof_sha256':hashlib.sha256(json.dumps(paint,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'metric_overlap_source_ink_reproof':disjoint,
            'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'source_text_modified':False,'table_semantics_inferred':False,'source_quality_page_cleared':False,'legal_publication':False}


def isolated_source_text_ink(source,page_number,width,height,*,dpi=240):
    """Fresh full-page text paint; no source bytes or selected text are changed."""
    import numpy as np
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(source)as document:
        page=document[page_number-1]
        if list(page.get_size())!=[width,height]:raise ValueError('Text-paint physical page dimensions differ')
        objects=list(page.get_objects(max_depth=0))
        if any(o.type==pdfium.raw.FPDF_PAGEOBJ_FORM for o in objects):raise ValueError('Text-paint Form objects require separate proof')
        for obj in objects:
            if obj.type!=pdfium.raw.FPDF_PAGEOBJ_TEXT:page.remove_obj(obj);obj.close()
        page.gen_content();bitmap=page.render(scale=dpi/72,may_draw_forms=False)
        try:return np.asarray(bitmap.to_pil().convert('L'))<255
        finally:bitmap.close()


def _source_text_ink_disjoint_fills(source,page_number,native,paint):
    """Resolve metric-only overlap using freshly rendered original text paint.

    The complete fill rectangles must contain zero isolated text ink. No glyph
    is removed, and any genuine/ambiguous paint intersection stays held.
    """
    import math
    dpi=240
    text_ink=isolated_source_text_ink(source,page_number,native.width,native.height,dpi=dpi)
    if not text_ink.any():return None
    h,w=text_ink.shape;proofs=[]
    held=[g for g in paint['classified_glyphs']if g['visibility_state']=='held_partial_occlusion']
    seqnos={seq for g in held for seq in g['coverage']['cover_seqnos']}
    for fill in paint['accepted_opaque_fills']:
        if fill['seqno']not in seqnos:continue
        a,b,c,d=fill['effective_bbox']
        region=[max(0,math.floor(a*w/native.width)),max(0,math.floor(b*h/native.height)),
                min(w,math.ceil(c*w/native.width)),min(h,math.ceil(d*h/native.height))]
        x0,y0,x1,y1=region
        if text_ink[y0:y1,x0:x1].any():return None
        proofs.append({'fill_seqno':fill['seqno'],'source_bbox':fill['effective_bbox'],
                       'conservative_raster_rectangle':region,'isolated_source_text_ink_pixels':0})
    if {p['fill_seqno']for p in proofs}!=seqnos:return None
    return {'kind':'source_text_ink_disjoint_later_rectangle_fills.v1','source_pdf_sha256':paint['source']['source_pdf_sha256'],
            'physical_page':page_number,'dpi':dpi,'text_ink_mask_sha256':hashlib.sha256(text_ink.tobytes()).hexdigest(),
            'fills':proofs,'glyphs_removed':0,'source_text_modified':False}
