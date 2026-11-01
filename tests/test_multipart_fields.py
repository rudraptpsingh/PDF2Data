from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from local_vector_batch import field_rules as fields
from local_vector_batch.legal_l1 import structure_document, extract_document


def data(*texts):
    pages=[dict(page=i,text=t,text_sha256=sha256(t.encode()).hexdigest())for i,t in enumerate(texts,1)]
    return dict(source_pdf_sha256='a'*64,source_package_sha256='b'*64,pages=pages,layout=[],tables=[],
        structure=structure_document(pages,'a'*64,'draft'),quality=dict(results=[dict(page=i,outcome='repair_required'if i==2 else'auto_accepted')for i in range(1,len(pages)+1)]))


def rules(pattern=r'(?P<value>COD within (?P<duration>two months) from the date of[\s\S]{0,100}?(?P<trigger>effectiveness of GNA))'):
    return dict(schema_version=fields.SCHEMA,fields_schema_version=fields.FIELDS_V3,id='multipart-test',version='1',
        fields=[dict(id='cod',type='text',scope='native_unit_candidate',pattern=pattern,capture_groups=['duration','trigger'])])


def run(d,r=None):return fields.compute(d,r or rules(),'c'*64,'draft')


def test_cross_page_value_and_captures_reopen_exact_unicode_spans_without_single_page_alias():
    d=data('1. 😀 संस्था COD within two months from the date of\n','effectiveness of GNA, subject to receipt.');before=deepcopy(d)
    v=run(d);o=v['observations'][0]
    assert 'source_span'not in o and [s['page']for s in o['source_spans']]==[1,2]
    assert ''.join(s['exact_quote']for s in o['source_spans'])==o['value_literal']
    assert o['captures']['duration']['source_spans'][0]['page']==1
    assert o['captures']['trigger']['source_spans'][0]['page']==2
    for s in [*o['source_spans'],*o['match_source_spans']]:
        assert d['pages'][s['page']-1]['text'][s['char_start']:s['char_start']+s['char_length']]==s['exact_quote']
        assert s['offset_encoding']=='codepoint'
    assert o['source_seams'][0]['semantic_continuation_approved']is False
    assert o['source_quality_observations'][1]['outcome']=='repair_required'
    assert o['native_parent']['id']==d['structure']['units'][0]['id']
    assert not o['content_verified']and not o['calculation_allowed']and d==before


def test_capture_itself_can_span_pages_without_inserting_a_separator():
    d=data('1. Before COD within two months from the date of','effectiveness of GNA')
    r=rules();r['fields'][0]['pattern']=r'(?P<value>COD (?P<duration>within two months from the date of[\s\S]{0,100}?effectiveness) of (?P<trigger>GNA))'
    o=run(d,r)['observations'][0]
    assert len(o['captures']['duration']['source_spans'])==2
    assert 'ofeffectiveness'in o['captures']['duration']['value_literal']
    assert o['projection']=='exact_native_span_concatenation_no_separator'


def test_footer_is_not_silently_removed_and_native_parent_gaps_remain_visible():
    d=data('1. COD within two months from the date of\nPage 1 of 2','effectiveness of GNA')
    o=run(d)['observations'][0]
    assert o['source_seams'][0]['omitted_native_parent_gap'][0]['characters']>0
    assert o['source_seams'][0]['furniture_state']=='not_removed_or_classified_by_field_extractor'
    d=data('1. COD within two months from the date of\nRepeated draft title\n','effectiveness of GNA')
    o=run(d)['observations'][0]
    assert 'Repeated draft title'in o['value_literal']


def test_separate_units_are_never_joined_and_missing_is_not_absent():
    v=run(data('1. COD within two months from the date of\n2. effectiveness of GNA'))
    assert not v['observations']and v['field_status']['cod']['status']=='not_detected_not_proven_absent'


