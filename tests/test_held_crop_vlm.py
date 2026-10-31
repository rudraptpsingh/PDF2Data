import json
from pathlib import Path
import pytest
from local_vector_batch import held_crop_vlm as m

def fixture(tmp_path,monkeypatch):
 source=tmp_path/'source'/'original.pdf';source.parent.mkdir();source.write_bytes(b'PDF-source')
 folder=tmp_path/'workspace';folder.mkdir();image=folder/'crop.png';image.write_bytes(b'crop')
 monkeypatch.setattr(m,'render',lambda case:b'crop')
 case={'id':'one','kind':'cell','page':1,'bbox':[0,0,20,20],'dpi':180,'source_pdf':str(source),'source_pdf_sha256':m.sha(source.read_bytes()),'image':str(image),'image_sha256':m.sha(b'crop'),'prompt':m.PROMPTS['cell'],'max_tokens':256}
 cases=folder/'cases.json';m.save(cases,{'schema_version':m.SCHEMA,'cases':[case,{**case,'id':'two'}]})
 return source,cases,folder/'results.json'

def test_model_loads_once_and_preserves_unicode_without_approval(tmp_path,monkeypatch):
 source,cases,out=fixture(tmp_path,monkeypatch);loads=[]
 def engine():
  loads.append(1);return {'model':'fixture'},lambda c:{'text':'वि.व. 2024-25 से 2028-29','generated_tokens':12}
 r=m.run(cases,out,engine_factory=engine)
 assert len(loads)==1 and len(r['results'])==2
 assert all(x['status']=='processed_candidate'for x in r['results'])
 assert r['source_quality_clearances']==r['human_approvals']==0 and not r['publication_eligible']
 assert 'वि.व.'in r['results'][0]['text']
 m.run(cases,out,engine_factory=lambda:pytest.fail('loaded on completed resume'),resume=True)
 source.write_bytes(b'changed')
 with pytest.raises(ValueError,match='identity'):m.run(cases,out,engine_factory=engine,resume=True)

@pytest.mark.parametrize('tokens',[None,256,-1])
def test_incomplete_never_becomes_processed(tmp_path,monkeypatch,tokens):
 _,cases,out=fixture(tmp_path,monkeypatch)
 r=m.run(cases,out,engine_factory=lambda:({},lambda c:{'text':'read','generated_tokens':tokens}))
 assert all(x['status']=='incomplete_retry'for x in r['results'])

def test_failure_does_not_block_next_crop(tmp_path,monkeypatch):
 _,cases,out=fixture(tmp_path,monkeypatch)
 def infer(c):
  if c['id']=='one':raise ValueError('reader failed')
  return {'text':'second','generated_tokens':2}
 r=m.run(cases,out,engine_factory=lambda:({},infer))
 assert [x['status']for x in r['results']]==['failed_retry','processed_candidate']

def test_mid_read_source_change_rejects_candidate(tmp_path,monkeypatch):
 source,cases,out=fixture(tmp_path,monkeypatch)
 def infer(c):source.write_bytes(b'outside crop edit');return {'text':'read','generated_tokens':2}
 r=m.run(cases,out,engine_factory=lambda:({},infer))
 assert all(x['status']=='failed_retry'for x in r['results'])

def test_forged_crop_and_prompt_reject_before_loading_model(tmp_path,monkeypatch):
 _,cases,out=fixture(tmp_path,monkeypatch);d=m.load(cases);d['cases'][0]['prompt']='infer deadline';m.save(cases,d)
 with pytest.raises(ValueError,match='prompt'):m.run(cases,out,engine_factory=lambda:pytest.fail('loaded'))
 d['cases'][0]['prompt']=m.PROMPTS['cell'];m.save(cases,d);Path(d['cases'][0]['image']).write_bytes(b'forged')
 with pytest.raises(ValueError,match='identity'):m.run(cases,out,engine_factory=lambda:pytest.fail('loaded'))
