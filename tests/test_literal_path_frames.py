"""Portable path evidence cannot authorize frame interiors or hidden ink."""
from pathlib import Path
import copy
import pytest
from test_native_minimal_grids import grid_pdf


def source(tmp_path,extra='',bars=None):
    from pypdf import PdfReader,PdfWriter
    from pypdf.generic import NameObject,DecodedStreamObject
    initial=tmp_path/'grid.pdf';grid_pdf(initial,2,1)
    reader=PdfReader(initial);writer=PdfWriter();writer.add_page(reader.pages[0]);page=writer.pages[0]
    bars=bars or'0 g 10 10 220 .48 re f 10 309.52 220 .48 re f 10 10 .48 300 re f 229.52 10 .48 300 re f 15 100 210 .48 re f'
    stream=DecodedStreamObject();stream.set_data(page.get_contents().get_data()+b'\n'+(bars+'\n'+extra).encode());page[NameObject('/Contents')]=writer._add_object(stream)
    path=tmp_path/'paths.pdf';writer.write(path);return path


def derive(path,mutate=None):
    import pdfplumber,pypdfium2 as pdfium,numpy as np
    from local_vector_batch.literal_path_frames import derive_literal_path_mask
    with pdfplumber.open(path)as pdf:
        native=pdf.pages[0];ind=pdfium.PdfDocument(path);view=ind[0];image=native.to_image(resolution=120)
        if mutate:mutate(native)
        try:return derive_literal_path_mask(native,view,np.asarray(image.original.convert('L'))<200,image.bbox)
        finally:view.close();ind.close()


def test_literal_frame_edges_and_separator_preserve_unexplained_interior(tmp_path):
    path=source(tmp_path,'0 g 116 115 8 8 re f');mask,proof=derive(path)
    assert len(proof['paths'])>=5
    # Unknown black square inside page frame is never explained by its bbox.
    assert not mask[330,200]
    assert all(p['rectangle_interior_authorized']is False for p in proof['paths'])
    assert all(p['semantic_role']=='unresolved_literal_path'for p in proof['paths'])
    assert proof['source_ink_pixels']==int(mask.sum())


def test_path_crossing_source_word_is_not_authorized(tmp_path):
    path=source(tmp_path,'0 g 30 248 180 .48 re f');_,proof=derive(path)
    assert not any(abs(p['source_bbox'][1]-71.52)<.02 for p in proof['paths'])


def test_forged_full_rectangle_cannot_use_real_thin_source_path(tmp_path):
    path=source(tmp_path)
    def mutate(native):
        for obj in native.rects:
            if obj['width']>200:obj['bottom']=obj['top']+100;obj['height']=100
    _,proof=derive(path,mutate)
    assert not any(p['source_bbox'][3]-p['source_bbox'][1]>1 and p['source_bbox'][2]-p['source_bbox'][0]>1 for p in proof['paths'])


def test_reader_geometry_mismatch_fails_closed(tmp_path,monkeypatch):
    from local_vector_batch import literal_path_frames as engine
    monkeypatch.setattr(engine,'_independent_path',lambda *a:None)
    mask,proof=derive(source(tmp_path))
    assert not mask.any()and proof['paths']==[]


@pytest.mark.parametrize('extra',['0 g 15 100 210 8 re f','0 G 0 0 1 RG 8 w 15 100 m 225 100 l S','0 g 15 100 210 .48 re f'])
def test_masks_never_expand_to_connected_component_interiors(tmp_path,extra):
    mask,proof=derive(source(tmp_path,extra))
    assert not mask[200,200]


def test_short_operator_like_paths_remain_unresolved(tmp_path):
    path=source(tmp_path,'0 g 30 120 50 .48 re f');_,proof=derive(path)
    assert not any(abs(p['source_bbox'][1]-199.52)<.02 for p in proof['paths'])


BLUE_FRAME='0.122 0.286 0.49 rg 12.16 307.84 215.68 2.16 re f 12.16 10 215.68 2.16 re f 10 12.16 2.16 295.68 re f 227.84 12.16 2.16 295.68 re f 10 10 2.16 2.16 re f 227.84 10 2.16 2.16 re f 10 307.84 2.16 2.16 re f 227.84 307.84 2.16 2.16 re f'


