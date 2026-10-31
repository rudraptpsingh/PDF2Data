"""Unexpected per-page readers never approve a page or drop its siblings."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest


def _bound(tmp_path):
 components={k:{'state':'fail'}for k in('source_identity','text_content','language','source_render','words_and_order','ordered_numbers','ordered_operators','layout')}
 return {'path':tmp_path/'synthetic-parent','manifest_sha256':'a'*64,'source_pdf_sha256':'b'*64,'profile':{'profile_sha256':'d'*64},'source_origin_profile':{},'artifact_hashes':{},'source':{'configuration':{}},'pages':{n:{'text_sha256':str(n)*64,'text':'synthetic local test'}for n in(1,2,3)},'quality':{'results':[{'page':n,'outcome':'repair_required','components':copy.deepcopy(components)}for n in(1,2,3)]}}


@pytest.mark.parametrize('failure',[RecursionError('cyclic annotation graph'),RuntimeError('reader crashed'),RuntimeError('invalid diagnostic \udc65')])
def test_page_reader_exception_is_explicit_lossless_hold_with_successful_siblings(tmp_path,monkeypatch,failure):
 from local_vector_batch import native_region_reproof as engine,targeted_reproof as api
 bound=_bound(tmp_path);monkeypatch.setattr(api,'runtime_modules',lambda runtime:{'epoch':{'tree_sha256':'c'*64}});monkeypatch.setattr(api,'verify_parent_unchanged',lambda b:None);monkeypatch.setattr(engine,'_bound',lambda *args:bound)
 def evaluate(b,origin,n,**kwargs):
  if n==2:raise failure
  quality=copy.deepcopy(b['quality']['results'][n-1]);quality['outcome']='auto_accepted'
  return {'page':n,'quality':quality}
 monkeypatch.setattr(engine,'_evaluate',evaluate)
 receipt=engine.create_region_reproof(tmp_path/'synthetic-parent',tmp_path/'synthetic-runtime',tmp_path/'synthetic-origin',[1,2,3],tmp_path/'sidecar')
 assert receipt['newly_auto_accepted_pages']==[1,3]and receipt['still_held_pages']==[2]and receipt['technical_hold_pages']==[2]
 pages=json.loads((tmp_path/'sidecar/page-proofs.json').read_text());held=pages[1]
 assert held['status']=='held'and held['failure_kind']=='technical_reader_exception'
 assert held['technical_error']['message_codepoints']==[ord(c)for c in str(failure)]
 assert held['technical_error']['source_quality_clearance']is False
 assert held['quality']==bound['quality']['results'][1]
 assert receipt['answer_publication_eligible']is False


def test_memory_error_aborts_partition_for_worker_cleanup(tmp_path,monkeypatch):
 from local_vector_batch import native_region_reproof as engine,targeted_reproof as api
 bound=_bound(tmp_path);monkeypatch.setattr(api,'runtime_modules',lambda runtime:{'epoch':{'tree_sha256':'c'*64}});monkeypatch.setattr(api,'verify_parent_unchanged',lambda b:None);monkeypatch.setattr(engine,'_bound',lambda *args:bound)
 calls=[]
 def evaluate(b,origin,n,**kwargs):
  calls.append(n)
  if n==2:raise MemoryError('synthetic capacity failure')
  return {'page':n,'quality':b['quality']['results'][n-1]}
 monkeypatch.setattr(engine,'_evaluate',evaluate)
 with pytest.raises(MemoryError):engine.create_region_reproof(tmp_path/'synthetic-parent',tmp_path/'synthetic-runtime',tmp_path/'synthetic-origin',[1,2,3],tmp_path/'sidecar')
 assert calls==[1,2]and not(tmp_path/'sidecar/reproof-version.json').exists()
 # A staging directory may preserve earlier failed candidate diagnostics; an
 # aborted partition must never leave a sealed quality/version receipt.
