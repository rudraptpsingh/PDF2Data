"""Portable source-coordinate tests; no corpus files, network, OCR or models."""
from types import SimpleNamespace
import math
import pytest
from local_vector_batch.native_table_cell_spacing import native_box_to_pdf_box,read_source_box


def test_four_declared_rotation_inverses_have_exact_expected_frames():
    expected={0:[20.,240.,80.,280.],90:[40.,20.,80.,80.],180:[160.,40.,220.,80.],270:[160.,240.,200.,300.]}
    for rotation,want in expected.items():
        size=(320,240)if rotation in(90,270)else(240,320)
        assert native_box_to_pdf_box([20,40,80,80],rotation=rotation,raw_pdf_bbox=[0,0,240,320],native_size=size)==want


def test_invalid_or_unsupported_coordinate_frames_never_guess():
    base=dict(rotation=0,raw_pdf_bbox=[0,0,240,320],native_size=[240,320])
    cases=[dict(rotation=45),dict(rotation=90.0),dict(raw_pdf_bbox=[5,0,245,320]),dict(native_origin=[1,0]),dict(native_size=[320,240]),dict(raw_pdf_bbox=[0,0,0,320])]
    for change in cases:
        with pytest.raises(ValueError):native_box_to_pdf_box([20,40,80,80],**{**base,**change})
    for box in([20,40,20,80],[-1,40,80,80],[20,40,241,80],[20,40,float('nan'),80],[20,40,80,float('inf')]):
        with pytest.raises(ValueError):native_box_to_pdf_box(box,**base)


def _source(path,rotation):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,NumberObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=240,height=320)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 37 214 Td (ALPHA123) Tj ET\nBT /F1 12 Tf 145 55 Td (BETA456) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream);page[NameObject('/Rotate')]=NumberObject(rotation)
    with path.open('wb')as f:writer.write(f)


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_actual_native_glyph_boxes_retrieve_same_independent_pdfium_word(tmp_path,rotation):
    import pdfplumber
    import pypdfium2 as pdfium
    path=tmp_path/f'rotation-{rotation}.pdf';_source(path,rotation)
    with pdfplumber.open(path)as native,pdfium.PdfDocument(path)as doc:
        page=native.pages[0];other=doc[0];tp=other.get_textpage()
        try:
            chars=page.chars[:8];assert ''.join(c['text']for c in chars)=='ALPHA123'
            box=[max(0,min(c['x0']for c in chars)-1),max(0,min(c['top']for c in chars)-1),min(page.width,max(c['x1']for c in chars)+1),min(page.height,max(c['bottom']for c in chars)+1)]
            value,proof=read_source_box(page,other,tp,box)
            assert value.strip()=='ALPHA123'
            assert proof['page_rotation_degrees']==rotation
            assert proof['text_modified']is False and proof['cell_or_page_approval']is False
            if rotation==90:
                old=tp.get_text_bounded(left=box[0],bottom=page.height-box[3],right=box[2],top=page.height-box[1])
                assert old.strip()!='ALPHA123'
        finally:tp.close();other.close()


def test_mismatched_independent_metadata_and_conflicting_readings_remain_unmodified():
    native=SimpleNamespace(rotation=0,width=240,height=320,bbox=(0,0,240,320))
    page=SimpleNamespace(get_rotation=lambda:90,get_size=lambda:(240,320),get_bbox=lambda:(0,0,240,320))
    reader=SimpleNamespace(get_text_bounded=lambda **kw:'NOT 50%')
    with pytest.raises(ValueError):read_source_box(native,page,reader,[20,40,80,80])
    page.get_rotation=lambda:0
    text,proof=read_source_box(native,page,reader,[20,40,80,80]);assert text=='NOT 50%'
    assert proof['cell_or_page_approval']is False


def _glyphs(text,*,gap=0):
    result=[];x=10.
    for char in text:
        result.append({'text':char,'x0':x,'x1':x+5,'top':10.,'bottom':20.,'size':10.});x+=5+gap
    return result


def _cell_page(glyphs,lines=()):
    return SimpleNamespace(crop=lambda box:SimpleNamespace(chars=glyphs,extract_text_lines=lambda **kw:list(lines)))


