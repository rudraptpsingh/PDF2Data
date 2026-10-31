"""Prose uses original glyph spacing, never concatenated words by guess."""
from contextlib import contextmanager
import pytest,pdfplumber,pypdfium2 as pf
from pypdf import PdfWriter,PdfReader
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from local_vector_batch.native_prose_reader import map_prose_reader

@contextmanager
def source(tmp_path,gap=0):
 p=tmp_path/'prose.pdf';writer=PdfWriter();page=writer.add_blank_page(612,842)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
 # Independent source proves raised ordinal at the exact digit advance.
 content=DecodedStreamObject();content.set_data('BT /F1 12 Tf 50 700 Td (Commission, 7) Tj ET BT /F1 8 Tf 129.344 702 Td (th) Tj ET BT /F1 12 Tf 136.016 700 Td ( Floor) Tj ET'.encode())
 page[NameObject('/Contents')]=writer._add_object(content);writer.write(p)
 with pdfplumber.open(p)as native,pf.PdfDocument(p)as other:
  pg=native.pages[0];view=other[0];tp=view.get_textpage();line=pg.extract_text_lines(return_chars=True)[0];layout=PdfReader(p).pages[0].extract_text(extraction_mode='layout')
  try:yield pg,view,tp,line,layout
  finally:tp.close();view.close()


def test_raised_ordinal_prose_reader_has_original_raw_and_geometry(tmp_path):
 with source(tmp_path)as(pg,view,tp,line,layout):
  result=map_prose_reader(line,pg,view,tp,'Commission, 7\r\nth Floor',layout)
  assert result is not None and result['mapped_text']=='Commission, 7th Floor'
  assert result['raw_text']=='Commission, 7\r\nth Floor'
  assert result['selected_text_modified']is False

@pytest.mark.parametrize('raw',['Commission, 8\r\nth Floor','Commission, 7\r\nth not Floor','Commission, 7\r\n Floor'])
def test_different_number_word_or_missing_suffix_stays_held(tmp_path,raw):
 with source(tmp_path)as(pg,view,tp,line,layout):assert map_prose_reader(line,pg,view,tp,raw,layout)is None


def test_ambiguous_independent_prose_context_stays_held(tmp_path):
 with source(tmp_path)as(pg,view,tp,line,layout):assert map_prose_reader(line,pg,view,tp,'Commission, 7\r\nth Floor',layout+'\n'+layout)is None


def test_wrong_independent_line_order_cannot_borrow_adjacent_repeated_rows(tmp_path):
 with source(tmp_path)as(pg,view,tp,line,layout):
  wrong='th Floor\nCommission, 7\nth Floor\nCommission, 7'
  assert map_prose_reader(line,pg,view,tp,'Commission, 7\r\nth Floor',wrong)is None
