"""Synthetic protocol/hold controls; these are not source accuracy cases."""
import base64
import json
from pathlib import Path
import pytest
from test_cached_l1 import legacy,run as admit
from local_vector_batch import source_page_reading as reading
from local_vector_batch.cached_l1 import inventory
from local_vector_batch.legal_l1 import verify_document,rows


@pytest.fixture
def source(tmp_path,monkeypatch):
    old,original=legacy(tmp_path,text='Old corrupt OCR literal',status='review_required');parent=tmp_path/'parent';admit(old,original,parent)
    model=tmp_path/'model';model.mkdir();prompt=tmp_path/'prompt.txt';prompt.write_text('OCR: Transcribe printed text literally without inference.')
    profile=dict(schema_version='askmill.profile.v2',provider='mlx-vlm',ownership='worker',model='local-example',revision='sha256:'+'a'*64,capabilities=['vision'],model_path=str(model))
    profiles=dict(schema_version='askmill.profiles.v1',profiles={'pdf-ocr':profile},lease_paths=[str(tmp_path/'shared.lock')])
    state=dict(model_path=model,complete=True,text='Independent literal alpha 42\r\nSecond line',calls=[])
    def invoke(cmd,path,timeout=150):
        state['calls'].append(cmd)
        if cmd[-1]=='list':result=profiles
        elif 'doctor'in cmd:result=dict(status='configuration_valid',inference_tested=False)
        else:
            image=Path(cmd[cmd.index('--image')+1]).read_bytes();prompt_path=Path(cmd[cmd.index('--input')+1]);store=Path(cmd[cmd.index('--store')+1]);maximum=int(cmd[cmd.index('--max-tokens')+1]);timeout_=float(cmd[cmd.index('--timeout')+1])
            request=dict(prompt=prompt_path.read_text(),images=[dict(extension='.png',sha256=reading.sha(image),base64=base64.b64encode(image).decode())],max_tokens=maximum,timeout=timeout_)
            runtime={'fixture':'offline'};key=reading.sha(reading.canonical(dict(request=request,profile=profile,runtime=runtime)))
            result=dict(schema_version='askmill.result.v1',status='completed'if state['complete']else'incomplete',complete=state['complete'],text=state['text'],model=profile['model'],revision=profile['revision'],domain_acceptance='unverified',receipt_id=key,replayed=False)
            content=dict(key=key,request=request,profile=profile,runtime=runtime,result=result)
            reading.write(store/(key+'.json'),dict(content=content,sha256=reading.sha(reading.canonical(content))))
        value=dict(command=cmd,exit_code=0 if result.get('complete',True) else 3,stdout=json.dumps(result),stderr='');
        if path is not None:reading.write(path,value)
        return value
    monkeypatch.setattr(reading,'invoke',invoke)
    # Executable identity is pinned but the fake never runs it.
    monkeypatch.setattr(reading.shutil,'which',lambda _:__file__)
    return parent,prompt,state,tmp_path


def execute(source,**kwargs):
    parent,prompt,state,root=source
    return reading.run(parent,1,prompt,'pdf-ocr',root/'reading',**kwargs)


def test_literal_candidate_preserves_parent_and_all_quality_holds(source):
    parent,prompt,state,root=source;before=inventory(parent)
    result=execute(source);assert result['status']=='candidate'and not result['source_quality_approval_granted']
    assert len(state['calls'])==3 and 'doctor'in state['calls'][1]and 'run'in state['calls'][2]
    assert (root/'reading/reading.txt').read_bytes()==state['text'].encode()
    assert inventory(parent)==before
    calls_before=len(state['calls']);verified=reading.verify(root/'reading',parent)
    assert verified['complete'] is True and verified['inference_calls_performed']==0 and verified['recorded_fresh_inference_calls']==1
    assert len(state['calls'])==calls_before
    assert not (root/'reading/l1-package.json').exists()


def test_incomplete_attempt_is_retained_and_cannot_be_staged(source):
    parent,prompt,state,root=source;state['complete']=False
    result=execute(source);assert result['status']=='held_incomplete'and not result['complete']
    assert (root/'reading/result.json').exists()and list((root/'reading/store').iterdir())
    assert reading.verify(root/'reading',parent)['complete'] is False
    assert result['inference_calls_performed']==1 and result['recorded_fresh_inference_calls']==1



@pytest.mark.parametrize('options',[dict(max_tokens=2049),dict(timeout=121),dict(page=0),dict(dpi=301)])
def test_budget_and_source_boundaries_precede_inference(source,options):
    parent,prompt,state,root=source
    args=dict(package=parent,page=1,prompt=prompt,profile='pdf-ocr',output=root/'reading');args.update(options)
    with pytest.raises(ValueError):reading.run(**args)
    assert state['calls']==[]


def test_input_overlap_cannot_mutate_parent(source):
    parent,prompt,state,root=source;before=inventory(parent)
    with pytest.raises(ValueError,match='immutable output'):reading.run(parent,1,prompt,'pdf-ocr',parent/'new')
    assert inventory(parent)==before and not state['calls']


def test_mutated_result_even_when_resealed_is_not_the_raw_runner_result(source):
    parent,prompt,state,root=source;execute(source);bundle=root/'reading';result=reading.read(bundle/'result.json');result['text']='invented reading';(bundle/'result.json').write_text(json.dumps(result));(bundle/'reading.txt').write_bytes(result['text'].encode());manifest=reading.read(bundle/'manifest.json');files=inventory(bundle);files.pop('manifest.json');manifest['files']=files;(bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='invocation/result'):reading.verify(bundle,parent)


def test_profiles_require_owned_local_worker_and_existing_shared_leases(tmp_path):
    model=tmp_path/'model';model.mkdir();p=dict(provider='mlx-vlm',ownership='worker',model_path=str(model),capabilities=['vision'],revision='pinned')
    for changes in(dict(provider='ollama'),dict(ownership='external'),dict(model_path=str(tmp_path/'missing'))):
        with pytest.raises(ValueError):reading.profile_snapshot(dict(schema_version='askmill.profiles.v1',profiles={'pdf-ocr':{**p,**changes}},lease_paths=[str(tmp_path/'shared')]),'pdf-ocr')
    with pytest.raises(ValueError,match='shared'):reading.profile_snapshot(dict(schema_version='askmill.profiles.v1',profiles={'pdf-ocr':p}),'pdf-ocr')


def test_output_cannot_write_into_pinned_model_directory(source):
    parent,prompt,state,root=source;out=state['model_path']/'receipt'
    with pytest.raises(ValueError,match='immutable output'):
        reading.run(parent,1,prompt,'pdf-ocr',out)
    assert not out.exists() and len(state['calls'])==1


def test_native_ocr_task_prompt_is_supported_without_appended_instructions(source):
    parent,prompt,state,root=source;prompt.write_text('OCR:')
    assert execute(source)['complete'] is True
    assert (root/'reading/prompt.txt').read_bytes()==b'OCR:'


def test_receipt_identity_cannot_follow_an_external_path(source):
    parent,prompt,state,root=source;execute(source);bundle=root/'reading'
    result=reading.read(bundle/'result.json');result['receipt_id']='../../other'
    (bundle/'result.json').write_text(json.dumps(result))
    call=reading.read(bundle/'invocations/run.json');call['stdout']=json.dumps(result);(bundle/'invocations/run.json').write_text(json.dumps(call))
    manifest=reading.read(bundle/'manifest.json');files=inventory(bundle);files.pop('manifest.json');manifest['files']=files;(bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='receipt identity'):reading.verify(bundle,parent)
