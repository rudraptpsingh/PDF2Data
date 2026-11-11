from copy import deepcopy
import pytest
from local_vector_batch.native_text_successor_preflight import _unchanged_pages


def test_unselected_page_records_must_remain_identical():
    original=[{'page':1,'text':'one','quality':'hold'},{'page':13,'text':'old'},{'page':14,'text':'control'}]
    target=deepcopy(original);target[1]['text']='candidate'
    a,b=_unchanged_pages(original,target)
    assert a[13]['text']=='old' and b[13]['text']=='candidate'
    for bad in (target[:-1],target+[target[0]],[] ):
        with pytest.raises(ValueError):_unchanged_pages(original,bad)
    changed=deepcopy(target);changed[2]['quality']='cleared'
    with pytest.raises(ValueError,match='unselected page changed'):_unchanged_pages(original,changed)
