"""Geometry and adversarial source tests; no legal source quality asserted."""
from copy import deepcopy
from pathlib import Path
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from local_vector_batch.native_literal_footer_paths import prove_literal_footer_paths,literal_footer_mask,footer_page_number_layout
from local_vector_batch.quality_gate import load_policy


def make_source(path, *, top=760, height=3.5, curved=False, overlap=False, colored=False, translucent=False, asymmetric=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
    stream=DecodedStreamObject()
    first=f'150 {800-top+3} 220 {800-top-3} 300 {800-top} c' if curved else f'300 {800-top} l'
    paint='0.75 0.2 0.75 rg' if colored else '0.75 g'
    if asymmetric:first=f'280 {800-top} l'
    value=f'{paint} 80 {800-top-height/2} m {first} 520 {800-top-height/2} l 300 {800-top-height} l h f'
    if translucent:
        from pypdf.generic import FloatObject
        state=DictionaryObject({NameObject('/ca'):FloatObject(.5)})
        page['/Resources'][NameObject('/ExtGState')]=DictionaryObject({NameObject('/GS'):state})
        value='/GS gs '+value
    if overlap:value+=f' 0 g BT /F1 12 Tf 280 {800-top-height/2} Td (TEXT) Tj ET'
    stream.set_data(value.encode());page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


def test_actual_independent_footer_polygon_and_pixel_mask(tmp_path):
    source=tmp_path/'source.pdf';make_source(source)
    before=source.read_bytes()
    with pdfplumber.open(source)as doc,pdfium.PdfDocument(source)as independent:
        native=doc.pages[0];proof=prove_literal_footer_paths(native,independent[0],load_policy())
        assert len(proof)==1 and proof[0]['independent_path']['fill_RGBA'][3]==255
        import json
        assert json.loads(json.dumps(proof))==proof
        im=native.to_image(resolution=144);shape=(im.original.height,im.original.width)
        mask=literal_footer_mask(native,im.bbox,shape,proof)
        assert mask.dtype==bool and mask.sum()>0
        # A rectangle interior far above the polygon is never masked.
        assert not mask[100,600] and not mask[1520,160]
        assert proof[0]['source_text_modified']is False
    assert source.read_bytes()==before


@pytest.mark.parametrize('kwargs',[{'top':400},{'height':10},{'curved':True},{'overlap':True},{'colored':True},{'translucent':True},{'asymmetric':True}])
def test_nonfooter_thick_bezier_or_text_overlap_still_hold(tmp_path,kwargs):
    source=tmp_path/'source.pdf';make_source(source,**kwargs)
    with pdfplumber.open(source)as doc,pdfium.PdfDocument(source)as independent:
        with pytest.raises(ValueError):prove_literal_footer_paths(doc.pages[0],independent[0],load_policy())


def test_changed_independent_source_does_not_match(tmp_path):
    a=tmp_path/'a.pdf';b=tmp_path/'b.pdf';make_source(a);make_source(b,height=3)
    with pdfplumber.open(a)as doc,pdfium.PdfDocument(b)as independent:
        with pytest.raises(ValueError,match='independent'):prove_literal_footer_paths(doc.pages[0],independent[0],load_policy())


def test_image_never_waived_by_footer_rule(tmp_path):
    source=tmp_path/'a.pdf';make_source(source)
    with pdfplumber.open(source)as doc,pdfium.PdfDocument(source)as independent:
        native=doc.pages[0];_ = native.objects;native._objects['image']=[{'source':'not_decoded'}]
        with pytest.raises(ValueError,match='separate source proof'):prove_literal_footer_paths(native,independent[0],load_policy())


def test_footer_layout_only_below_tables_and_exact_physical_page_number():
    line={'x0':72,'top':795,'x1':524,'bottom':807,'text':'Printed source title, 2024 9'}
    witness=footer_page_number_layout(line,595,842,9,[[70,100,525,766]])
    assert witness and witness['independent_text_numbers_operators_proved_by_this_candidate']is False
    assert footer_page_number_layout(line,595,842,8,[])is None
    assert footer_page_number_layout({**line,'top':500},595,842,9,[])is None
    assert footer_page_number_layout({**line,'x1':300},595,842,9,[])is None
    assert footer_page_number_layout(line,595,842,9,[[70,100,525,800]])is None
    assert footer_page_number_layout({**line,'text':'Printed source 19'},595,842,9,[])is None


def test_printed_page_label_kept_distinct_from_physical_page_in_actual_footer_frame():
    line={'x0':100,'x1':464,'top':733,'bottom':741,'text':'Insert name Page 3'}
    frame={'x0':93.6,'top':728.16,'x1':516.96,'bottom':745.14,'stroke':True,'fill':False,'linewidth':.706}
    witness=footer_page_number_layout(line,612,792,25,[],[frame])
    assert witness['printed_page_label']=='3' and witness['physical_page_number']==25
    assert witness['printed_label_equals_physical_page']is False
    assert footer_page_number_layout(line,612,792,25,[],[])is None
    assert footer_page_number_layout(line,612,792,25,[],[{**frame,'top':600}])is None
    assert footer_page_number_layout({**line,'text':'Printed heading'},612,792,25,[],[frame])is None
