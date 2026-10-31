from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest

from local_vector_batch.legal_l1 import structure_document,extract_document
from local_vector_batch.regulatory_candidates import build,load_profile,quote_regions,extract,verify,read


def data(*texts):
    pages=[dict(page=i,id=f'page-{i}',text=t,text_sha256=sha256(t.encode()).hexdigest())for i,t in enumerate(texts,1)]
    return dict(source_pdf_sha256='a'*64,pages=pages,tables=[],structure=structure_document(pages,'a'*64,'draft'),
                quality=dict(results=[dict(page=i,outcome='repair_required'if i==2 else'auto_accepted')for i in range(1,len(pages)+1)]))


def run(d):return build(d,load_profile()[0],'b'*64,'c'*64)
def records(v,g):return [r for r in v['records']if r['group']==g]


def test_multiple_operations_keep_distinct_old_new_and_value_roles():
    d=data('DRAFT NOTIFICATION\n1. Amendment\n1.1. The words “18 months” shall be substituted with the words “6 months” and the words “within 15 days” shall be inserted after the words “start date” in Regulation 9.3.1.')
    original=deepcopy(d);v=run(d);ops=records(v,'amendments')
    assert [r['operation']for r in ops]==['substitute','insert']
    assert ops[0]['old_fragment']['literal']=='18 months'and ops[0]['new_fragment']['literal']=='6 months'
    assert ops[1]['new_fragment']['literal']=='within 15 days'and ops[1]['old_fragment']is None
    values=records(v,'values')
    role_by_value={r['value']['raw_value']['literal']:{x['role']for x in r['amendment_value_roles']}for r in values}
    assert role_by_value['18']=={'earlier_text'}and role_by_value['6']=={'replacement_text'}
    roles={r['value']['raw_value']['literal']:r['assertion_role']['candidate']for r in values}
    assert roles['18']=='quoted_earlier_text'and roles['6']=='proposed'
    assert d==original and all(not r['legal_answer_eligible']and not r['calculation_allowed']for r in v['records'])


def test_nested_quotes_do_not_clip_inserted_definition_and_quoted_instructions_are_not_new_operations():
    d=data('1. Amendment\n1.1. A new clause shall be added under Regulation 2 as under:\n“LAND BG means “Bank Guarantee”; the words “x” shall be deleted by the applicant.”')
    v=run(d);ops=records(v,'amendments')
    assert len(ops)==1 and '“Bank Guarantee”'in ops[0]['new_fragment']['literal']
    assert 'shall be deleted'in ops[0]['new_fragment']['literal']


def test_missing_quote_or_unprinted_old_text_stays_unknown():
    v=run(data('1. Amendment\n1.1. Clause (iv) of Regulation 17.1 shall be deleted.\n1.2. The words “uncertain shall be added under Regulation 2.'))
    ops=records(v,'amendments')
    assert ops[0]['old_fragment']is None and ops[1]['new_fragment']is None
    assert 'unbalanced_source_quote_not_repaired'in ops[1]['limitations']


def test_cross_page_condition_keeps_two_native_anchors_and_held_quality():
    d=data('1. Amendment\n1.1. A proviso shall be added: “Provided that request is made within ',
           '30 days from notification, subject to payment of dues.”')
    v=run(d);op=records(v,'amendments')[0]
    assert {s['page']for s in op['new_fragment']['source_spans']}=={1,2}
    condition=records(v,'conditions')[0]
    assert {s['page']for s in condition['source_spans']}=={1,2}
    assert any(q['outcome']=='repair_required'for q in condition['source_quality_observations'])
    assert condition['predicate_tree']['kind']=='unknown'
    assert not condition['source_quality_approval_granted']
    assert condition['native_child_ids']


def test_explicit_boolean_join_candidates_and_exclusion_have_evidence():
    v=run(data('1. Amendment\n1.1. A proviso shall be added: “In case (i) capacity is exempt or (ii) capacity has consent, sub-clauses (a) shall not apply to ESS (excluding PSP).”'))
    conditions=records(v,'conditions')
    tree=next(r['predicate_tree']for r in conditions if r['predicate_tree']['kind']=='any')
    assert tree['connector']['literal']=='or'and len(tree['children'])==2
    excluded=next(r for r in conditions if r['introducer']['literal']=='excluding')
    assert excluded['literal']=='excluding PSP'
    assert excluded['predicate_tree']['observed_components'][0]['kind']=='not'


