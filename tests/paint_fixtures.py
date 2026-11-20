"""Deterministic fixture PDFs for the paint-visibility reader goldens (L2/L3).

Built from raw content streams with pypdf only, so the fixtures never depend on
the reader under test. Each case exercises one rule edge of
``native_paint_visibility``; the real pages come from a public order that is
committed beside these tests.
"""
from pathlib import Path

HERE = Path(__file__).parent
REAL = HERE / 'fixtures' / 'paint' / 'cerc-545-TD-2026.pdf'

T = 'BT /F1 16 Tf 40 260 Td (X) Tj ET\n'           # one single-character paint
WORD = 'BT /F1 14 Tf 40 200 Td (Visible) Tj ET\n'   # control text, never covered

CASES = {
    'plain_text': WORD + 'BT /F2 11 Tf 40 150 Td (Plain text line) Tj ET\n',
    'white_cover_full': WORD + T + '1 1 1 rg 36 250 20 24 re f\n',
    'black_cover_full': WORD + T + '0 0 0 rg 36 250 20 24 re f\n',
    'cover_earlier': WORD + '1 1 1 rg 36 250 20 24 re f\n' + T,
    'cover_partial': WORD + T + '1 1 1 rg 36 250 8 24 re f\n',
    'two_rects_union': WORD + T + '1 1 1 rg 36 250 10 24 re f 46 250 10 24 re f\n',
    'two_rects_gap': WORD + T + '1 1 1 rg 36 250 9.5 24 re f 46 250 10 24 re f\n',
    'transparent_fill': WORD + T + 'q /GSA gs 1 1 1 rg 36 250 20 24 re f Q\n',
    'clip_rect_cover': WORD + T + 'q 30 240 40 40 re W n 1 1 1 rg 36 250 20 24 re f Q\n',
    'clip_rect_narrow': WORD + T + 'q 30 240 12 40 re W n 1 1 1 rg 36 250 20 24 re f Q\n',
    'clip_nonrect': WORD + T + 'q 30 240 m 70 240 l 50 290 l h W n 1 1 1 rg 36 250 20 24 re f Q\n',
    'form_opaque_cover': WORD + T + 'q /FmPlain Do Q\n',
    'form_group_alpha': WORD + T + 'q /GSA gs /FmGroup Do Q\n',
    'optional_content': WORD + T + '/OC /L1 BDC 1 1 1 rg 36 250 20 24 re f EMC\n',
    'stroke_text': WORD + 'BT 1 Tr /F1 16 Tf 40 260 Td (X) Tj ET\n1 1 1 rg 36 250 20 24 re f\n',
    'invisible_text': WORD + 'BT 3 Tr /F1 16 Tf 40 260 Td (X) Tj ET\n1 1 1 rg 36 250 20 24 re f\n',
    'shared_paint_fonts': 'BT /F1 10 Tf 40 100 Td (X) Tj /F1 8 Tf 20 0 Td (Y) Tj ET\n1 1 1 rg 30 90 60 30 re f\n',
    'whitespace': 'BT /F1 14 Tf 40 200 Td (A B) Tj ET\n1 1 1 rg 36 190 60 30 re f\n',
    'underline_metric_only': 'BT /F2 13.2 Tf 40 260 Td (INDEX) Tj ET\n0 0 0 rg 39 257.9 61 0.1 re f\n',
    'text_after_cover': WORD + '1 1 1 rg 36 250 20 24 re f\n' + T + 'BT /F1 16 Tf 60 260 Td (Z) Tj ET\n',
    'tj_kerning': 'BT /F1 14 Tf 40 200 Td [(A) -400 (B) 250 (C)] TJ ET\n1 1 1 rg 36 190 14 24 re f\n',
    'nonrect_fill': WORD + T + '1 1 1 rg 30 240 m 80 240 l 55 290 l h f\n',
    'scaled_text_matrix': 'BT /F1 1 Tf 18 0 0 18 40 240 Tm (W) Tj ET\n1 1 1 rg 36 230 24 26 re f\n',
    'cm_translated_cover': WORD + T + 'q 1 0 0 1 30 240 cm 1 1 1 rg 6 10 20 24 re f Q\n',
    'fill_stroke_cover': WORD + T + '1 1 1 rg 0 G 36 250 20 24 re B\n',
    'gray_fill_cover': WORD + T + '0.5 g 36 250 20 24 re f\n',
    'table_redacted_cell': ('0 G 0.5 w 30 100 180 40 re S 120 100 m 120 140 l S\n'
                             'BT /F2 10 Tf 40 115 Td (Name) Tj 90 0 Td (Amount) Tj ET\n'
                             '1 1 1 rg 121 101 88 38 re f\n'),
    'multi_line_paragraph': ('BT /F2 10 Tf 12 TL 30 280 Td (First line of a paragraph) Tj T* '
                              '(second line continues) Tj T* (third line) Tj ET\n1 1 1 rg 28 252 120 13 re f\n'),
    'rotated_page': WORD,
    'soft_mask_state': WORD + T,
}
ROTATED = {'rotated_page'}
SOFT_MASK = {'soft_mask_state'}
REAL_PAGES = [1, 2, 3, 4, 5, 6]


