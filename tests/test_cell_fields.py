from copy import deepcopy
import json
from hashlib import sha256
from pathlib import Path
import pytest
from local_vector_batch.cell_fields import compute,load_rules,digest,extract,verify,implementation

def rule(**kw):return {'schema_version':'pdf2data.extraction-rules.v1','id':'test.cells','version':'1','fields':[dict(id='fee',type='quantity',pattern=r'Rs\.?\s*(?P<value>[\d,]+)\s*/(?P<unit>MW)',cell_columns=[2],**kw)]}
def data():
    text='Flat text interleaves Rs 50,000/MW and Rs 10,000/MW'
    return {'source_pdf_sha256':'a'*64,'source_package_sha256':'b'*64,'declared_family':'generic','pages':[{'page':1,'text':text,'text_sha256':sha256(text.encode()).hexdigest()}],'quality':{'results':[{'page':1,'outcome':'repair_required'}]},'tables':[{'id':'t1','page':1,'status':'needs_row_column_review','cells':[['Clause','Draft','Request'],['9.3','Rs 50,000/MW','Rs 10,000/MW']],'cell_spans':[{'id':'c1','row':1,'column':1,'text':'Rs 50,000/MW','bbox':[0,0,10,10]},{'id':'c2','row':1,'column':2,'text':'Rs 10,000/MW','bbox':[10,0,20,10]}]}]}
def test_values_never_leak_between_columns_and_source_is_not_mutated():
    source=data();before=deepcopy(source);r=compute(source,rule(),'c'*64);o=r['observations'][0]
    assert len(r['observations'])==1 and o['value_literal']=='10,000'
    assert o['typed_candidate']=={'numerator':'10000','denominator':'1'}
    assert o['unit_literal']=='MW' and o['source_quality']=='repair_required'
    assert o['cell_locator']['source_pointer']=='/tables/0/cell_spans/1'
    cell=source['tables'][0]['cell_spans'][1]['text'];s=o['cell_source_span']
    assert cell[s['char_start']:s['char_start']+s['char_length']]=='10,000'
    assert 'source_span'not in o and not o['selected_page_offset_assigned']
    assert not o['calculation_allowed'] and not o['semantic_scope_verified']
    assert source==before

def test_missing_cell_quote_on_flat_page_is_still_native_cell_evidence():
    source=data();source['pages'][0]['text']='unrelated selected text';source['pages'][0]['text_sha256']=sha256(b'unrelated selected text').hexdigest()
    assert compute(source,rule(),'c'*64)['observations'][0]['value_literal']=='10,000'

def test_null_blanks_numeric_native_values_and_continuation_rows_never_fill_down():
    source=data();source['tables'][0].pop('cell_spans');source['tables'][0]['cells']=[['9.3','Rs 50,000/MW','Rs 10,000/MW'],['',None,''],['',0,False]]
    r=compute(source,rule(),'c'*64);assert len(r['observations'])==1
    assert r['observations'][0]['cell_locator']['source_basis']=='grid_only_geometry_unavailable'
    assert r['observations'][0]['cell_locator']['bbox']is None

def test_page_table_row_filters_are_explicit_and_no_match_is_not_absence():
    for kw in [{'pages':[2]},{'table_ids':['missing']},{'cell_rows':[0]},{'families':['judgment']}]:
        result=compute(data(),rule(**kw),'c'*64);assert not result['observations']
        assert result['field_status']['fee']['status']in('not_detected_not_proven_absent','not_applicable_to_declared_family')

def test_repeat_literals_have_distinct_cell_ids_and_rules_versions():
    source=data();source['tables'][0]['cell_spans'].append({'id':'c3','row':2,'column':2,'text':'Rs 10,000/MW'})
    a=compute(source,rule(),'c'*64)['observations'];b=compute(source,rule(),'d'*64)['observations']
    assert len(a)==2 and len({o['id']for o in a+b})==4

