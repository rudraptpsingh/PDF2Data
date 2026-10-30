"""Source block proofs reject columns, stale dates and missing glyphs."""
from contextlib import contextmanager
from copy import deepcopy
import pytest
from pypdf import PdfWriter,PdfReader
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
import pdfplumber,pypdfium2 as pdfium
from local_vector_batch.native_metadata_rows import prove_header_metadata,source_blocks,load_rules

@contextmanager
def actual(tmp_path,left='No. L-1/246/2019/CERC',right='Date: 24th December, 2019',y=760):
 p=tmp_path/'source.pdf';w=PdfWriter();page=w.add_blank_page(612,842)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
 content=DecodedStreamObject();content.set_data(f'BT /F1 11 Tf 50 {y} Td ({left}) Tj ET\nBT /F1 11 Tf 350 {y} Td ({right}) Tj ET'.encode());page[NameObject('/Contents')]=w._add_object(content);w.write(p)
 with pdfplumber.open(p)as native,pdfium.PdfDocument(p)as independent:
  pg=native.pages[0];tp=independent[0].get_textpage();line=pg.extract_text_lines(return_chars=True)[0];layout=PdfReader(p).pages[0].extract_text(extraction_mode='layout')
  try:yield pg,tp,line,layout
  finally:tp.close()

def test_source_reference_and_date_are_independently_read_and_partitioned(tmp_path):
 with actual(tmp_path)as(pg,tp,line,layout):
  proof=prove_header_metadata(line,pg,1,[[40,200,550,300]],tp,layout)
  assert len(proof['blocks'])==2 and proof['source_text_modified']is False
  indexes=[m['line_glyph_index']for b in proof['blocks']for m in b['source_glyph_members']]
  assert sorted(indexes)==list(range(len(line['chars'])))
  assert proof['blocks'][0]['field_kind']=='reference_number'
  assert proof['blocks'][1]['selected_text']=='Date: 24th December, 2019'

@pytest.mark.parametrize('left,right',[
 ('Petitioners shall reply','Respondents may object'),
 ('No. 1/2024 respondents shall file','Date: 24th December, 2019'),
 ('No. L-1/246/2019/CERC','Date: subject to approval'),
 ('Date: 24th December, 2019','No. L-1/246/2019/CERC'),
 ('No. L-1/246/2019/CERC','Date:24/12/2019 and 25/12/2019')])
def test_true_columns_prose_or_multiple_dates_remain_held(tmp_path,left,right):
 with actual(tmp_path,left,right)as(pg,tp,line,layout):
  with pytest.raises(ValueError):prove_header_metadata(line,pg,1,[[40,200,550,300]],tp,layout)

@pytest.mark.parametrize('change',['page','below_header','overlapping_table','layout_digit','layout_duplicate','pdfium_digit','missing_glyph'])
def test_source_geometry_and_independent_readers_fail_closed(tmp_path,change):
 with actual(tmp_path)as(pg,tp,line,layout):
  page=1;tables=[[40,200,550,300]]
  if change=='page':page=2
  if change=='below_header':line=deepcopy(line);line['bottom']=400
  if change=='overlapping_table':tables=[[40,80,550,300]]
  if change=='layout_digit':layout=layout.replace('2019','2018')
  if change=='layout_duplicate':layout=layout+'\n'+layout
  if change=='pdfium_digit':
   original_reader=tp
   class WrongReader:
    def get_text_bounded(self,**kwargs):return original_reader.get_text_bounded(**kwargs).replace('2019','2018')
   tp=WrongReader()
  if change=='missing_glyph':line=deepcopy(line);line['chars']=line['chars'][1:]
  with pytest.raises(ValueError):prove_header_metadata(line,pg,page,tables,tp,layout)

def test_month_first_and_dotted_dates_are_configured_literal_forms(tmp_path):
 for right in ['October 05, 2017','Dated: 26.09.2024']:
  with actual(tmp_path,left='No. Eco T2/2017-CERC',right=right)as(pg,tp,line,layout):
   result=prove_header_metadata(line,pg,1,[[40,200,550,300]],tp,layout)
   assert result['blocks'][1]['selected_text']==right


def test_source_superscript_line_breaks_are_not_digit_substitution(tmp_path):
 with actual(tmp_path)as(pg,tp,line,layout):
  original_reader=tp
  class SplitOrdinal:
   def get_text_bounded(self,**kwargs):return original_reader.get_text_bounded(**kwargs).replace('24th','24\r\nth')
  proof=prove_header_metadata(line,pg,1,[[40,200,550,300]],SplitOrdinal(),layout)
  assert proof['blocks'][1]['independent_raw_text'].find('24\r\nth')>=0
  assert proof['source_text_modified']is False

def test_same_syntax_outside_configured_issuer_is_not_auto_classified(tmp_path):
 with actual(tmp_path,left='No. ABC/123/2019')as(pg,tp,line,layout):
  with pytest.raises(ValueError):prove_header_metadata(line,pg,1,[[40,200,550,300]],tp,layout)
