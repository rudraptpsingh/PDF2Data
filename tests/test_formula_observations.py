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


@pytest.mark.parametrize('literal', [
    'Conn-BG2: <132kV Rs. 1 Crore',
    'X + 2 + 3 = 5',
    '2 + 3 = 5 + 7',
    '2 + 3 = 5 ≥ X',
    '2 + 3 = 5 ± 0.1 MW',
    'Voltage ≈ 132 kV',
])
def test_comparison_and_partial_equalities_keep_the_entire_source_literal(literal):
    d = source([(literal, 150, False)])
    formula, = extract_formulas(d)['formulas']
    assert formula['literal'] == literal
    assert formula['kind'] == 'unparsed_formula_candidate'
    assert formula['expression_tree'] is None
    assert formula['arithmetic_status'] == 'unparsed'


def test_parenthesized_numeric_equality_preserves_operators_and_units():
    literal = '(145 − 45) × 2 ÷ 5 = 40 MW'
    d = source([(literal, 150, False)])
    formula, = extract_formulas(d)['formulas']
    assert formula['literal'] == literal
    assert formula['arithmetic_status'] == 'exact_match'
    assert formula['computed_rational'] == '40/1'
    assert formula['result_unit_literal'] == 'MW'


def test_unicode_negative_result_is_kept_as_printed():
    literal = '4035.78 − 4045.78 = −10.00'
    formula, = extract_formulas(source([(literal, 150, False)]))['formulas']
    assert formula['literal'] == literal
    assert formula['stated_result_literal'] == '−10.00'
    assert formula['computed_rational'] == '-10/1'
    assert formula['arithmetic_status'] == 'exact_match'


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


def test_table_formulas_use_their_own_cells_instead_of_the_interleaved_page_line():
    mixed='(D)=(A-B)/ (F)=|min of (D-'
    data=source([(mixed,150,False)])
    headings=['Asset','Cost','Claimed','Norm','Allowable','Prior','Additional']
    symbols=[None,'(A)','(B)','(C)','(D)=(A-B)/\n(100-C) * C','(E)','(F)=|min of (D-\nE) or (B-E)|']
    data['tables']=[dict(id='table-1',table_version_id='version-1',page=1,
        cells=[headings,symbols,['Asset-2','100','5','1%','0.96','5','0.00']],
        cell_spans=[dict(id=f'cell-{column}',row=1,column=column,text=symbols[column],
                         bbox=[72,140,400,175]) for column in (4,6)])]
    model=extract_formulas(data)
    assert model['schema_version']=='pdf2data.formulas.v2'
    assert [(f['symbol'],f['literal'],f['source_cell']['cell_id']) for f in model['formulas']]==[
        ('D',symbols[4],'cell-4'),('F',symbols[6],'cell-6')]
    assert all(not f['variable_bindings_verified'] for f in model['formulas'])
    assert extract_formulas(data,include_table_cells=False)['formulas'][0]['literal']==mixed
