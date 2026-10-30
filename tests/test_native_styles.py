from types import SimpleNamespace
from local_vector_batch.native_styles import source_underlines


def page(rect_top=29,lower=False):
    chars=[{'text':t,'x0':10+i*8,'x1':18+i*8,'top':15,'bottom':30} for i,t in enumerate('Date')]
    if lower:chars.append({'text':'2','x0':18,'x1':26,'top':34,'bottom':49})
    return SimpleNamespace(chars=chars,rects=[{'x0':10,'x1':42,'top':rect_top,'bottom':rect_top+1}])


def test_underline_is_recorded_as_retained_source_style():
    result=source_underlines(page())
    assert len(result)==1 and result[0]['text_run']=='Date'
    assert result[0]['formatting_semantics'].startswith('unresolved')


def test_strike_fraction_and_table_rules_are_not_masked():
    assert not source_underlines(page(rect_top=22))
    assert not source_underlines(page(lower=True))
    assert not source_underlines(page(),table_count=1)
