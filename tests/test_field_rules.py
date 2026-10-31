from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_document_model import source
from local_vector_batch.field_rules import compute,load,catalog


def rules(*fields):return {'schema_version':'pdf2data.extraction-rules.v1','id':'test','version':'1','fields':list(fields)}


def test_new_fields_are_added_in_config_without_parser_changes_and_keep_exact_unicode_anchors():
    d=source([('No. ABC/123 Dated: 26.10.2026',150,False),('दिनांक: 31.02.2026; waiver 75 %',180,False)])
    r=rules({'id':'date','type':'date','pattern':r'(?:Dated|दिनांक): (?P<value>\d{2}\.\d{2}\.\d{4})','date_format':'%d.%m.%Y'},
            {'id':'waiver_percent','type':'quantity','pattern':r'(?P<value>\d+)\s*(?P<unit>%)'})
    before=deepcopy(d);result=compute(d,r,'b'*64,'regulation');obs=result['observations']
    assert obs[0]['typed_candidate']=='2026-10-26'
    assert obs[1]['typed_candidate']is None and obs[1]['validation_issues']
    assert obs[2]['typed_candidate']=={'numerator':'75','denominator':'1'}and obs[2]['unit_literal']=='%'
    for item in obs:
        s=item['source_span'];assert d['pages'][0]['text'][s['char_start']:s['char_start']+s['char_length']]==item['value_literal']
        assert not item['calculation_allowed']and not item['content_verified']
    assert d==before
    r['fields'].append({'id':'new_reference','type':'reference','pattern':r'No\. (?P<value>[A-Z]+/\d+)'})
    assert compute(d,r,'c'*64,'regulation')['field_status']['new_reference']['matches']==1


def test_rules_preserve_unit_assumptions_and_do_not_turn_family_hints_into_facts():
    d=source([('Amount: 100',150,False)]);r=rules({'id':'amount','type':'quantity','pattern':r'Amount: (?P<value>\d+)','unit':'INR','families':['regulation']})
    result=compute(d,r,'b'*64,'regulation');o=result['observations'][0]
    assert o['unit_literal']is None and o['configured_unit_hint']=='INR'
    assert not result['family_is_verified_legal_scope']
    assert not compute(d,r,'b'*64,'judgment')['observations']


def test_formula_rules_cannot_grant_structure_or_computation_approval():
    d=source([('SECR = [[Tariff / Maximum(RTE)]',150,False)]);r=rules({'id':'energy_formula','type':'formula','extractor':'formulas'})
    o=compute(d,r,'b'*64,'regulation')['observations'][0]
    assert not o['calculation_allowed']and not o['formula_observation']['answer_gate']['structured_math_allowed']


@pytest.mark.parametrize('field',[
    {'id':'x','type':'python','pattern':r'(?P<value>.+)'},
    {'id':'x','type':'text','pattern':r'.+'},
    {'id':'x','type':'text','pattern':r'(?P<value>['},
    {'id':'x','type':'formula','extractor':'formulas','scope':'footer_candidate'},
    {'id':'x','type':'text','pattern':r'(?P<value>.+)','pages':[-1]},
    {'id':'x','type':'text','pattern':r'(?P<value>.+)','pages':[True]},
])
def test_invalid_or_unsupported_rules_fail_before_extraction(tmp_path,field):
    p=tmp_path/'rules.json';p.write_text(json.dumps(rules(field)))
    with pytest.raises(ValueError):load(p)


def test_catalog_is_searchable_and_rule_version_changes_observation_identity(tmp_path):
    r=rules({'id':'waiver','type':'quantity','description':'waiver percentage','pattern':r'(?P<value>\d+) %'})
    p=tmp_path/'rules.json';p.write_text(json.dumps(r));assert len(catalog(p,'waiver')['fields'])==1
    assert not catalog(p,'unrelated')['fields']
    d=source([('75 %',150,False)])
    assert compute(d,r,'a'*64,'generic')['observations'][0]['id']!=compute(d,r,'b'*64,'generic')['observations'][0]['id']


def test_packaged_template_initializes_a_searchable_ruleset_without_overwriting(tmp_path,capsys):
    from local_vector_batch.cli import main
    path=tmp_path/'my-rules.json'
    assert main(['fields','init','--output',str(path),'--json'])==0
    assert len(load(path)['fields'])==4
    assert main(['fields','init','--output',str(path),'--json'])==2


def test_default_metadata_rules_do_not_match_notification_negation_or_cited_unlabelled_dates():
    import local_vector_batch.field_rules as module
    r=load(Path(module.__file__).with_name('extraction_rules')/'document-v1.json')
    d=source([('Supplementary notification; no back-to-back contract and non-readiness.',150,False),
              ('No. L-1/250/2019/CERC Dated: 26.10.2026',180,False),
              ('The draft dated 31.07.2026 cited OM dated 16.04.2026.',200,False)])
    result=compute(d,r,'b'*64,'generic')['observations']
    assert [o['value_literal']for o in result if o['field_id']=='document.reference']==['L-1/250/2019/CERC']
    assert [o['value_literal']for o in result if o['field_id']=='document.date_numeric']==['26.10.2026']
