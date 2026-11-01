from copy import deepcopy
import json
from pathlib import Path
import pytest
from local_vector_batch import field_witness as m


def request():
    return dict(source_pdf_sha256='a'*64, source_package_sha256='b'*64, native_fields_sha256='c'*64,
                regions=[dict(id='hi', image_sha256='d'*64)], fields=[dict(id='title', region_id='hi',
                native_field_handle={'json_pointer':'/observations/0'}, native_observation_id='obs1',
                native_value_literal='corrupt title', native_source_span={'page':1}, native_geometry=None,
                native_source_quality={'repair_required':True}, selector={'kind':'between','prefix':'Title: ','suffix':' END'},
                required_tokens=['(संशोधन)'])])


def reading(identity, text='Title: नियम (संशोधन) END', state='complete'):
    return dict(reader_id=identity, reader_fingerprint=m.digest(identity), attestation='operator_import_not_inference_verified',
                regions=[dict(region_id='hi',text=text,state=state)])


def test_correction_retains_native_evidence_and_never_approves():
    r=request(); original=deepcopy(r)
    v=m.derive_comparison(r,[reading('a'),reading('b')],['a','b']); row=v['fields'][0]
    assert r==original and row['state']=='consensus_correction_candidate'
    assert row['proposed_literal']=='नियम (संशोधन)' and row['native_value_literal']=='corrupt title'
    assert row['native_source_quality']=={'repair_required':True}
    assert not row['selected_page_offset_assigned']
    assert all(row[k] is False and v[k] is False for k in m.FLAGS)


@pytest.mark.parametrize('text,state', [('Title: नियम END','complete'),('Title: नियम (संशोधन) END','incomplete'),('Title: नियम (संशोधन) END Title: नियम (संशोधन) END','complete')])
def test_agreement_cannot_hide_missing_qualifier_truncation_or_ambiguity(text,state):
    v=m.derive_comparison(request(),[reading('a',text,state),reading('b',text,state)],['a','b'])
    assert v['fields'][0]['state']=='reader_conflict_or_incomplete'
    assert v['fields'][0]['proposed_literal'] is None


def test_conflicting_readers_and_missing_reader():
    rows=[reading('a'),reading('b','Title: अन्य (संशोधन) END')]
    assert m.derive_comparison(request(),rows,['a','b'])['fields'][0]['proposed_literal'] is None
    with pytest.raises(ValueError,match='unavailable'): m.derive_comparison(request(),rows,['a','c'])
    rows[1]['reader_fingerprint']=rows[0]['reader_fingerprint']
    with pytest.raises(ValueError,match='distinct reader'): m.derive_comparison(request(),rows,['a','b'])


def test_selector_offsets_reopen_exact_local_text_and_preserve_negation():
    text='\n  Hello not ₹5 / MW\n'; value=m.select(text,{'kind':'first_line'})[0]
    assert text[value['char_start']:value['char_start']+value['char_length']]==value['value_literal']=='Hello not ₹5 / MW'
    assert m.normalized('A\n B')=='A B' and m.normalized('not 5 MW')!=m.normalized('5 MW')


def setup_import(tmp_path, monkeypatch):
    req=tmp_path/'request';req.mkdir();m.write(req/'request.json',request())
    monkeypatch.setattr(m,'checked_request',lambda p:request())
    m.write(req/'manifest.json',{'metadata':{'source_package':str(tmp_path/'native-package'),'field_bundle':str(tmp_path/'native-fields')}})
    source=tmp_path/'source.json';meta=tmp_path/'meta.json'
    source.write_text(json.dumps(dict(request_sha256=m.file_sha256(req/'request.json'),regions=[dict(region_id='hi',image_sha256='d'*64,text='Title: नियम (संशोधन) END',complete=True)]),indent=2))
    meta.write_text(json.dumps(dict(reader_id='external',engine='tesseract.js',version='7.0.0',source_text_provided=False),indent=2))
    return req,source,meta


def test_external_import_retains_original_bytes_and_replays(tmp_path,monkeypatch):
    req,source,meta=setup_import(tmp_path,monkeypatch);out=tmp_path/'out'
    m.import_reading(req,source,meta,out)
    assert (out/'external-readings.json').read_bytes()==source.read_bytes()
    assert (out/'external-metadata.json').read_bytes()==meta.read_bytes()
    assert m.checked_reading(out,req,request())['attestation']=='operator_import_not_inference_verified'


@pytest.mark.parametrize('mutation',['wrong_image','wrong_request','duplicate','nonblind'])
def test_external_import_rejects_wrong_pins_and_nonblind_readings(tmp_path,monkeypatch,mutation):
    req,source,meta=setup_import(tmp_path,monkeypatch);v=m.load(source);md=m.load(meta)
    if mutation=='wrong_image':v['regions'][0]['image_sha256']='e'*64
    if mutation=='wrong_request':v['request_sha256']='e'*64
    if mutation=='duplicate':v['regions']*=2
    if mutation=='nonblind':md['source_text_provided']=True
    m.write(source,v);m.write(meta,md);out=tmp_path/'out'
    with pytest.raises(ValueError):m.import_reading(req,source,meta,out)
    assert not out.exists()


