from pathlib import Path
import json
import pytest
from test_benchmark import fixture_suite
from local_vector_batch.benchmark import prepare
from local_vector_batch.ollama_ocr_benchmark import Reader,prediction,run,subset_run,replay


@pytest.mark.parametrize('url',['https://example.com','http://remote:11434','http://user:password@127.0.0.1:11434','http://127.0.0.1:11434/path'])
def test_private_source_reader_cannot_send_images_to_an_external_or_credential_url(url):
    with pytest.raises(ValueError,match='loopback'):Reader(url,'model')


@pytest.mark.parametrize('reply',[
    {'done':False,'message':{'content':'partial'}},
    {'done':True,'done_reason':'length','message':{'content':'partial'}},
    {'done':True,'done_reason':'stop','message':{'content':''}}])
def test_incomplete_empty_or_capped_output_cannot_be_a_complete_prediction(reply):
    with pytest.raises(ValueError):prediction(reply)


@pytest.mark.parametrize('text',['<|im_end|>','<|im_start|>user\n<|im_end|>',
    '<|ref|>text<|/ref|><|det|>[[0, 1, 900, 999]]<|/det|>\n'])
def test_only_control_or_grounding_tokens_cannot_earn_empty_page_or_absence_credit(text):
    with pytest.raises(ValueError,match='no parsed content'):
        prediction(dict(done=True,done_reason='stop',message=dict(content=text)))


def test_only_complete_grounding_metadata_is_removed_from_prediction():
    raw='<|ref|>table<|/ref|><|det|>[[0, 1, 900, 999]]<|/det|>\n<table><tr><td>51%</td></tr></table>'
    assert prediction({'done':True,'done_reason':'stop','message':{'content':raw}}).startswith('<table>')
    incomplete='<|ref|>unfinished'
    assert prediction({'done':True,'done_reason':'stop','message':{'content':incomplete}})==incomplete


def test_source_only_inference_retains_raw_outputs_and_rejects_tampered_resume(tmp_path,monkeypatch):
    import local_vector_batch.ollama_ocr_benchmark as module
    suite=tmp_path/'suite';prepare(fixture_suite(tmp_path),suite,custom=True)
    calls=[]
    class FakeReader:
        def __init__(self,*args):self.identity={'digest':'pinned','model':'local'}
        def read(self,image,max_tokens):
            assert image.suffix=='.png' and image.read_bytes().startswith(b'\x89PNG')
            calls.append(image)
            return {'done':True,'done_reason':'stop','message':{'content':'Unverified reading'}},'request-hash',{'model_digest_after':'pinned'}
    monkeypatch.setattr(module,'Reader',FakeReader)
    out=tmp_path/'run';result=run(suite/'suite.json',out)
    assert result['errors']==0 and len(calls)==2
    saved=json.loads((out/'run.json').read_text())
    assert saved['fresh_extraction_run'] is True
    assert len(saved['cases'])==2 and all(c['review_required_pages']==[1]for c in saved['cases'])
    assert len(list(out.rglob('*-response.json')))==2
    replayed=tmp_path/'replayed';assert replay(suite/'suite.json',out,replayed)['model_calls']==0
    assert len(calls)==2
    assert json.loads((replayed/'run.json').read_text())['fresh_extraction_run'] is False
    assert run(suite/'suite.json',out,resume=True)['reused_cases']==2 and len(calls)==2
    next(out.rglob('page-0001.md')).write_text('changed')
    with pytest.raises(ValueError,match='changed'):run(suite/'suite.json',out,resume=True)
    next(out.rglob('*-response.json')).write_text('{}')
    with pytest.raises(ValueError,match='changed'):replay(suite/'suite.json',out,tmp_path/'tampered')


def test_frozen_subset_copies_only_source_identical_predictions(tmp_path,monkeypatch):
    from local_vector_batch.benchmark import sha,write
    root=fixture_suite(tmp_path);prepare(root,tmp_path/'suite',custom=True)
    suite=json.loads((tmp_path/'suite/suite.json').read_text());parent=tmp_path/'parent';parent.mkdir()
    (parent/'prediction.md').write_text('source only')
    cases=[dict(id=c['id'],pdf=c['pdf'],source_sha256=c['sha256'],status='completed',artifacts=[dict(page=1,mode='tables',path='prediction.md',sha256=sha(b'source only'))])for c in suite['cases']]
    write(parent/'run.json',dict(config={'profile':'cached'},cases=cases))
    subset_run(tmp_path/'suite/suite.json',parent,tmp_path/'subset')
    assert (tmp_path/'subset/prediction.md').read_bytes()==b'source only'
    assert json.loads((tmp_path/'subset/run.json').read_text())['fresh_extraction_run'] is False
    cases[0]['source_sha256']='0'*64;write(parent/'run.json',dict(config={'profile':'cached'},cases=cases))
    with pytest.raises(ValueError,match='source-identical'):subset_run(tmp_path/'suite/suite.json',parent,tmp_path/'invalid')
