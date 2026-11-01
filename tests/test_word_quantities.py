"""Engineering controls; real-source qualification is documented separately."""
from copy import deepcopy
import json
import pytest
from local_vector_batch import field_rules as fields, field_checks as checks
from test_multipart_fields import data

WORDS='english_cardinal_0_100_v1'
PERCENT='english_cardinal_percent_0_100_v1'


def test_public_rule_schema_requires_quantity_type_for_normalization():
    from pathlib import Path
    jsonschema=pytest.importorskip('jsonschema')
    schema=json.loads((Path(__file__).parents[1]/'schemas/extraction-rules.schema.json').read_text())
    validator=jsonschema.Draft202012Validator(schema)
    config=dict(schema_version=fields.SCHEMA,id='schema-boundary',version='1',fields=[rule()])
    validator.validate(config)
    for kind in ('text','date','reference','formula'):
        config['fields'][0]['type']=kind
        with pytest.raises(jsonschema.ValidationError):validator.validate(config)


def rule(mode=WORDS, pattern=r'(?P<value>.+)'):
    return dict(id='amount',type='quantity',pattern=pattern,quantity_normalization=mode)


@pytest.mark.parametrize('literal,expected',[
    ('zero',0),('ten',10),('nineteen',19),('twenty',20),('twenty six',26),
    ('fifty one',51),('twenty-six',26),('fifty-one',51),('one hundred',100),
    ('Ninety-Nine',99),('seventy\ntwo',72),
])
def test_bounded_cardinals_are_exact(literal,expected):
    assert fields.quantity_candidate(literal,rule())==dict(numerator=str(expected),denominator='1')


@pytest.mark.parametrize('literal',[
    'twenty six or thirty','twenty--six','twenty - six','twentysix','twenty zero',
    'one-hundred','one hundred and one','one hundred one','hundred','two hundred',
    'one thousand','one point five','half','a hundred','ten and','26','twenty 6',
    '-ten','minus ten','ten%','ten per cent','ten to twenty','twenty–six',
    'twenty\u00ad-six','twenly-six','twenty-sіx','छब्बीस','twenty-six (26)',
    ' twenty six','twenty six ','twenty, six','ten/one','NaN','Infinity','',
])
def test_unknown_ambiguous_or_corrupt_forms_are_rejected(literal):
    with pytest.raises(ValueError):fields.quantity_candidate(literal,rule())


@pytest.mark.parametrize('literal,unit',[
    ('twenty-six per cent','per cent'),('fifty one percent','percent'),
    ('one hundred %','%'),('ten%','%'),('Ten PER\nCENT','PER\nCENT'),
])
def test_printed_percent_witness_retains_magnitude_without_ratio_scaling(literal,unit):
    f=rule(PERCENT,r'(?P<value>.+(?P<unit>per cent|percent|%))')
    assert fields.quantity_candidate(literal,f,unit)['denominator']=='1'


@pytest.mark.parametrize('literal,unit',[
    ('twenty-six',None),('twenty-six percent',None),('twenty-six MW','MW'),
    ('twenty-six percent','%'),('twenty-six percent trailing','percent'),
    ('twenty-sixpercent','percent'),('twenty-six per mille','per mille'),
])
def test_unit_hints_and_incorrect_witnesses_never_substitute_for_printed_unit(literal,unit):
    f=rule(PERCENT,r'(?P<value>.+(?P<unit>.+))');f['unit']='percent'
    with pytest.raises(ValueError):fields.quantity_candidate(literal,f,unit)


def test_opt_in_changes_only_typed_candidate_and_issue_and_preserves_source_holds():
    d=data('1. not less than twenty-six per cent of the ownership.');before=deepcopy(d)
    f=rule(pattern=r'not less than (?P<value>twenty-six) per cent of the ownership')
    f.update(scope='native_unit_candidate',unit='percent')
    r=dict(schema_version=fields.SCHEMA,fields_schema_version=fields.FIELDS_V3,id='test',version='1',fields=[f])
    after=fields.compute(d,r,'c'*64,'draft')['observations'][0]
    del f['quantity_normalization']
    old=fields.compute(d,r,'c'*64,'draft')['observations'][0]
    assert old['typed_candidate'] is None and old['validation_issues']==['ambiguous_numeric_literal']
    assert after['typed_candidate']==dict(numerator='26',denominator='1') and not after['validation_issues']
    assert {k:v for k,v in after.items() if k not in ('typed_candidate','validation_issues')}=={k:v for k,v in old.items() if k not in ('typed_candidate','validation_issues')}
    assert after['configured_unit_hint']=='percent' and after['unit_literal'] is None
    assert not after['unit_source_spans'] and 'not less than' in after['match_source_spans'][0]['exact_quote']
    assert not after['content_verified'] and not after['calculation_allowed'] and not after['source_quality_approval_granted']
    assert d==before