def test_closed_coloured_frame_requires_all_eight_actual_parts(tmp_path):
    mask,proof=derive(source(tmp_path,bars=BLUE_FRAME))
    assert len(proof['closed_page_frames'])==1
    frame=proof['closed_page_frames'][0]
    assert frame['independent_opaque_source_parts']==8 and frame['interior_authorized']is False
    assert not mask[330,200]


@pytest.mark.parametrize('extra',['1 g 10.1 100 .8 10 re f','BT /F1 2 Tf 10 100 Td (X) Tj ET'])
def test_closed_frame_rejects_white_overpaint_or_glyphs(tmp_path,extra):
    _,proof=derive(source(tmp_path,extra,bars=BLUE_FRAME))
    assert proof['closed_page_frames']==[]


def test_incomplete_closed_frame_cannot_authorize_thick_sides(tmp_path):
    _,proof=derive(source(tmp_path,bars=BLUE_FRAME.replace('10 10 2.16 2.16 re f','')))
    assert proof['closed_page_frames']==[]
    assert not any(p['source_bbox'][2]-p['source_bbox'][0]>1 and p['source_bbox'][3]-p['source_bbox'][1]>1 for p in proof['paths'])


def test_thick_separator_is_not_a_page_frame(tmp_path):
    _,proof=derive(source(tmp_path,'0 g 15 100 210 2.16 re f'))
    assert not any(abs(p['source_bbox'][1]-217.84)<.02 for p in proof['paths'])


def test_duplicate_opaque_corner_paints_keep_exact_multiplicity(tmp_path):
    extra='0.122 0.286 0.49 rg 10 10 2.16 2.16 re f'
    _,proof=derive(source(tmp_path,extra,bars=BLUE_FRAME))
    assert len(proof['closed_page_frames'])==1
    assert proof['closed_page_frames'][0]['geometric_frame_parts']==8
    assert proof['closed_page_frames'][0]['independent_opaque_source_parts']==9


def test_actual_width_top_header_black_separator_is_source_proved(tmp_path):
    _,proof=derive(source(tmp_path,bars='0 g 15 290 210 1.44 re f'))
    assert len(proof['top_header_separators'])==1
    assert any(p['classification']=='opaque_top_header_separator'for p in proof['paths'])


@pytest.mark.parametrize('bar,extra',[
 ('0 g 15 100 210 1.44 re f',''),
 ('0 g 15 290 150 1.44 re f',''),
 ('0 g 15 290 210 1.51 re f',''),
 ('0.1 g 15 290 210 1.44 re f',''),
 ('0 g 15 290 210 1.44 re f','1 g 60 290 10 .5 re f'),
 ('0 g 15 290 210 1.44 re f','BT /F1 2 Tf 60 290 Td (X) Tj ET'),
])
def test_header_separator_does_not_waive_band_width_thickness_paint_or_glyphs(tmp_path,bar,extra):
    _,proof=derive(source(tmp_path,extra,bars=bar))
    assert proof['top_header_separators']==[]
    assert not any(p['classification']=='opaque_top_header_separator'for p in proof['paths'])


def test_actual_affine_source_matrix_binds_header_vertices(tmp_path):
    _,proof=derive(source(tmp_path,bars='q .75 0 0 -.75 0 320 cm 20 40 280 1.92 re f Q'))
    assert len(proof['top_header_separators'])==1
    path=next(p for p in proof['paths']if p['classification']=='opaque_top_header_separator')
    assert path['independent_path']['PDFium_object_matrix']==[.75,0,0,-.75,0,320]
    assert path['independent_path']['PDFium_local_path_points']!=path['independent_path']['PDFium_path_points']


def test_forged_independent_object_matrix_cannot_authorize_a_header(tmp_path,monkeypatch):
    import pypdfium2 as pdfium
    from pypdfium2._helpers.pageobjects import PdfObject
    monkeypatch.setattr(PdfObject,'get_matrix',lambda self:pdfium.PdfMatrix(1,0,0,1,100,0))
    _,proof=derive(source(tmp_path,bars='0 g 15 290 210 1.44 re f'))
    assert proof['top_header_separators']==[]