def test_page_selection_does_not_skip_a_middle_page_or_match_context():
    d=data('1. COD within two months from the date of\n','intervening qualifier\n','effectiveness of GNA')
    r=rules();r['fields'][0]['pages']=[1,3]
    assert not run(d,r)['observations']
    r['fields'][0]['pages']=[1,2,3]
    assert len(run(d,r)['observations'][0]['source_spans'])==3


def test_selectors_are_exact_and_unknown_selectors_fail():
    d=data('1. COD within two months from the date of\n','effectiveness of GNA')
    r=rules();r['fields'][0]['native_unit_ids']=['not-a-source-unit']
    with pytest.raises(ValueError,match='selector'):run(d,r)
    r['fields'][0]['native_unit_ids']=[d['structure']['units'][0]['id']]
    assert len(run(d,r)['observations'])==1


def test_optional_captures_and_scalar_normalization_are_explicit():
    r=rules(r'(?P<value>(?P<duration>20)[\s\S]{0,30}?(?P<trigger>30)(?P<optional> absent)?)')
    r['fields'][0].update(type='quantity',capture_groups=['duration','trigger','optional'])
    o=run(data('1. 20\n','30'),r)['observations'][0]
    assert o['typed_candidate']is None and 'multipart_scalar_normalization_not_established'in o['validation_issues']
    assert o['captures']['optional']==dict(value_literal=None,source_spans=[],status='not_matched')


@pytest.mark.parametrize('change',[{'scope':'all'},{'extractor':'formulas','type':'formula'},
    {'native_unit_ids':['x','x']},{'native_unit_labels':[1]},{'native_unit_ids':None}])
def test_invalid_scopes_and_selectors_fail_at_rule_load(tmp_path,change):
    r=rules();r['fields'][0].update(change);p=tmp_path/'rules.json';p.write_text(json.dumps(r))
    with pytest.raises(ValueError):fields.load(p)


def test_reordered_corrupt_or_missing_parents_fail():
    d=data('1. COD within two months from the date of\n','effectiveness of GNA')
    for change in ('reverse','quote','missing'):
        changed=deepcopy(d)
        if change=='reverse':changed['structure']['units'][0]['spans'].reverse()
        elif change=='quote':changed['structure']['units'][0]['spans'][0]['exact_quote']='invented'
        else:changed['structure'].pop('units')
        with pytest.raises(ValueError):run(changed)


def test_lookaround_capture_cannot_escape_explicit_value():
    r=rules(r'(?=(?P<duration>COD))(?P<value>CO)(?P<trigger>)')
    r['fields'][0]['capture_groups']=['duration']
    with pytest.raises(ValueError,match='outside'):run(data('1. COD'),r)


def test_v3_projection_rejects_forged_page_hash_or_duplicate_parents():
    d=data('1. COD within two months from the date of\n','effectiveness of GNA')
    changed=deepcopy(d);changed['pages'][0]['text_sha256']='e'*64
    with pytest.raises(ValueError,match='identity'):run(changed)
    changed=deepcopy(d);changed['structure']['units'].append(changed['structure']['units'][0])
    with pytest.raises(ValueError,match='Duplicate native'):run(changed)


def test_mcp_page_filter_retains_the_complete_multipart_record(tmp_path,monkeypatch):
    from local_vector_batch.mcp_server import Bridge
    from test_legal_l1 import source_fixture
    # A captured source-parent can cross pages; this fixture changes only the
    # exported test observation after native export, with an exact test checksum.
    source=source_fixture(tmp_path)
    r=rules(r'(?P<value>within (?P<duration>two weeks))');r['fields'][0]['capture_groups']=['duration']
    rp=tmp_path/'rules.json';rp.write_text(json.dumps(r));monkeypatch.setenv('PDF2DATA_RULES',str(rp))
    bridge=Bridge(tmp_path/'workspace',[tmp_path])
    try:
        job=bridge.parse(source['path'],ocr='off');bridge.workspace.executor.shutdown(wait=True)
        base=bridge.workspace.directory(job['run_id'])/'readable';p=base/'configured-fields/fields.json'
        packet=json.loads(p.read_text());row=packet['observations'][0]
        other=packet['observations'][1]['source_spans'][0]
        row['source_spans'].append(other);packet['observations']=[row]
        fields.write(p,packet);manifest=json.loads((base/'manifest.json').read_text());manifest['files']['configured-fields/fields.json']=fields.file_sha256(p);fields.write(base/'manifest.json',manifest)
        # MCP read's established contract is hashed export readback, not a
        # producer semantic verifier. Check filtering cannot drop a second part.
        answer=bridge.read(job['run_id'],artifact='fields',page=2,max_chars=200000)['data']
        assert len(answer['observations'][0]['source_spans'])==2
        assert answer['page_filter_scope']=='records_touching_page_complete_multipart_records_retained'
    finally:bridge.close()


