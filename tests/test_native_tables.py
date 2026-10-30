from types import SimpleNamespace
from local_vector_batch.native_tables import glyph_audit,is_ruling_object,table_shape


def test_shaded_text_background_is_not_a_table_rule():
    assert not is_ruling_object({'object_type':'rect','fill':True,'stroke':False,'width':40,'height':9,'non_stroking_color':[.85,.89,.94]})
    assert is_ruling_object({'object_type':'rect','fill':True,'stroke':False,'width':400,'height':.6,'non_stroking_color':[0,0,0]})
    assert is_ruling_object({'object_type':'char','text':'X'})
    assert is_ruling_object({'object_type':'rect','stroke':True,'width':40,'height':90})


def test_unicode_currency_script_and_math_are_not_classified_as_garbage():
    result=glyph_audit('₹ 9.175 crore · क्षमता 72.6 MW ≤ 100% — “subject to”')
    assert result['non_ascii_characters']>0 and not result['issues']
    for bad in ['(cid:123)','\ufffd','\ue001','x\x00y']:
        assert glyph_audit(bad)['status']=='repair_required'


def test_merged_cell_has_span_and_does_not_fill_covered_positions():
    found=SimpleNamespace(rows=[SimpleNamespace(cells=[(0,0,10,20),(10,0,20,10)]),SimpleNamespace(cells=[None,(10,10,20,20)])])
    shape=table_shape(found,[['Category','Header'],[None,'100%']],'t')
    merged=next(c for c in shape['cell_spans'] if c['text']=='Category')
    assert merged['row_span']==2 and shape['row_count']==2 and shape['column_count']==2
    assert not any(c['row']==1 and c['column']==0 for c in shape['cell_spans'])


def test_mixed_script_lookalike_is_flagged_but_preserved():
    result=glyph_audit('Pаy 100')  # Cyrillic a inside an otherwise Latin word.
    assert result['status']=='repair_required'
    assert result['issues'][0]['literal']=='Pаy'
