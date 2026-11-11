"""Source-specific, review-only candidate for one DSM Gazette header glyph.

The candidate is not a linguistic approval, a page-quality waiver, or an
L1/Map publication. It is available only for the exact reviewed original,
physical page, selected reading, font streams, source glyph and raster crop.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

SOURCE = 'a39c0efc43709b039d6bf3cc34bb45415150eeb9ca299d789c7622bf16788fc7'
PAGE_SHA = '40cf00f9ed58543093c4c1fc93726500222374c25520a041c1b56bd09ec0a4d9'
FONT_SHA = '5d65859a1e864e3f9a005ede572d48e6aeebbcae9f276089b800b342f0db91e2'
CMAP_SHA = '1e3d1ad8a2a56f21ae64e6509b6b925a4c690a6c9bb98d61104f1c2ad9d7b044'
CROP_SHA = '487153f683349843e7603b2e5e53955afc15eae04c8e3179611e16b37272975b'
BOX = (283.5628, 39.2916, 290.4153, 49.2516)
HEADER = '[भाग III—खण् ड 4] भारत का रािपत्र : असाधारण 13'


def _hash(data):
    return sha256(data).hexdigest()


def _observe(source):
    import pdfplumber
    from pypdf import PdfReader
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[12]
        hits = [g for g in page.chars if g.get('text') == 'ि' and
                all(abs(float(g[k]) - v) < .01 for k, v in zip(('x0','top','x1','bottom'), BOX))]
        crop = BytesIO()
        page.crop((225,34,356,51)).to_image(resolution=300).save(crop,format='PNG')
        glyphs = [{'text': g['text'], 'font': g['fontname'],
                   'bbox': [float(g[k]) for k in ('x0','top','x1','bottom')]} for g in hits]
    page = PdfReader(source).pages[12]
    fonts = [v.get_object() for v in page['/Resources']['/Font'].values()
             if str(v.get_object().get('/BaseFont')) == '/BCDEEE+ArialUnicodeMS']
    if len(fonts) != 1:
        raise ValueError('reviewed font resource is not unique')
    font = fonts[0]
    desc = font['/DescendantFonts'][0].get_object()['/FontDescriptor'].get_object()
    return {'glyphs': glyphs, 'font_sha256': _hash(desc['/FontFile2'].get_object().get_data()),
            'cmap_sha256': _hash(font['/ToUnicode'].get_object().get_data()),
            'crop_sha256': _hash(crop.getvalue())}


def propose(source, physical_page, selected):
    """Return an exact source-bound correction *candidate*; never grant approval."""
    source = Path(source)
    if physical_page != 13 or _hash(source.read_bytes()) != SOURCE:
        raise ValueError('source or physical page outside reviewed scope')
    if not isinstance(selected,str) or _hash(selected.encode()) != PAGE_SHA:
        raise ValueError('selected parent page differs from reviewed reading')
    if selected.splitlines()[0] != HEADER or selected.count(HEADER) != 1:
        raise ValueError('header context is absent or ambiguous')
    observed = _observe(source)
    glyphs = observed['glyphs']
    if (len(glyphs) != 1 or glyphs[0]['text'] != 'ि' or
        glyphs[0]['font'] != 'BCDEEE+ArialUnicodeMS' or
        any(abs(a-b) >= .01 for a,b in zip(glyphs[0]['bbox'],BOX)) or
        observed['font_sha256'] != FONT_SHA or observed['cmap_sha256'] != CMAP_SHA or
        observed['crop_sha256'] != CROP_SHA):
        raise ValueError('reviewed glyph/font/raster evidence changed')
    start=selected.index('रािपत्र') + 2
    if selected[start] != 'ि':
        raise ValueError('source glyph offset changed')
    proposed=selected[:start]+'ज'+selected[start+1:]
    return {'schema_version':'pdf2data.dsm-header-glyph-candidate.v1',
        'source_pdf_sha256':SOURCE,'page':13,'parent_page_text_sha256':PAGE_SHA,
        'selected_start':start,'selected_end':start+1,'original_quote':'ि','candidate':'ज',
        'candidate_page_text':proposed,'candidate_page_sha256':_hash(proposed.encode()),
        'glyph':glyphs[0],'font_sha256':FONT_SHA,'cmap_sha256':CMAP_SHA,
        'crop_sha256':CROP_SHA,'crop_bbox_pdf_points':[225,34,356,51],'crop_dpi':300,
        'basis':'visually_observed_exact_source_font_glyph_and_raster',
        'independent_linguistic_review':'not_run','source_quality_approval_granted':False,
        'legal_approval':False,'publication_eligible':False,'original_text_preserved':True}
