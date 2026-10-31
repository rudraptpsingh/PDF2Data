from contextlib import contextmanager
from copy import deepcopy
import hashlib
import pytest,pdfplumber,pypdfium2 as pf
from pypdf import PdfWriter,PdfReader
from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
from local_vector_batch.native_blank_form_fields import prove_blank_form_header

@contextmanager
def actual(tmp_path,left='Name of the Petitioner',right='____________________',y=760):
 from local_vector_batch.l1_review import preview_tables
 from local_vector_batch.native_table_feedback import validate_feedback
 p=tmp_path/'blank-form.pdf';w=PdfWriter();pg=w.add_blank_page(612,842);font=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}));pg[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
 content=DecodedStreamObject();content.set_data((f'BT /F1 11 Tf 50 {y} Td ({left}) Tj ET BT /F1 11 Tf 350 {y} Td ({right}) Tj ET\n40 542 510 100 re S 40 592 m 550 592 l S 295 542 m 295 642 l S\nBT /F1 11 Tf 50 620 Td (A) Tj ET BT /F1 11 Tf 310 620 Td (B) Tj ET BT /F1 11 Tf 50 570 Td (C) Tj ET BT /F1 11 Tf 310 570 Td (D) Tj ET').encode());pg[NameObject('/Contents')]=w._add_object(content);w.write(p);source_sha=hashlib.sha256(p.read_bytes()).hexdigest();tables=preview_tables(p,1,source_sha)
 with pdfplumber.open(p)as pdf,pf.PdfDocument(p)as other:
  native=pdf.pages[0];view=other[0];tp=view.get_textpage();line=next(l for l in native.extract_text_lines(return_chars=True)if l['top']<180 and len(l['text'])>10)
  raw=[{**deepcopy(t),'cells':native.find_tables()[i].extract(),'extraction_basis':'native_pdf_geometry'}for i,t in enumerate(tables)]
  cells=validate_feedback({'source_pdf_sha256':source_sha,'page':1,'page_text_sha256':'synthetic','tables':tables,'raw_tables':raw,'fresh_quality':{'results':[{'components':{}}]}},[],{})
  layout=PdfReader(p).pages[0].extract_text(extraction_mode='layout')
  try:yield line,native,view,tp,layout,tables,cells['tables']
  finally:tp.close();view.close()


def prove(args):
 line,native,view,tp,layout,tables,proofs=args
 return prove_blank_form_header(line,native,1,[t['bbox']for t in tables],tp,layout,pdfium_page=view,header_tables=tables,table_proofs=proofs)

@pytest.mark.parametrize('label',['Name of the Petitioner','Name of the Integrated Mine:'])
def test_independently_proved_blank_marker_never_creates_filled_fact(tmp_path,label):
 with actual(tmp_path,left=label)as args:
  result=prove(args)
  assert result['field_value']is None and result['field_state']=='unfilled_literal_underscores'
  assert result['blocks'][1]['selected_text']=='____________________'
  assert result['field_semantics_reviewed']is False

@pytest.mark.parametrize('left,right',[('Name of the Petitioner','ACME'),('Name of the Integrated Mine:','__5__'),('Rate of return','____________________')])
def test_filled_values_numbers_and_unconfigured_body_labels_stay_held(tmp_path,left,right):
 with actual(tmp_path,left,right)as args:
  with pytest.raises(ValueError):prove(args)

@pytest.mark.parametrize('change',['missing_table_proof','stale_cell','overlap','body_band','missing_glyph','changed_independent_blank'])
def test_fresh_table_and_all_original_source_blocks_are_required(tmp_path,change):
 with actual(tmp_path)as args:
  line,native,view,tp,layout,tables,proofs=args;line=deepcopy(line);tables=deepcopy(tables);proofs=deepcopy(proofs)
  if change=='missing_table_proof':proofs=[]
  if change=='stale_cell':proofs[0]['cells'][0]['selected_cell_sha256']='wrong'
  if change=='overlap':tables[0]['bbox'][1]=line['top']
  if change=='body_band':line['bottom']=native.height*.25+.01
  if change=='missing_glyph':line['chars']=line['chars'][1:]
  if change=='changed_independent_blank':layout=layout.replace('____________________','___________________')
  with pytest.raises(ValueError):prove((line,native,view,tp,layout,tables,proofs))
