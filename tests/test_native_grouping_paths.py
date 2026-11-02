"""Grouping strokes are source relationships, not ignorable decoration."""
import copy
import json
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest

from local_vector_batch.native_grouping_paths import derive_grouping_mask


def source(path, *, color='0 G', width=.7, matrix='', second=False, extra='', alpha=None):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    for number in range(2 if second else 1):
        page = writer.add_blank_page(width=240, height=320)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        if alpha is not None:
            from pypdf.generic import FloatObject
            state=DictionaryObject({NameObject('/CA'):FloatObject(alpha)})
            page['/Resources'][NameObject('/ExtGState')]=DictionaryObject({NameObject('/GS'):state})
        text = 'Other' if number else 'Original'
        content = (f'BT /F1 10 Tf 116 240 Td ({text}) Tj 0 -20 Td (Revised) Tj ET\n'
                   f'q {matrix} {color} {"/GS gs" if alpha is not None else ""} {width} w 106 248 m 102 248 100 247 100 246 c '
                   f'100 226 l 100 224 102 224 106 224 c S Q\n{extra}')
        stream = DecodedStreamObject(); stream.set_data(content.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def derive(path, *, other=None, page=0, mutate=None):
    with pdfplumber.open(path) as native, pdfium.PdfDocument(other or path) as independent:
        view = native.pages[0]
        image = view.to_image(resolution=120)
        if mutate:
            mutate(view)
        return derive_grouping_mask(view, independent[page], path,
                                    np.asarray(image.original.convert('L')) < 100, image.bbox)


def test_exact_source_subpath_paint_and_raster_retain_candidate_relationship(tmp_path):
    path = source(tmp_path/'source.pdf')
    mask, proofs = derive(path)
    assert len(proofs) == 1 and mask.any()
    proof = proofs[0]
    assert json.loads(json.dumps(proofs)) == proofs
    assert proof['physical_page'] == 1 and proof['stroke_RGBA'] == [0, 0, 0, 255]
    assert proof['raster_ink_pixels'] == int(mask.sum())
    assert proof['semantic_ownership_approved'] is False
    assert proof['bounding_interior_authorized'] is False
    assert proof['source_subpaths'][0]['relationship'] == 'source_grouping_geometry_candidate'
    assert len(proof['source_subpaths'][0]['adjacent_glyphs']) > 2
    assert not mask[140, 172]  # Unpainted bracket interior is never authorized.


def test_changed_source_or_physical_page_cannot_supply_a_matching_bracket(tmp_path):
    path = source(tmp_path/'source.pdf', second=True)
    other = source(tmp_path/'different.pdf', extra='0 g 10 10 1 1 re f')
    with pytest.raises(ValueError, match='source identity'):
        derive(path, other=other)
    with pytest.raises(ValueError, match='physical page'):
        derive(path, page=1)


def test_native_control_point_change_requires_independent_exact_path(tmp_path):
    def mutate(native):
        native.curves[0]['path'] = copy.deepcopy(native.curves[0]['path'])
        native.curves[0]['path'][1] = ('c', (103, 72), *native.curves[0]['path'][1][2:])
    with pytest.raises(ValueError, match='independent path'):
        derive(source(tmp_path/'source.pdf'), mutate=mutate)


@pytest.mark.parametrize('kwargs', [{'color':'1 0 0 RG'}, {'width':3}, {'alpha':.5}])
def test_unsupported_paint_stays_held(tmp_path, kwargs):
    with pytest.raises(ValueError, match='paint|stroke width'):
        derive(source(tmp_path/'source.pdf', **kwargs))


def test_foreign_ink_inside_bracket_is_not_owned(tmp_path):
    mask, _ = derive(source(tmp_path/'source.pdf', extra='0 g 102 234 1 1 re f'))
    assert not mask[141, 171]


def test_translated_actual_object_matrix_is_preserved(tmp_path):
    _, proof = derive(source(tmp_path/'source.pdf', matrix='1 0 0 1 -10 0 cm'))
    assert proof[0]['independent_path']['matrix'] == [1, 0, 0, 1, -10, 0]


def test_closed_unsupported_curve_gets_no_grouping_mask(tmp_path):
    from local_vector_batch.native_grouping_paths import shape
    assert not shape([['m', [0, 0]], ['c', [1, 0], [2, 1], [3, 3]], ['h']])
    path=source(tmp_path/'unsupported.pdf',extra='0 G 150 120 m 140 140 170 140 160 120 c h S')
    mask,_=derive(path)
    with pdfplumber.open(path)as native:
        ink=np.asarray(native.pages[0].to_image(resolution=120).original.convert('L'))<100
    assert (ink[290:340,220:290]&~mask[290:340,220:290]).any()


def test_changed_independent_affine_matrix_cannot_authorize_source_path(tmp_path,monkeypatch):
    from pypdfium2._helpers.pageobjects import PdfObject
    monkeypatch.setattr(PdfObject,'get_matrix',lambda self:pdfium.PdfMatrix(1,0,0,1,25,0))
    with pytest.raises(ValueError,match='independent path'):
        derive(source(tmp_path/'source.pdf'))


def test_duplicate_source_subpaths_and_changed_bounds_stay_held(tmp_path):
    extra='0 G .7 w 106 248 m 102 248 100 247 100 246 c 100 226 l 100 224 102 224 106 224 c S'
    with pytest.raises(ValueError,match='unique independent'):
        derive(source(tmp_path/'duplicate.pdf',extra=extra))
    with pytest.raises(ValueError,match='bounds differ'):
        derive(source(tmp_path/'bounds.pdf'),mutate=lambda native:native.curves[0].update(x0=95))


def test_crossing_glyph_is_not_hidden_by_grouping_proof(tmp_path):
    with pytest.raises(ValueError, match='overlaps source glyphs'):
        derive(source(tmp_path/'source.pdf', extra='BT /F1 8 Tf 99 235 Td (X) Tj ET'))