def test_multipart_rule_budget_timeout_does_not_publish(tmp_path,monkeypatch):
    from test_legal_l1 import source_fixture
    import subprocess
    package=tmp_path/'source';extract_document(source_fixture(tmp_path),package)
    rp=tmp_path/'rules.json';rp.write_text(json.dumps(rules()))
    def fail(*a,**kw):raise subprocess.TimeoutExpired(a[0],1)
    monkeypatch.setattr(fields.subprocess,'run',fail)
    with pytest.raises(ValueError,match='time budget'):fields.extract(package,rp,tmp_path/'fields',timeout=1)
    assert not(tmp_path/'fields').exists()


@pytest.fixture
def native_bundle(tmp_path):
    from test_legal_l1 import source_fixture
    package=tmp_path/'source';extract_document(source_fixture(tmp_path),package)
    r=rules(r'(?P<value>within (?P<duration>two weeks))');r['fields'][0]['capture_groups']=['duration']
    rp=tmp_path/'rules.json';rp.write_text(json.dumps(r));out=tmp_path/'fields';fields.extract(package,rp,out)
    return out


def test_real_cli_package_v3_verify_query_and_exact_evaluation(native_bundle,tmp_path):
    assert fields.verify(native_bundle)['schema_version']==fields.FIELDS_V3
    rows=fields.query(native_bundle)['observations'];assert len(rows)==2
    value=json.loads((native_bundle/'fields.json').read_text())
    refs={k:value[k]for k in ('source_pdf_sha256','source_package_sha256','rules_sha256')}
    refs.update(reviewer='synthetic source labels; not independent gold',reference_scope='development_fixture',
        fields=[dict(field_id='cod',expected=[dict(native_unit_id=r['native_parent']['id'],source_spans=r['source_spans'],value_literal=r['value_literal'],captures=r['captures'])for r in rows])])
    path=tmp_path/'references.json';path.write_text(json.dumps(refs))
    assert fields.evaluate(native_bundle,path,tmp_path/'evaluation')['passed']is True
    refs['fields'][0]['expected'][0]['captures']={};path.write_text(json.dumps(refs))
    result=fields.evaluate(native_bundle,path,tmp_path/'negative');assert result['passed']is False


@pytest.mark.parametrize('mutation',['parent','span','capture','approval','seam'])
def test_rehashed_multipart_mutations_fail_native_rederivation(native_bundle,mutation):
    value=json.loads((native_bundle/'fields.json').read_text());o=value['observations'][0]
    if mutation=='parent':o['native_parent']['id']='wrong-parent'
    elif mutation=='span':o['source_spans'][0]['char_start']+=1
    elif mutation=='capture':o['captures']['duration']['value_literal']='three weeks'
    elif mutation=='approval':value['semantic_ownership_approved']=True
    else:o['source_seams']=[dict(semantic_continuation_approved=True)]
    fields.write(native_bundle/'fields.json',value)
    manifest=json.loads((native_bundle/'manifest.json').read_text());manifest['files']['fields.json']=fields.file_sha256(native_bundle/'fields.json');fields.write(native_bundle/'manifest.json',manifest)
    with pytest.raises(ValueError,match='re-derivation'):fields.verify(native_bundle)
