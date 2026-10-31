from local_vector_batch.structure_evaluation import tokens,choose


def test_exact_provision_reference_is_kept_separate_from_common_digits():
    assert tokens('Which requirements are under paragraph 1.5?')==['requirements','paragraph','1.5']


def test_budget_never_clips_source_units_and_overflow_is_explicit():
    units=[dict(id='a',region_id='a',text='Evidence '+('long '*100)),dict(id='b',region_id='b',text='Evidence short')]
    result=choose(units,'Evidence',100,2)
    assert result['selected_regions']==['b']
    assert result['required_candidate_omissions']==['a']
    assert result['evidence'][0]['text']=='Evidence short' and result['chars']<=100


def test_relationship_expansion_does_not_create_evidence_outside_known_units():
    units=[dict(id='a',region_id='a',text='Main condition.'),dict(id='b',region_id='b',text='Exception applies.')]
    result=choose(units,'condition',100,1,lambda ids:['missing','b'])
    assert set(result['selected_regions'])=={'a','b'}