def test_rehashed_comparison_still_fails_replay(tmp_path,monkeypatch):
    req,source,meta=setup_import(tmp_path,monkeypatch);a=tmp_path/'a';b=tmp_path/'b';out=tmp_path/'result'
    m.import_reading(req,source,meta,a)
    md=m.load(meta);md.update(reader_id='second',engine='other');m.write(meta,md)
    m.import_reading(req,source,meta,b)
    m.compare(req,[a,b],['external','second'],out)
    assert m.verify(out)['valid']
    v=m.load(out/'comparison.json');v['fields'][0]['proposed_literal']='invented';m.write(out/'comparison.json',v)
    manifest=m.load(out/'manifest.json');manifest['files']['comparison.json']=m.file_sha256(out/'comparison.json');m.write(out/'manifest.json',manifest)
    with pytest.raises(ValueError,match='replay'):m.verify(out)


def test_bundle_symlink_rejected(tmp_path):
    bundle=tmp_path/'bundle';bundle.mkdir();target=tmp_path/'target';target.write_text('{}')
    try: (bundle/'manifest.json').symlink_to(target)
    except OSError: pytest.skip('host does not permit symlink creation')
    with pytest.raises(ValueError,match='regular files'):m.check_files(bundle)


def test_ollama_blind_request_truncation_and_local_identity(tmp_path,monkeypatch):
    from local_vector_batch import ollama_ocr_benchmark
    req=tmp_path/'request';req.mkdir();r=request();r['regions'][0]['image_file']='region.png'
    (req/'region.png').write_bytes(b'pixels');r['regions'][0]['image_sha256']=m.file_sha256(req/'region.png')
    m.write(req/'request.json',r);monkeypatch.setattr(m,'checked_request',lambda p:r)
    m.write(req/'manifest.json',{'metadata':{'source_package':str(tmp_path/'native-package'),'field_bundle':str(tmp_path/'native-fields')}})
    calls=[]
    class FakeReader:
        def __init__(self,host,model,timeout):self.identity={'digest':'x'*64,'model':model}
        def model_digest(self):return 'x'*64
        def request(self,path,body):
            calls.append((path,body))
            if path=='/api/show':return {'capabilities':['vision']}
            return {'done':True,'done_reason':'length','message':{'content':'partial text'}}
    monkeypatch.setattr(ollama_ocr_benchmark,'Reader',FakeReader)
    out=tmp_path/'out';result=m.read_ollama(req,out,'http://127.0.0.1:11434','local')
    assert result['incomplete']==1
    body=calls[-1][1]
    assert body['messages'][0]['content']==m.PROMPT and 'corrupt title' not in json.dumps(body)
    assert m.checked_reading(out,req,r)['regions'][0]['state']=='incomplete'
    with pytest.raises(ValueError,match='local vision model'):m.read_ollama(req,tmp_path/'cloud','http://127.0.0.1:11434','model-cloud')


def test_ollama_rejects_model_change_without_committing_output(tmp_path,monkeypatch):
    from local_vector_batch import ollama_ocr_benchmark
    req=tmp_path/'request';req.mkdir();r=request();r['regions'][0]['image_file']='region.png'
    (req/'region.png').write_bytes(b'pixels');r['regions'][0]['image_sha256']=m.file_sha256(req/'region.png');m.write(req/'request.json',r)
    monkeypatch.setattr(m,'checked_request',lambda p:r)
    m.write(req/'manifest.json',{'metadata':{'source_package':str(tmp_path/'native-package'),'field_bundle':str(tmp_path/'native-fields')}})
    class FakeReader:
        def __init__(self,*args):self.identity={'digest':'x'*64}
        def model_digest(self):return 'y'*64
        def request(self,path,body):
            if path=='/api/show':return {'capabilities':['vision']}
            return {'done':True,'done_reason':'stop','message':{'content':'text'}}
    monkeypatch.setattr(ollama_ocr_benchmark,'Reader',FakeReader)
    out=tmp_path/'out'
    with pytest.raises(ValueError,match='digest changed'):m.read_ollama(req,out,'http://127.0.0.1:11434','local')
    assert not out.exists()


def test_import_cannot_write_inside_original_package(tmp_path,monkeypatch):
    req,source,meta=setup_import(tmp_path,monkeypatch)
    native=tmp_path/'native-package';native.mkdir()
    with pytest.raises(ValueError,match='immutable inputs'):
        m.import_reading(req,source,meta,native/'new')
    assert not (native/'new').exists()
