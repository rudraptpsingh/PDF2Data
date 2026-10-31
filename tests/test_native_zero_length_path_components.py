"""Independent synthetic path representation controls, not legal source gold."""
import json
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from local_vector_batch.native_zero_length_path_components import prove_rectangle_corner_point
from local_vector_batch.native_literal_footer_paths import literal_footer_mask


def make_source(path, *, point='80 60', linked=True, alpha=False, width=.7):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject,FloatObject
    w=PdfWriter();p=w.add_blank_page(width=600,height=800);stream=DecodedStreamObject()
    data=f'{width} w 1 J 0 G 80 40 m 520 40 l 520 60 l 80 60 l 80 40 l h '
    data+=f'{point} m h S'if linked else f'S {point} m h S'
    if alpha:
        p[NameObject('/Resources')]=DictionaryObject({NameObject('/ExtGState'):DictionaryObject({NameObject('/GS'):DictionaryObject({NameObject('/CA'):FloatObject(.5)})})})
        data='/GS gs '+data
    stream.set_data(data.encode());p[NameObject('/Contents')]=w._add_object(stream);w.write(path)


def test_actual_parser_split_point_requires_unique_retained_independent_rectangle(tmp_path):
    source=tmp_path/'s.pdf';make_source(source)
    with pdfplumber.open(source)as native,pdfium.PdfDocument(source)as independent:
        page=native.pages[0];assert len(page.curves)==1 and len(page.rects)==1
        proof=prove_rectangle_corner_point(page,independent[0],page.curves[0])
        assert proof['ink_mask_pixels_added']==0 and proof['rectangle_bbox']==[80,740,520,760]
        assert proof==json.loads(json.dumps(proof))
        assert not literal_footer_mask(page,(0,0,600,800),(800,600),[proof]).any()


@pytest.mark.parametrize('kwargs',[{'point':'81 60'},{'linked':False},{'alpha':True},{'width':2}])
def test_point_not_matching_rectangle_corner_or_opaque_bound_path_holds(tmp_path,kwargs):
    source=tmp_path/'s.pdf';make_source(source,**kwargs)
    with pdfplumber.open(source)as native,pdfium.PdfDocument(source)as independent:
        with pytest.raises(ValueError):prove_rectangle_corner_point(native.pages[0],independent[0],native.pages[0].curves[0])


def test_changed_rectangle_source_rejects(tmp_path):
    a=tmp_path/'a.pdf';b=tmp_path/'b.pdf';make_source(a);make_source(b,width=.8)
    with pdfplumber.open(a)as native,pdfium.PdfDocument(b)as independent:
        with pytest.raises(ValueError):prove_rectangle_corner_point(native.pages[0],independent[0],native.pages[0].curves[0])