def test_cell_control_mapping_requires_real_glyphs_and_unique_third_context():
    from local_vector_batch.native_table_cell_spacing import map_cell_reader
    page=_cell_page(_glyphs('Annexure-III'))
    proof=map_cell_reader(page,None,None,[0,0,100,30],'Annexure-\nIII','Annexure\x02III','third Annexure-III context')
    assert proof['status']=='source_proved'and proof['mapped_text']=='Annexure-III'
    assert proof['raw_text']=='Annexure\x02III'and proof['selected_text_modified']is False
    for third in('AnnexureIII','Annexure-III and Annexure-III'):
        actual=map_cell_reader(page,None,None,[0,0,100,30],'Annexure-III','Annexure\x02III',third)
        assert actual['mapped_text']=='Annexure\x02III'and actual['repairs']==[]


def test_multiline_control_mapping_requires_every_control_proved_on_source_lines(monkeypatch):
    from local_vector_batch import native_table_cell_spacing as module
    glyphs=_glyphs('underAnnexure-III');line={'x0':20,'top':10,'x1':70,'bottom':20,'chars':_glyphs('Annexure-')}
    page=_cell_page(glyphs,[line]);monkeypatch.setattr(module,'read_source_box',lambda *a:('Annexure\x02',{'bbox':'actual_geometry'}))
    proof=module.map_cell_reader(page,None,None,[0,0,150,30],'under\nAnnexure-\nIII','under\r\nAnnexure\x02III','under othercolumn Annexure- anothercolumn III')
    assert proof['status']=='source_proved'
    assert proof['repairs'][0]['line_witnesses'][0]['source_rectangle']=={'bbox':'actual_geometry'}
    # One unproved control cannot be waved through by a different proved line.
    bad=module.map_cell_reader(page,None,None,[0,0,150,30],'underAnnexure-III','under\x02Annexure\x02III','Annexure-')
    assert bad['repairs']==[]


def test_cell_spacing_reuses_source_word_boundary_proof_and_retains_raw():
    from local_vector_batch.native_table_cell_spacing import map_cell_reader
    page=_cell_page(_glyphs('7th August'))
    result=map_cell_reader(page,None,None,[0,0,150,30],'7th August','7\r\nth August','unique 7th August context')
    assert result['status']=='source_proved'and result['mapped_text']=='7th August'
    assert result['raw_text']=='7\r\nth August'
    assert result['repairs'][0]['kind']=='source_geometry_bound_cell_reader_spacing'
    assert result['repairs'][0]['boundaries'][0]['source_gap_points']==0


@pytest.mark.parametrize('glyphs,selected,raw,third',[
    (_glyphs('1234',gap=4),'1234','12 34','1234'),
    (_glyphs('12 34'),'1234','12 34','1234'),
    (_glyphs('1234'),'1234','12 43','1234'),
    (_glyphs('1234'),'1234','12 34','1234 and1234'),
])
def test_real_word_number_gaps_and_ambiguous_contexts_never_compact(glyphs,selected,raw,third):
    from local_vector_batch.native_table_cell_spacing import map_cell_reader
    result=map_cell_reader(_cell_page(glyphs),None,None,[0,0,150,30],selected,raw,third)
    assert result['mapped_text']==raw and result['repairs']==[]


def test_ordered_source_cell_lines_allow_interleaved_columns_but_not_ambiguous_context():
    from local_vector_batch.native_table_cell_spacing import unique_cell_context
    lines=[{'text':'7th August, 2020'},{'text':'(Friday)'}]
    third='7th August, 2020  OTHER COLUMN\n(Friday)  OTHER COLUMN\n later7th August, 2020'
    result=unique_cell_context('7th August, 2020\n(Friday)',lines,third)
    assert result and len(result['line_spans'])==2
    assert third[slice(*result['line_spans'][1])]=='(Friday)'
    assert unique_cell_context('7th August, 2020\n(Friday)',lines,'7th August, 2020 7th August, 2020 (Friday)')is None
    assert unique_cell_context('7th August, 2020\n(Friday)',lines,third,max_context_chars=8)is None


def test_control_count_cannot_substitute_for_the_exact_control_glyph_position(monkeypatch):
    from local_vector_batch import native_table_cell_spacing as module
    line={'x0':10,'top':10,'x1':35,'bottom':20,'chars':_glyphs('LEFT-')}
    page=_cell_page(_glyphs('LEFT-RIGHT-'),[line])
    monkeypatch.setattr(module,'read_source_box',lambda *a:('LEFT\x02',{'bbox':'left-line-only'}))
    result=module.map_cell_reader(page,None,None,[0,0,150,30],'LEFT-RIGHT-',
                                 'LEFT-RIGHT\x02','LEFT- other-column RIGHT-')
    assert result['mapped_text']=='LEFT-RIGHT\x02'and result['repairs']==[]
