from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from local_vector_batch import field_rules as module


def data(text='😀 संस्था चाहे पुराना या नया चुने'):
    return {'source_pdf_sha256':'a'*64,'source_package_sha256':'b'*64,
            'pages':[{'page':1,'text':text,'text_sha256':sha256(text.encode()).hexdigest()}],
            'layout':[],'structure':{'regions':[]}}


def rules(pattern=r'(?P<value>(?P<actor>संस्था) चाहे (?P<left>पुराना) (?P<connector>या) (?P<right>नया) चुने(?P<optional> absent)?)'):
    return {'schema_version':module.SCHEMA,'fields_schema_version':module.FIELDS_V2,'id':'capture','version':'1',
            'fields':[{'id':'condition','type':'text','pattern':pattern,'capture_groups':['actor','left','connector','right','optional']}]}


def test_named_captures_keep_exact_unicode_spans_and_explicit_optional_absence():
    d=data();before=deepcopy(d);r=module.compute(d,rules(),'c'*64,'regulation')
    assert r['schema_version']==module.FIELDS_V2 and r['source_package_sha256']=='b'*64
    assert r['producer_implementation']==module.implementation()and r['semantic_ownership_approved']is False
    o=r['observations'][0];assert o['captures']['actor']['value_literal']=='संस्था'
    for name,c in o['captures'].items():
        if name=='optional':assert c=={'value_literal':None,'source_span':None,'status':'not_matched'};continue
        s=c['source_span'];assert d['pages'][0]['text'][s['char_start']:s['char_start']+s['char_length']]==c['value_literal']
        assert s['offset_encoding']=='codepoint'and s['page_text_sha256']==d['pages'][0]['text_sha256']
        assert o['source_span']['char_start']<=s['char_start']<s['char_start']+s['char_length']<=o['source_span']['char_start']+o['source_span']['char_length']
    assert d==before and o['content_verified']is False and o['calculation_allowed']is False


def test_v1_default_matches_frozen_rc4_output():
    r={'schema_version':module.SCHEMA,'id':'baseline','version':'1','fields':[{'id':'months','type':'quantity','pattern':r'(?P<value>18) (?P<unit>months)'}]}
    d=data('Applicant: 18 months');d.pop('source_package_sha256')
    output=module.compute(d,r,'b'*64,'regulation')
    assert sha256(json.dumps(output,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()=='a1ba2c0299867fcee6116689bdb30904779c1ccda91eaacf408b5d850528e513'


@pytest.mark.parametrize('change',[
    {'capture_groups':['missing']},{'capture_groups':['actor','actor']},{'capture_groups':['value']},
    {'capture_groups':['bad-name']},{'capture_groups':None},{'capture_groups':['x']*33},
    {'extractor':'formulas','type':'formula'},
])
def test_capture_configuration_errors_fail_before_extraction(tmp_path,change):
    r=rules();r['fields'][0].update(change);p=tmp_path/'rules.json';p.write_text(json.dumps(r))
    with pytest.raises(ValueError):module.load(p)


def test_opt_in_required_and_lookaround_capture_cannot_escape_value(tmp_path):
    r=rules();r.pop('fields_schema_version');p=tmp_path/'rules.json';p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='opt-in'):module.load(p)
    r=rules(r'(?=(?P<actor>संस्था))(?P<value>सं)');r['fields'][0]['capture_groups']=['actor']
    with pytest.raises(ValueError,match='outside'):module.compute(data('संस्था'),r,'c'*64,'regulation')


def test_field_capture_rules_are_not_silently_ignored_by_cell_extractor(tmp_path):
    from local_vector_batch.cell_fields import load_rules
    p=tmp_path/'rules.json';p.write_text(json.dumps(rules()))
    with pytest.raises(ValueError,match='page-local'):load_rules(p)


def test_v2_bundle_rederivation_rejects_rehashed_capture_and_boolean_tampering(tmp_path,monkeypatch):
    import local_vector_batch.legal_l1 as l1
    package=tmp_path/'source';package.mkdir()
    for name in (*module.SOURCE_FILES,'original.pdf','source-receipt.json','l1-package.json'):
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name)
    r=rules();rp=tmp_path/'rules.json';rp.write_text(json.dumps(r))
    d=data();d['source_package_sha256']=module.file_sha256(package/'l1-package.json')
    result=module.compute(d,r,module.file_sha256(rp),'regulation')
    bundle=tmp_path/'bundle';bundle.mkdir();(bundle/'rules.json').write_bytes(rp.read_bytes());module.write(bundle/'fields.json',result)
    manifest={'schema_version':'pdf2data.field-bundle.v2','source_package':str(package),
              'source_files':{name:module.file_sha256(package/name)for name in (*module.SOURCE_FILES,'original.pdf','source-receipt.json','l1-package.json')},
              'files':{name:module.file_sha256(bundle/name)for name in ('rules.json','fields.json')},
              'producer_implementation':module.implementation(),'publication_eligible':False,
              'source_quality_approval_granted':False,'semantic_ownership_approved':False}
    module.write(bundle/'manifest.json',manifest)
    monkeypatch.setattr(l1,'verify_document',lambda p:None)
    def rebuild(package,rules,out,timeout=30):out.mkdir();module.write(out/'fields.json',result)
    monkeypatch.setattr(module,'extract',rebuild)
    assert module.verify(bundle)['integrity']=='passed'
    for mutation in ('capture','boolean','schema'):
        changed=deepcopy(result)
        if mutation=='capture':changed['observations'][0]['captures']['actor']['value_literal']='wrong'
        elif mutation=='boolean':changed['semantic_ownership_approved']=0
        else:changed['schema_version']=module.FIELDS_V1
        module.write(bundle/'fields.json',changed);manifest['files']['fields.json']=module.file_sha256(bundle/'fields.json');module.write(bundle/'manifest.json',manifest)
        with pytest.raises(ValueError,match='re-derivation'):module.verify(bundle)
    manifest['producer_implementation']['field_rules.py']='0'*64;module.write(bundle/'manifest.json',manifest)
    with pytest.raises(ValueError,match='implementation'):module.verify(bundle)


def test_v2_duplicate_keys_fail(tmp_path):
    p=tmp_path/'rules.json';text=json.dumps(rules());p.write_text(text.replace('"id": "capture"','"id": "capture", "id": "other"'))
    with pytest.raises(ValueError,match='Duplicate'):module.load(p)


def test_v2_protocol_schema_keeps_optional_absence_and_denies_approval():
    jsonschema=pytest.importorskip('jsonschema')
    schema=json.loads((Path(__file__).parents[1]/'schemas/configured-fields.v2.schema.json').read_text())
    result=module.compute(data(),rules(),'c'*64,'regulation')
    jsonschema.Draft202012Validator(schema).validate(result)
    for kind in ('approval','capture'):
        changed=deepcopy(result)
        if kind=='approval':changed['semantic_ownership_approved']=True
        else:changed['observations'][0]['captures']['optional']['value_literal']='invented'
        with pytest.raises(jsonschema.ValidationError):jsonschema.Draft202012Validator(schema).validate(changed)


def test_verify_cli_has_json_and_timeout_flags(monkeypatch):
    import argparse
    parser=argparse.ArgumentParser();module.configure(parser)
    args=parser.parse_args(['verify','--bundle','bundle','--timeout','5','--json'])
    monkeypatch.setattr(module,'verify',lambda path,timeout:{'bundle':str(path),'timeout':timeout})
    assert module.execute(args)=={'bundle':'bundle','timeout':5}and args.json
