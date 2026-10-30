"""Page-scoped reuse is exact and source/policy drift fails closed."""
import copy,hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_native_tight_cell_ownership import _pdf


def test_duplicate_or_overlapping_raw_variants_reject_before_any_paint(tmp_path,monkeypatch):
 from local_vector_batch import native_tight_cell_ownership as engine,native_tables
 source=tmp_path/'synthetic';source.write_bytes(b'bound synthetic source');page=SimpleNamespace(page_number=1,pdf=SimpleNamespace(stream=SimpleNamespace(name=str(source))))
 def forbidden(*args,**kwargs):raise AssertionError('Expensive page proof must not run on ambiguous source geometry')
 monkeypatch.setattr(engine,'audit_cells',forbidden);monkeypatch.setattr(native_tables,'find_native_tables',forbidden)
 for cells in([{'bbox':[0,0,10,10]},{'bbox':[0,0,10,10]}],[{'bbox':[0,0,10,10]},{'bbox':[5,0,15,10]}]):
  with engine.page_source_cell_context(source,1,page,hashlib.sha256(source.read_bytes()).hexdigest(),cells):
   assert engine.try_owned_cell_reader(page,None,None,[0,0,10,10],'A','A','A',{})is None


def test_cached_page_reports_equal_standalone_and_do_not_leak_mutable_glyphs(tmp_path,monkeypatch):
 import pdfplumber,pypdfium2 as pdfium
 from pypdf import PdfReader
 from local_vector_batch import native_tight_cell_ownership as engine
 source=tmp_path/'source.pdf';_pdf(source);cell={'bbox':[19,65,110,84],'selected':'6th Street'};reference=engine.audit_cells(source,1,[cell])[0];calls=[];actual=engine._prepare_ownership_page
 def prepare(*args):calls.append(1);return actual(*args)
 monkeypatch.setattr(engine,'_prepare_ownership_page',prepare)
 with pdfplumber.open(source)as native,pdfium.PdfDocument(source)as independent:
  page=native.pages[0];other=independent[0];tp=other.get_textpage()
  try:
   third=PdfReader(source).pages[0].extract_text(extraction_mode='layout')or''
   with engine.page_source_cell_context(source,1,page,hashlib.sha256(source.read_bytes()).hexdigest(),[cell],pdfium_page=other,text_page=tp,third_layout=third)as context:
    first=engine.audit_cells(source,1,[cell])[0];assert first==reference
    first['owned_characters'][0]['text']='MUTATED OUTPUT ONLY'
    assert engine.audit_cells(source,1,[cell])[0]==reference
   assert context['prepared']is None
  finally:tp.close();other.close()
 assert calls==[1]


def test_source_change_invalidates_page_context(tmp_path):
 from local_vector_batch import native_tight_cell_ownership as engine
 source=tmp_path/'source';source.write_bytes(b'immutable');page=object()
 with pytest.raises(ValueError,match='source changed'):
  with engine.page_source_cell_context(source,1,page,hashlib.sha256(source.read_bytes()).hexdigest(),[]):source.write_bytes(b'changed')


def test_proof_or_policy_epoch_change_invalidates_context(tmp_path,monkeypatch):
 from local_vector_batch import native_tight_cell_ownership as engine,proof_identity
 source=tmp_path/'source';source.write_bytes(b'immutable');state={'profile_sha256':'epoch1'};monkeypatch.setattr(proof_identity,'inspect_pdf2data_profile',lambda root:state.copy())
 with pytest.raises(ValueError,match='proof code or policy changed'):
  with engine.page_source_cell_context(source,1,object(),hashlib.sha256(source.read_bytes()).hexdigest(),[]):state['profile_sha256']='epoch2'