@pytest.mark.parametrize('change',[{'cell_columns':[]},{'cell_columns':[True]},{'cell_columns':[-1]},{'cell_rows':[]},{'table_ids':[]},{'extractor':'formulas','type':'formula'},{'scope':'body_candidate'}])
def test_invalid_scope_fails(tmp_path,change):
    obj=rule();obj['fields'][0].update(change);p=tmp_path/'rules.json';p.write_text(json.dumps(obj))
    with pytest.raises(ValueError):load_rules(p)

def test_duplicate_rule_keys_rejected(tmp_path):
    p=tmp_path/'rules.json';p.write_text('{"id":"x","id":"y"}')
    with pytest.raises(ValueError):load_rules(p)

def test_bundle_reverification_detects_rehashed_output_and_changed_implementation(tmp_path,monkeypatch):
    import local_vector_batch.cell_fields as module
    package=tmp_path/'source';package.mkdir()
    for name in module.SOURCE_FILES:
        p=package/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text(name)
    path=tmp_path/'rules.json';path.write_text(json.dumps(rule()))
    def run(p,r,t):return compute(data(),load_rules(r),module.file_sha256(r)),{n:module.file_sha256(package/n)for n in module.SOURCE_FILES},implementation()
    monkeypatch.setattr(module,'_run',run)
    bundle=tmp_path/'bundle';extract(package,path,bundle);assert verify(bundle)['valid']
    content=json.loads((bundle/'cell-fields.json').read_text());content['observations'][0]['typed_candidate']['numerator']='50000';(bundle/'cell-fields.json').write_text(json.dumps(content))
    manifest=json.loads((bundle/'manifest.json').read_text());manifest['files']['cell-fields.json']=module.file_sha256(bundle/'cell-fields.json');(bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='re-derivation'):verify(bundle)
    manifest['implementation_files_sha256']['cell_fields.py']='0'*64;(bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='implementation'):verify(bundle)

def test_extraction_timeout_has_no_partial_output(tmp_path,monkeypatch):
    import subprocess
    import local_vector_batch.legal_l1 as l1
    import local_vector_batch.cell_fields as module
    package=tmp_path/'source';package.mkdir()
    for name in module.SOURCE_FILES:
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name)
    rules=tmp_path/'rules.json';rules.write_text(json.dumps(rule()));monkeypatch.setattr(l1,'verify_document',lambda p:None)
    def fail(*args,**kwargs):raise subprocess.TimeoutExpired('worker',1)
    monkeypatch.setattr(module.subprocess,'run',fail)
    output=tmp_path/'output'
    with pytest.raises(ValueError,match='time budget'):extract(package,rules,output,timeout=1)
    assert not output.exists()

def test_protocol_schema_rejects_page_span_and_approval_escalation():
    jsonschema=pytest.importorskip('jsonschema')
    schema=json.loads((Path(__file__).parents[1]/'schemas/configured-cell-fields.schema.json').read_text())
    result=compute(data(),rule(),'c'*64);jsonschema.Draft202012Validator(schema).validate(result)
    for kind in ('page_span','approval'):
        changed=deepcopy(result)
        if kind=='page_span':changed['observations'][0]['source_span']=changed['observations'][0]['cell_source_span']
        else:changed['observations'][0]['calculation_allowed']=True
        with pytest.raises(jsonschema.ValidationError):jsonschema.Draft202012Validator(schema).validate(changed)

def test_missing_optional_pdf_dependency_has_actionable_error(tmp_path,monkeypatch):
    import local_vector_batch.legal_l1 as l1
    import local_vector_batch.cell_fields as module
    def missing(path):raise ModuleNotFoundError("No module named 'pypdf'",name='pypdf')
    monkeypatch.setattr(l1,'verify_document',missing)
    with pytest.raises(ValueError,match=r'PDF2Data\[pdf\]'):module._run(tmp_path,tmp_path/'rules.json',30)
