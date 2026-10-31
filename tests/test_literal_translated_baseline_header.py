"""Real synthetic source controls for translated header strokes."""
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from local_vector_batch.literal_path_frames import derive_literal_path_mask,_independent_path


def source(path, *, y=758.4, scale=1):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    w=PdfWriter();p=w.add_blank_page(width=600,height=800)
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
    stream=DecodedStreamObject();stream.set_data(f'BT /F1 8 Tf 180 760 Td (Header) Tj ET q {scale} 0 0 1 80 {y} cm 0 G 0.7 w [] 0 d 0 0 m 440 0 l S Q'.encode());p[NameObject('/Contents')]=w._add_object(stream);w.write(path)


def test_translated_opaque_baseline_header_preserves_actual_path_and_matrix(tmp_path):
    p=tmp_path/'s.pdf';source(p)
    with pdfplumber.open(p)as d,pdfium.PdfDocument(p)as independent:
        n=d.pages[0];im=n.to_image(resolution=120);ink=np.asarray(im.original.convert('L'))<100
        mask,proof=derive_literal_path_mask(n,independent[0],ink,im.bbox)
        assert mask.any()and len(proof['paths'])==1
        path=proof['paths'][0];assert path['classification']=='source_header_rule_at_glyph_baseline'
        assert path['independent_path']['PDFium_object_matrix'][4:]==pytest.approx([80,758.4])
        assert path['independent_path']['PDFium_bounds'][1]>750
        assert path['rectangle_interior_authorized']is False


@pytest.mark.parametrize('change',['middle_of_glyph','scaled_stroke'])
def test_possible_strikethrough_and_scaled_stroke_still_unaccounted(tmp_path,change):
    p=tmp_path/'s.pdf';source(p,y=763 if change=='middle_of_glyph'else 758.4,scale=2 if change=='scaled_stroke'else 1)
    with pdfplumber.open(p)as d,pdfium.PdfDocument(p)as independent:
        n=d.pages[0];im=n.to_image(resolution=120);ink=np.asarray(im.original.convert('L'))<100
        _,proof=derive_literal_path_mask(n,independent[0],ink,im.bbox)
        assert proof['paths']==[]