def test_rate_currency_scale_and_duration_do_not_become_calculations():
    v=run(data('1. Amendment\n1.1. A proviso shall be added: “Bank Guarantee shall be Rs. 5 lakh/MW within 15 days. A fee of Rs 50,000/MW is payable.”'))
    rates=[r['value']for r in records(v,'values')if r['value']['kind']=='rate']
    assert rates[0]['typed_candidate']=={'numerator':'5','denominator':'1'}
    assert rates[0]['scale_candidate']=={'numerator':'100000','denominator':'1'}
    assert rates[0]['currency_literal']=='Rs.'and rates[0]['currency_code_candidate']is None
    assert rates[1]['typed_candidate']['numerator']=='50000'
    assert all(r['absolute_date']is None for r in rates)


def test_factor_words_and_printed_dates_keep_their_own_literals():
    v=run(data('DRAFT NOTIFICATION Dated: 20.05.2026\n1. Amendment\n1.1. The words “minimum discharge as twice the Connectivity quantum” shall be added.'))
    factors=[r['value']for r in records(v,'values')if r['value']['kind']=='factor']
    assert factors[0]['raw_value']['literal']=='twice'and factors[0]['typed_candidate']['numerator']=='2'
    dates=[r['value']for r in records(v,'values')if r['value']['kind']=='date']
    assert dates[0]['typed_candidate']=='2026-05-20'and dates[0]['date_role']=='unresolved'
    date_record=next(r for r in records(v,'values')if r['value']['kind']=='date')
    assert date_record['assertion_role']['candidate']=='unknown'and date_record['source_document_role']['candidate']=='draft'


def test_heading_does_not_silently_override_different_operation_target():
    v=run(data('1. Amendment to Regulation 5.2\n1.1. A new proviso shall be added under Regulation 5.1: “Provided that documents are supplied.”'))
    op=records(v,'amendments')[0]
    assert any('5.1'in r['literal']for r in op['target_reference_candidates'])
    assert any('5.2'in r['literal']for r in op['heading_reference_candidates'])
    assert 'heading_and_operation_references_differ_not_reconciled'in op['limitations']


def test_new_address_and_insertion_anchor_are_separate_unresolved_references():
    v=run(data('1. Amendment\n1.1. A new Regulation 3.10 shall be added after Regulation 3.9 as under: “3.10 A condition.”'))
    op=records(v,'amendments')[0]
    by_literal={r['literal']:r['role_candidate']for r in op['target_reference_candidates']}
    assert by_literal['Regulation 3.10']=='new_address_candidate'
    assert by_literal['Regulation 3.9']=='location_anchor_candidate'
    assert [r['reference']['literal']for r in op['dependency_requirements']]==['Regulation 3.9']


def test_transition_and_commencement_do_not_invent_effectiveness_or_deadline():
    v=run(data('DRAFT NOTIFICATION\n1. Commencement\n1.1. These regulations shall come into force from publication in Official Gazette.\n2. Amendment\n2.1. A new clause shall be added: “Transition provisions: For cases where COD is over, the Agency within one month of effectiveness shall return BGs, subject to clearing dues.”'))
    assert records(v,'commencements')[0]['actual_publication_date']is None
    assert records(v,'commencements')[0]['subitem_label']=='1.1'
    transition=records(v,'transitions')[0]
    assert transition['actual_deadline']is None and transition['condition_candidate_ids']
    assert 'subject to'in transition['qualified_consequence']['literal']


def test_all_native_units_and_table_are_accounted_without_table_semantics():
    d=data('1. Amendment\n1.1. A clause shall be deleted.\n2. No matching rule.');d['tables']=[dict(id='native-table',page=1,cells=[['A',None],['B','']])]
    v=run(d)
    assert v['coverage']['native_units_accounted']==len(d['structure']['units'])
    assert v['coverage']['tables'][0]['raw_table']==d['tables'][0]
    assert v['coverage']['tables'][0]['coverage_status']=='review_gap'
    assert v['coverage']['semantic_completeness']=='not_established'
    assert any(u['groups']['amendments']['status']=='not_detected'for u in v['coverage']['units'])


