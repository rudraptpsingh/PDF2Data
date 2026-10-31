"""Synthetic source controls for exact form markers, not legal factual inputs."""
import json
import pdfplumber
import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader
from local_vector_batch.native_literal_form_rows import prove_literal_form_row


def make_source(path,label='Fax:',marker='..........',repeat=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    w=PdfWriter();p=w.add_blank_page(width=600,height=800)
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
    data=f'BT /F1 12 Tf 80 400 Td ({label})Tj ET BT /F1 12 Tf 230 400 Td ({marker})Tj ET'
    if repeat:data+=f' BT /F1 12 Tf 80 350 Td ({label})Tj ET BT /F1 12 Tf 230 350 Td ({marker})Tj ET'
    stream=DecodedStreamObject();stream.set_data(data.encode());p[NameObject('/Contents')]=w._add_object(stream);w.write(path)


def prove(path,*,changed_layout=None,tables=()):
    layout=PdfReader(path).pages[0].extract_text(extraction_mode='layout')
    with pdfplumber.open(path)as native,pdfium.PdfDocument(path)as independent:
        page=native.pages[0];view=independent[0];tp=view.get_textpage()
        try:return prove_literal_form_row(page.extract_text_lines(return_chars=True)[0],page,1,list(tables),tp,layout if changed_layout is None else changed_layout,pdfium_page=view)
        finally:tp.close();view.close()


def test_literal_blank_marker_preserved_and_never_filled(tmp_path):
    source=tmp_path/'s.pdf';make_source(source);before=source.read_bytes();result=prove(source)
    assert result['literal_marker_glyphs']=='..........'and result['field_value_inferred']is False
    assert result['source_text_modified']is False and result['source_quality_cleared_by_this_rule']is False
    assert result==json.loads(json.dumps(result))and source.read_bytes()==before


@pytest.mark.parametrize('label,marker',[('Payment:','..........'),('Fax:','123456'),('Date:','01.01.2027'),('Fax:','....1....')])
def test_real_values_and_unconfigured_labels_not_called_blank(tmp_path,label,marker):
    source=tmp_path/'s.pdf';make_source(source,label,marker)
    with pytest.raises(ValueError):prove(source)


def test_changed_independent_row_and_table_overlap_reject(tmp_path):
    source=tmp_path/'s.pdf';make_source(source)
    with pytest.raises(ValueError):prove(source,changed_layout='Fax:    .....1.....')
    with pytest.raises(ValueError):prove(source,tables=[[50,380,400,430]])


def test_identical_repeated_rows_need_equal_source_and_layout_count(tmp_path):
    source=tmp_path/'s.pdf';make_source(source,repeat=True);result=prove(source)
    assert len(result['matching_independent_layout_rows'])==2
    with pytest.raises(ValueError):prove(source,changed_layout='Fax:    ..........')
