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

@contextmanager
def contact_source(tmp_path,first_text='6th Floor, Tower B, World Trade Centre'):
 """Actual original PDF with contact grid, No./Date and later source grid."""
 from local_vector_batch.l1_review import preview_tables
 import hashlib
 p=tmp_path/'contact.pdf';w=PdfWriter();page=w.add_blank_page(612,842)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
 operations=['50 732 512 80 re S','50 760 m 562 760 l S','50 786 m 562 786 l S',
  '50 430 512 60 re S','50 460 m 562 460 l S']
 for y,text in [(795,first_text),(769,'New Delhi-110029'),(743,'Ph: Fax: 011-20904365')]:
  operations.append(f'BT /F1 11 Tf 60 {y} Td ({text}) Tj ET')
 operations+=['BT /F1 11 Tf 50 680 Td (No. L-1/246/2019/CERC) Tj ET','BT /F1 11 Tf 350 680 Td (Date: 24th December, 2019) Tj ET']
 content=DecodedStreamObject();content.set_data('\n'.join(operations).encode());page[NameObject('/Contents')]=w._add_object(content);w.write(p)
 tables=preview_tables(p,1,hashlib.sha256(p.read_bytes()).hexdigest())
 with pdfplumber.open(p)as native,pdfium.PdfDocument(p)as independent:
  pg=native.pages[0];view=independent[0];tp=view.get_textpage()
  line=next(l for l in pg.extract_text_lines(return_chars=True)if l['text'].startswith('No.'))
  layout=PdfReader(p).pages[0].extract_text(extraction_mode='layout')
  try:yield pg,view,tp,line,layout,tables
  finally:tp.close();view.close()


def test_actual_contact_grid_can_precede_metadata_without_losing_table(tmp_path):
 with contact_source(tmp_path)as(pg,view,tp,line,layout,tables):
  result=prove_header_metadata(line,pg,1,[t['bbox']for t in tables],tp,layout,pdfium_page=view,header_tables=tables)
  proof=result['preceding_contact_header_tables']
  assert len(proof)==1 and len(proof[0]['cells'])==3
  assert proof[0]['table_structure_preserved']is True
  assert result['field_semantics_reviewed']is False


@pytest.mark.parametrize('change',['missing_registry','stale_cell','unproved_reader','invalid_glyph','wrong_page','header_boundary','overlap','operative_content','not_contact','missing_slot'])
def test_prior_header_exception_rejects_stale_body_and_unproved_cells(tmp_path,change):
 with contact_source(tmp_path)as(pg,view,tp,line,layout,tables):
  tables=deepcopy(tables);boxes=[t['bbox'][:]for t in tables];page=1
  if change=='missing_registry':tables=[]
  if change=='stale_cell':tables[0]['cell_spans'][0]['text']='Different address'
  if change=='unproved_reader':tables[0]['cell_spans'][0]['readers_agree']=False
  if change=='invalid_glyph':tables[0]['cell_spans'][0]['independent_glyph_audit']['issues']=['unresolved']
  if change=='wrong_page':page=2
  if change=='header_boundary':boxes[0][3]=pg.height*.18+.01
  if change=='overlap':boxes[0][3]=line['top']+.01
  if change=='operative_content':tables[0]['cell_spans'][0]['text']='Respondents shall file'
  if change=='not_contact':tables[0]['cell_spans'][0]['text']='Example ordinary body text'
  if change=='missing_slot':tables[0]['cell_spans'].pop()
  with pytest.raises(ValueError):
   prove_header_metadata(line,pg,page,boxes,tp,layout,pdfium_page=view,header_tables=tables)


def test_dual_day_header_preserves_both_literals_without_selecting_date(tmp_path):
 with actual(tmp_path,right='Date: 15th/16th January, 2026')as(pg,tp,line,layout):
  result=prove_header_metadata(line,pg,1,[[40,200,550,300]],tp,layout)
  assert result['blocks'][1]['selected_text']=='Date: 15th/16th January, 2026'
  assert result['field_semantics_reviewed']is False and result['legal_effect_inferred']is False


def test_actual_operative_table_cannot_be_reclassified_as_contact_header(tmp_path):
 with contact_source(tmp_path,first_text='Applicants shall file at Tower B')as(pg,view,tp,line,layout,tables):
  assert all(c['readers_agree']for c in tables[0]['cell_spans'])
  with pytest.raises(ValueError,match='contact-header grammar'):
   prove_header_metadata(line,pg,1,[t['bbox']for t in tables],tp,layout,pdfium_page=view,header_tables=tables)


def test_zero_table_reference_metadata_needs_fresh_empty_inventory(tmp_path):
 with actual(tmp_path)as(pg,tp,line,layout):
  result=prove_header_metadata(line,pg,1,[],tp,layout,header_tables=[])
  assert len(result['blocks'])==2 and not result['preceding_contact_header_tables']
  with pytest.raises(ValueError):prove_header_metadata(line,pg,1,[],tp,layout)
  with pytest.raises(ValueError):prove_header_metadata(line,pg,2,[],tp,layout,header_tables=[])


def test_real_native_grid_cannot_be_hidden_by_empty_metadata_registry(tmp_path):
 with contact_source(tmp_path)as(pg,view,tp,line,layout,tables):
  with pytest.raises(ValueError,match='freshly empty'):
   prove_header_metadata(line,pg,1,[],tp,layout,pdfium_page=view,header_tables=[])
