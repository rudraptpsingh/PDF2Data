import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('reading_grader',Path(__file__).parents[1]/'scripts/grade_local_readings.py')
grader=importlib.util.module_from_spec(spec);spec.loader.exec_module(grader)


def model(text,**kw):return {'model':'local-test','revision':'test','offline':True,'results':[{'id':'test','kind':'transcription','text':text,**kw}]}


def test_a_correct_date_with_a_hallucinated_extra_date_is_not_a_pass():
    cases={'cases':[{'id':'test','kind':'date','source_line_reference':'Dated 24.2.2010','reference_scope':'test'}]}
    r=grader.grade(cases,model('7/1/2010 Dated 24.2.2010'))
    assert r['failed']==1 and r['results'][0]['unexpected_numeric_tokens']==['7/1/2010']


def test_right_value_in_wrong_column_is_rejected():
    cases={'cases':[{'id':'test','kind':'table','reference_grid':[['GNA','COD'],['Not effective','30.07.2026']],'expected_gna_column':0,'expected':'Not effective'}]}
    r=grader.grade(cases,model('<table><tr><th>GNA</th><th>COD</th></tr><tr><td>30.07.2026</td><td>Not effective</td></tr></table>'))
    assert r['failed']==1 and not r['results'][0]['gna_cell_exact']


def test_otsl_phantom_column_is_not_accepted_as_a_good_table():
    cases={'cases':[{'id':'test','kind':'table','reference_grid':[['GNA','COD'],['Not effective','30.07.2026']],'expected_gna_column':0,'expected':'Not effective'}]}
    r=grader.grade(cases,model('<fcel>GNA<fcel>COD<ecel><nl><fcel>Not effective<fcel>30.07.2026<ecel><nl>'))
    assert not r['results'][0]['shape_exact'] and r['failed']==1


def test_token_cap_cannot_be_called_complete_even_for_a_matching_value():
    cases={'cases':[{'id':'test','kind':'cell','expected':'Not effective'}]}
    r=grader.grade(cases,model('Not effective',possible_token_cap=True))
    assert r['failed']==1
