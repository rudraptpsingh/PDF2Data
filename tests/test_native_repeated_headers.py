from contextlib import contextmanager
from copy import deepcopy
import pytest,pdfplumber,pypdfium2 as pf
from pypdf import PdfWriter,PdfReader
from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
from local_vector_batch.native_repeated_headers import prove_repeated_source_header

@contextmanager
def actual(tmp_path,*,title='SOURCE PUBLICATION HEADER FOR TESTS',second_title=None,y=800,number_offset=0,numbers=None):
 p=tmp_path/'headers.pdf';w=PdfWriter()
 for n in range(1,4):
  pg=w.add_blank_page(612,842);font=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}));pg[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
  text=second_title if n!=2 and second_title is not None else title
  printed=numbers[n-1]if numbers is not None else n+number_offset
  content=DecodedStreamObject();content.set_data((f'BT /F1 9 Tf 40 {y} Td ({printed}) Tj ET BT /F1 9 Tf 120 {y} Td ({text}) Tj ET BT /F1 9 Tf 450 {y} Td ([PART IV]) Tj ET BT /F1 12 Tf 50 600 Td (Body clause {n} shall remain subject to source review.) Tj ET').encode());pg[NameObject('/Contents')]=w._add_object(content)
 w.write(p)
 with pdfplumber.open(p)as pdf,pf.PdfDocument(p)as other:
  native=pdf.pages[1];view=other[1];tp=view.get_textpage();line=next(l for l in native.extract_text_lines(return_chars=True)if l['text'].startswith(str(numbers[1]if numbers is not None else 2+number_offset)+' '));layout=PdfReader(p).pages[1].extract_text(extraction_mode='layout')
  try:yield line,native,view,tp,layout
  finally:tp.close();view.close()

def prove(args):
 line,native,view,tp,layout=args
 return prove_repeated_source_header(line,native,2,[],tp,layout,pdfium_page=view,header_tables=[])


def test_repeated_header_keeps_every_printed_page_literal_and_reference_scope(tmp_path):
 with actual(tmp_path)as args:
  result=prove(args)
  assert result['physical_page_number_sequence_observed']is True
  assert result['target']['printed_page_number_literal']=='2'
  assert result['references'][0]['printed_page_number_literal']in('1','3')
  assert result['reference_body_quality_promoted']is False and result['text_removed']is False


def test_source_page_number_offset_is_observed_not_assumed_identity(tmp_path):
 with actual(tmp_path,number_offset=20)as args:
  result=prove(args);assert result['target']['printed_page_number_literal']=='22'

@pytest.mark.parametrize('options',[
 {'title':'THE LICENSEE SHALL SUBMIT WITHIN THIRTY DAYS'},
 {'title':'DUTIES'},
 {'y':500},
 {'second_title':'DIFFERENT SOURCE PUBLICATION HEADER'},
])
def test_body_columns_short_split_clauses_or_nonrepeated_content_remain_held(tmp_path,options):
 with actual(tmp_path,**options)as args:
  with pytest.raises(ValueError):prove(args)


def test_changed_digit_or_duplicate_independent_layout_row_rejected(tmp_path):
 with actual(tmp_path)as args:
  line,native,view,tp,layout=args
  for changed in(layout.replace('SOURCE','S0URCE'),layout+'\n'+layout):
   with pytest.raises(ValueError):prove((line,native,view,tp,changed))


def test_table_overlap_and_legacy_header_font_never_become_furniture(tmp_path):
 with actual(tmp_path)as args:
  line,native,view,tp,layout=args
  with pytest.raises(ValueError):prove_repeated_source_header(line,native,2,[[40,30,550,100]],tp,layout,pdfium_page=view)
  line=deepcopy(line)
  for char in line['chars']:char['fontname']='ABCDEF+Devnagari-ChanakyaNormalA'
  with pytest.raises(ValueError,match='font'):prove((line,native,view,tp,layout))


def test_repeated_number_is_not_an_observed_page_sequence(tmp_path):
 with actual(tmp_path,numbers=[8,8,8])as args:
  with pytest.raises(ValueError):prove(args)