def test_multipart_word_join_stays_null_and_held():
    d=data('1. fifty-','one')
    f=rule(pattern=r'(?P<value>fifty-one)');f['scope']='native_unit_candidate'
    r=dict(schema_version=fields.SCHEMA,fields_schema_version=fields.FIELDS_V3,id='test',version='1',fields=[f])
    o=fields.compute(d,r,'c'*64,'draft')['observations'][0]
    assert o['typed_candidate'] is None
    assert o['validation_issues']==['multipart_scalar_normalization_not_established']
    assert o['source_quality_observations'][1]['outcome']=='repair_required'


def test_printed_unit_is_anchored_and_source_conflicting_digits_stay_distinct():
    d=data('1. twenty-six per cent. Hindi 9%; English 3%.')
    fs=[dict(rule(PERCENT,r'(?P<value>twenty-six (?P<unit>per cent))'),scope='native_unit_candidate',unit='percent')]
    fs.extend(dict(id=lang,type='quantity',scope='native_unit_candidate',pattern=lang+r' (?P<value>\d+)') for lang in ('Hindi','English'))
    r=dict(schema_version=fields.SCHEMA,fields_schema_version=fields.FIELDS_V3,id='test',version='1',fields=fs)
    rows=fields.compute(d,r,'c'*64,'draft')['observations']
    assert rows[0]['unit_literal']=='per cent' and rows[0]['unit_source_spans'][0]['exact_quote']=='per cent'
    assert [o['typed_candidate']['numerator'] for o in rows]==['26','9','3']


def test_percent_witness_cannot_be_bound_to_another_occurrence():
    from test_document_model import source
    f=rule(PERCENT,r'(?P<value>ten percent) and (?P<unit>percent)')
    r=dict(schema_version=fields.SCHEMA,id='test',version='1',fields=[f])
    o=fields.compute(source([('ten percent and percent',150,False)]),r,'c'*64,'draft')['observations'][0]
    assert o['typed_candidate'] is None and o['validation_issues']==['unsupported_word_numeric_literal']


@pytest.mark.parametrize('literal',['twenty–six','twenty six or thirty','one hundred and one','छब्बीस'])
def test_unsupported_values_remain_explicit_null_candidates(literal):
    from test_document_model import source
    r=dict(schema_version=fields.SCHEMA,id='test',version='1',fields=[rule()])
    o=fields.compute(source([(literal,150,False)]),r,'c'*64,'draft')['observations'][0]
    assert o['value_literal']==literal and o['source_span']['exact_quote']==literal
    assert o['typed_candidate'] is None and o['validation_issues']==['unsupported_word_numeric_literal']
    assert not o['content_verified'] and not o['calculation_allowed']


@pytest.mark.parametrize('change',[
    {'quantity_normalization':'guess'}, {'quantity_normalization':None},
    {'type':'text'}, {'quantity_normalization':PERCENT},
])
def test_invalid_rule_configuration_is_rejected(tmp_path,change):
    f=rule();f.update(change)
    p=tmp_path/'rules.json';p.write_text(json.dumps(dict(schema_version=fields.SCHEMA,id='test',version='1',fields=[f])))
    with pytest.raises(ValueError):fields.load(p)


def test_typed_checks_fail_on_mutated_numbers_and_distinguish_explicit_null(tmp_path):
    f=rule();r=dict(schema_version=fields.SCHEMA,id='test',version='1',fields=[f])
    from test_document_model import source
    observed=fields.compute(source([('twenty six',150,False)]),r,'c'*64,'draft')
    conf=dict(schema_version=checks.SCHEMA,id='checks',version='1',target=dict(rules_sha256='c'*64),checks=[dict(id='amount',field_id='amount',count=dict(min=1,max=1),expected_literals=['twenty six'],expected_typed_candidates=[dict(numerator='26',denominator='1')])])
    p=tmp_path/'checks.json';p.write_text(json.dumps(conf));conf=checks.load(p)
    assert checks.evaluate(observed,conf)['passed']
    observed['observations'][0]['typed_candidate']['numerator']='25'
    assert not checks.evaluate(observed,conf)['passed']
    conf['checks'][0]['expected_typed_candidates']=[None]
    assert not checks.evaluate(observed,conf)['passed']
    observed['observations'][0]['typed_candidate']=None
    assert checks.evaluate(observed,conf)['passed']


@pytest.mark.parametrize('value',[[],['26'],[{}],[{'numerator':'26','denominator':'0'}],[{'numerator':26,'denominator':'1'}],[{'numerator':'26','denominator':'1','unit':'percent'}]])
def test_invalid_typed_expectations_fail_configuration(tmp_path,value):
    conf=dict(schema_version=checks.SCHEMA,id='checks',version='1',target=dict(rules_sha256='c'*64),checks=[dict(id='amount',field_id='amount',count=dict(min=1,max=1),expected_typed_candidates=value)])
    p=tmp_path/'checks.json';p.write_text(json.dumps(conf))
    with pytest.raises(ValueError):checks.load(p)