def _write(path, content, *, rotate=False, soft_mask=False):
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                               NameObject, NumberObject)
    writer = PdfWriter()
    page = writer.add_blank_page(width=240, height=320)
    def font(base):
        return writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject(base)}))
    def form(body, group):
        stream = DecodedStreamObject(); stream.set_data(body.encode())
        stream.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                       NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(240), NumberObject(320)])})
        if group:
            stream[NameObject('/Group')] = DictionaryObject({NameObject('/S'): NameObject('/Transparency')})
        return writer._add_object(stream)
    cover = '1 1 1 rg 36 250 20 24 re f\n'
    states = DictionaryObject({NameObject('/GSA'): DictionaryObject({NameObject('/ca'): FloatObject(0.5),
                                                                     NameObject('/CA'): FloatObject(0.5)})})
    if soft_mask:
        states[NameObject('/GSM')] = DictionaryObject({NameObject('/SMask'): DictionaryObject(
            {NameObject('/S'): NameObject('/Alpha')})})
    layer = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/OCG'),
                                                 NameObject('/Name'): NameObject('/Hidden')}))
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font('/Helvetica'),
                                               NameObject('/F2'): font('/Times-Roman')}),
        NameObject('/ExtGState'): states,
        NameObject('/XObject'): DictionaryObject({NameObject('/FmPlain'): form(cover, False),
                                                  NameObject('/FmGroup'): form(cover, True)}),
        NameObject('/Properties'): DictionaryObject({NameObject('/L1'): layer}),
    })
    stream = DecodedStreamObject(); stream.set_data(content.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    if rotate:
        page[NameObject('/Rotate')] = NumberObject(90)
    with open(path, 'wb') as handle:
        writer.write(handle)
    return path


def build_all(folder):
    """Yield (case_id, pdf_path, page) for every fixture page, synthetic then real."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    for name, content in CASES.items():
        yield name, _write(folder / f'{name}.pdf', content, rotate=name in ROTATED,
                           soft_mask=name in SOFT_MASK), 1
    for page in REAL_PAGES:
        yield f'cerc_545_td_2026_p{page}', REAL, page


def project(capture_fn, analyze_fn, source, page):
    """Reader-neutral projection of the paint-visibility outcome for one page.

    Seqnos and glyph keys are reader-internal numbering, so the golden keeps
    the paint-ordered text, visibility states and geometry instead.
    """
    try:
        proof = analyze_fn(source, page, [])
    except ValueError as error:
        return {'error': str(error)}
    r = lambda box: [round(v, 2) for v in box]
    glyphs = [{'text': g['text'], 'bbox': r(g['bbox']), 'state': g['visibility_state'],
               'excluded': g['excluded_from_visible_candidate'], 'ink_basis': g['ink_bounds_basis']}
              for g in proof['classified_glyphs']]
    return {
        'page_bbox': r(proof['source']['page_bbox']),
        'glyphs': glyphs,
        'retained_text': ''.join(g['text'] for g in glyphs if not g['excluded']),
        'excluded_text': ''.join(g['text'] for g in glyphs if g['excluded']),
        'accepted_fill_bboxes': sorted(r(f['effective_bbox']) for f in proof['accepted_opaque_fills']),
        'unsupported_reasons': sorted(u['reason'] for u in proof['unsupported_paints']),
    }


def record(output, folder):
    """Record the goldens with whatever reader ``native_paint_visibility`` uses now."""
    import json
    from local_vector_batch import native_paint_visibility as module
    goldens = {case: project(module.capture, module.analyze, path, page)
               for case, path, page in build_all(folder)}
    write_goldens(output, goldens, reader=getattr(module, 'READER', 'PyMuPDF'))
    return goldens


def write_goldens(output, goldens, *, reader):
    import gzip, json
    body = json.dumps({'reader': reader, 'pages': goldens}, sort_keys=True, separators=(',', ':')).encode()
    with open(output, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as handle:
        handle.write(body)


def load_goldens(path):
    import gzip, json
    with gzip.open(path) as handle:
        return json.load(handle)


if __name__ == '__main__':
    import sys, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = record(sys.argv[1], tmp)
    print(len(result), 'pages recorded')
