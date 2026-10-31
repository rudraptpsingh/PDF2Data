from copy import deepcopy
from fractions import Fraction
import pytest
from test_document_model import source
from local_vector_batch.formula_observations import arithmetic,extract_formulas
from local_vector_batch.document_model import build_document_model,validate_document_model
from local_vector_batch.layout_overlay import build_overlays


def test_printed_transmission_example_becomes_tree_and_exact_check_with_source_span():
    d=source([('The quantum is 5450 - (5000 + 300) = 150MW',150,False)])
    original=deepcopy(d['pages']);d['document_model']=build_document_model(d)
    f=d['document_model']['formula_observations']['formulas'][0]
    assert f['literal']=='5450 - (5000 + 300) = 150MW'
    assert f['computed_value']=='150' and f['arithmetic_status']=='exact_match'
    assert f['expression_tree']['type']=='subtract' and f['expression_tree']['right']['type']=='add'
    assert f['result_unit_literal']=='MW' and not f['variable_bindings_verified']
    assert d['pages']==original
    overlay=next(r for r in build_overlays(d)['pages'][0]['regions'] if r['type']=='formula')
    assert overlay['text']==f['literal'] and '150 MW' in overlay['preview_html']


def test_different_or_rounded_result_is_not_silently_repaired():
    d=source([('5450 - (5000 + 300) = 151MW',150,False),('1 / 3 = 0.33',180,False)])
    f=extract_formulas(d)['formulas']
    assert len(f)==2 and all(x['arithmetic_status']=='different_or_rounded' for x in f)
    assert f[0]['stated_result_literal']=='151'


def test_symbolic_formula_and_zero_divisor_remain_explicit():
    d=source([('Charge = Rate × Quantity',150,False),('10 / 0 = 1',180,False)])
    f=extract_formulas(d)['formulas']
    assert any(x['kind']=='unparsed_formula_candidate' and x['expression_tree'] is None for x in f)
    assert any(x['arithmetic_status']=='undefined_division_by_zero' for x in f)


def test_operator_glyphs_are_normalized_for_arithmetic_only():
    value,tree=arithmetic('5450 − (5000 + 300)')
    assert value==Fraction(150)
    assert arithmetic('3 × 350')[0]==1050
    assert arithmetic('1,00,000 / 4')[0]==25000


@pytest.mark.parametrize('expr',["__import__('os').system('echo bad')",'2 ** 10','1,2,3 + 1','a + 1','9'*31+' + 1'])
def test_unbounded_or_executable_source_syntax_is_not_accepted(expr):
    with pytest.raises((ValueError,SyntaxError)):arithmetic(expr)


def test_changed_formula_anchor_is_rejected():
    d=source([('800 + 100 = 900 MW',150,False)]);m=build_document_model(d)
    m['formula_observations']['formulas'][0]['source_span']['char_start']+=1
    with pytest.raises(ValueError,match='formula source anchor'):validate_document_model(m,d)


def test_arithmetic_record_cannot_be_tampered_and_grouping_is_not_guessed():
    d=source([('100 + 23 = 1,2,3',150,False)]);m=build_document_model(d)
    assert m['formula_observations']['formulas'][0]['arithmetic_status']=='unparsed'
    m['formula_observations']['formulas'][0]['computed_value']='123'
    with pytest.raises(ValueError,match='arithmetic observation changed'):validate_document_model(m,d)
