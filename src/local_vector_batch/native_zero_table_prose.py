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
    if paint['unsupported_paints']or any(g['visibility_state']not in('retained_visible_or_unproven','retained_raw_whitespace')for g in glyphs):raise ValueError('Zero-table native prose has occluded or unsupported source glyphs')
    def white(color):
        if isinstance(color,(float,int)):return color==1
        return isinstance(color,(tuple,list))and bool(color)and all(channel==1 for channel in color)
    if any(white(g['color'])for g in glyphs if g['text'].strip()):raise ValueError('White native text needs independent contrast/ink proof')
    if Counter(compact(''.join(g['text']for g in glyphs)))!=Counter(compact(''.join(g['text']for g in native_page.chars))):raise ValueError('Zero-table independent source glyph inventories disagree')
    return {'kind':'source_verified_zero_native_tables_and_visible_prose.v1','source_pdf_sha256':source_pdf_sha256,
            'page':page_number,'source_content_bbox':content_box,'fresh_native_table_count':0,'source_glyph_count':len(native_page.chars),
            'paint_visibility_proof_sha256':hashlib.sha256(json.dumps(paint,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'source_text_modified':False,'table_semantics_inferred':False,'source_quality_page_cleared':False,'legal_publication':False}