def test_noncovered_script_remains_a_review_gap_with_exact_unicode():
    d=data('1. प्रारूप आदेश ₹ ९१,७५०।');v=run(d)
    assert all(g['status']=='review_gap'for g in v['coverage']['units'][0]['groups'].values())
    assert v['coverage']['units'][0]['source_spans'][0]['exact_quote']==d['pages'][0]['text']


def test_native_bundle_rederives_associations_and_rejects_promotion(tmp_path):
    from test_legal_l1 import source_fixture
    from local_vector_batch.hashing import file_sha256
    source=source_fixture(tmp_path);package=tmp_path/'l1';extract_document(source,package)
    out=tmp_path/'candidate';extract(package,out)
    value=verify(out);assert value['coverage']['native_units_accounted']
    # Even if hashes are rewritten, a forged permission is not a source derivation.
    p=out/'candidates.json';value['legal_answer_eligible']=True;p.write_text(json.dumps(value))
    manifest=json.loads((out/'manifest.json').read_text());manifest['files']['candidates.json']=file_sha256(p)
    (out/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='fresh source derivation'):verify(out)


@pytest.fixture
def native_bundle(tmp_path):
    from test_legal_l1 import source_fixture
    source=source_fixture(tmp_path);package=tmp_path/'l1';extract_document(source,package)
    out=tmp_path/'candidate';extract(package,out)
    return out


def test_cli_profile_init_preserves_bytes_and_refuses_overwrite(tmp_path,capsys):
    from local_vector_batch.cli import main
    path=tmp_path/'rules.json'
    assert main(['regulatory','init','--output',str(path),'--json'])==0
    assert path.read_bytes()==load_profile()[1].read_bytes()
    assert main(['regulatory','init','--output',str(path),'--json'])==2


@pytest.mark.parametrize('change',[
    {'quantity_pattern':r'(?P<value>\d+) MW'},
    {'operation_pattern':'shall be added'},
    {'factor_pattern':'twice'},
    {'max_records':True},
    {'max_records':100001},
    {'operation_names':{'added':'execute'}},
    {'comparisons':[{'pattern':'[','candidate':'lte'}]},
    {'boolean_patterns':['(?P<left>x)(?P<right>y)']},
])
def test_invalid_profile_fails_before_processing(tmp_path,change):
    profile=deepcopy(load_profile()[0]);profile.update(change)
    path=tmp_path/'rules.json';path.write_text(json.dumps(profile))
    with pytest.raises((ValueError,__import__('re').error)):load_profile(path)


def test_custom_profile_can_add_domain_vocabulary_without_code_change():
    profile=deepcopy(load_profile()[0]);profile['id']='contract-time-limits';profile['version']='2'
    profile['quantity_pattern']=r'(?P<value>\d+)\s+(?P<unit>business days)'
    profile['condition_pattern']=r'contingent upon'
    d=data('1. Contract\n1.1. Delivery is contingent upon approval within 12 business days.')
    original=deepcopy(d)
    value=build(d,profile,'d'*64,'c'*64)
    assert records(value,'values')[0]['value']['unit']['literal']=='business days'
    assert records(value,'conditions')[0]['introducer']['literal']=='contingent upon'
    assert not run(d)['records']
    assert records(value,'values')[0]['id']!=build(d,profile,'e'*64,'c'*64)['records'][0]['id']
    assert d==original


def test_record_limit_fails_instead_of_clipping():
    profile=deepcopy(load_profile()[0]);profile['max_records']=1
    with pytest.raises(ValueError,match='not clipped'):build(data('1. Rates\n1.1. 5 MW and 6 MW'),profile,'b'*64,'c'*64)


def test_bounded_worker_timeout_does_not_publish_bundle(tmp_path,monkeypatch):
    import local_vector_batch.regulatory_candidates as module
    from test_legal_l1 import source_fixture
    package=tmp_path/'l1';extract_document(source_fixture(tmp_path),package)
    def timeout(*args,**kwargs):raise module.subprocess.TimeoutExpired(args[0],1)
    monkeypatch.setattr(module.subprocess,'run',timeout)
    out=tmp_path/'candidate'
    with pytest.raises(ValueError,match='no partial bundle'):extract(package,out,timeout=1)
    assert not out.exists()


@pytest.mark.parametrize('mutation',['operand','span','unit_id','manifest_approval','profile','source'])
def test_mutated_bundles_cannot_pass_integrity(native_bundle,mutation):
    from local_vector_batch.hashing import file_sha256
    bundle=native_bundle;manifest_path=bundle/'manifest.json';manifest=json.loads(manifest_path.read_text())
    if mutation=='manifest_approval':manifest['publication_eligible']=True
    elif mutation=='source':
        p=Path(manifest['source_package'])/'original.pdf';p.write_bytes(p.read_bytes()+b'changed')
    elif mutation=='profile':
        p=bundle/'profile.json';value=json.loads(p.read_text());value['id']='another-profile';p.write_text(json.dumps(value));manifest['files']['profile.json']=file_sha256(p)
    else:
        p=bundle/'candidates.json';value=json.loads(p.read_text());record=value['records'][0]
        if mutation=='span':record['source_spans'][0]['char_start']+=1
        elif mutation=='unit_id':record['native_unit_id']='invented-native-unit'
        else:record['unsupported_operand']=dict(value=0,unit='INR')
        p.write_text(json.dumps(value));manifest['files']['candidates.json']=file_sha256(p)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):verify(bundle)


def references_for(bundle):
    value=verify(bundle)
    return {**{k:value[k]for k in ('source_pdf_sha256','source_package_sha256','profile_sha256')},
            'reference_scope':'agent_development','reviewer':'test fixture; not independent gold',
            'cases':[{'id':'coverage','scope':'bundle','checks':[{'pointer':'/coverage/semantic_completeness','equals':'not_established'}]}]}


def test_evaluation_cli_detects_wrong_claim_and_read_is_source_bound(native_bundle,tmp_path,capsys):
    from local_vector_batch.cli import main
    refs=references_for(native_bundle);path=tmp_path/'references.json';path.write_text(json.dumps(refs))
    assert main(['regulatory','evaluate','--bundle',str(native_bundle),'--references',str(path),'--output',str(tmp_path/'good'),'--json'])==0
    refs['cases'][0]['checks'][0]['equals']='complete';path.write_text(json.dumps(refs))
    assert main(['regulatory','evaluate','--bundle',str(native_bundle),'--references',str(path),'--output',str(tmp_path/'bad'),'--json'])==3
    assert json.loads((tmp_path/'bad/evaluation.json').read_text())['failed']==1
    candidate=verify(native_bundle)['records'][0]
    assert read(native_bundle,candidate_id=candidate['id'])['records']==[candidate]
    with pytest.raises(ValueError,match='does not resolve'):read(native_bundle,candidate_id='other-bundle-id')


@pytest.mark.parametrize('mutation',['stale_source','empty','bad_check','empty_contains','duplicate','no_provenance'])
def test_evaluation_rejects_vacuous_or_stale_references(native_bundle,tmp_path,mutation):
    from local_vector_batch.regulatory_candidates import evaluate
    refs=references_for(native_bundle)
    if mutation=='stale_source':refs['source_pdf_sha256']='e'*64
    elif mutation=='empty':refs['cases']=[]
    elif mutation=='bad_check':refs['cases'][0]['checks']=[{'pointer':'/records','equal':[]}]
    elif mutation=='empty_contains':refs['cases'][0]['checks']=[{'pointer':'/schema_version','contains':''}]
    elif mutation=='duplicate':refs['cases']*=2
    elif mutation=='no_provenance':refs['reviewer']=''
    path=tmp_path/'references.json';path.write_text(json.dumps(refs))
    with pytest.raises(ValueError):evaluate(native_bundle,path,tmp_path/'report')
    assert not (tmp_path/'report').exists()


def test_new_native_wire_schema_validates_bundle_and_rejects_promotions(native_bundle):
    from jsonschema import Draft202012Validator,ValidationError
    schema=json.loads((Path(__file__).parents[1]/'schemas/regulatory-candidates.schema.json').read_text())
    Draft202012Validator.check_schema(schema);validator=Draft202012Validator(schema)
    value=verify(native_bundle);validator.validate(value)
    value['records'][0]['legal_answer_eligible']=True
    with pytest.raises(ValidationError):validator.validate(value)


def test_reference_only_source_has_explicit_non_detection_not_absence():
    value=run(data('1. Preamble\nReference Regulation 5. No amendment instruction.'))
    assert not records(value,'amendments')
    group=value['coverage']['units'][0]['groups']['amendments']
    assert group['status']=='not_detected'and group['absence_proven']is False
