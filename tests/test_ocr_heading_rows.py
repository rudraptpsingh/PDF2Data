from copy import deepcopy
import hashlib
import pytest
from local_vector_batch.ocr_heading_rows import plan


def fixture():
    lines=[{'text':'Procedure for connection','bbox':[.18,.600,.5,.620],'words':[]},
        {'text':'4.4','bbox':[.13,.6005,.16,.6205],'words':[]}]
    text='\n'.join(r['text']for r in lines)
    page={'page':42,'method':'rapidocr','text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'chars':len(text),'ocr':{'lines':lines}}
    independent={'lines':[{'text':'4.4 Procedure for connection','bbox':[.129,.599,.501,.622]}]}
    return page,independent


def test_order_only_keeps_every_raw_provider_line_and_all_literal_fields():
    page,v=fixture();original=deepcopy(page);result=plan(page,v);new=result['proposed_page']
    assert page==original and new['text']=='4.4\nProcedure for connection'
    assert new['ocr']['lines']==[page['ocr']['lines'][1],page['ocr']['lines'][0]]
    assert result['proof']['original_line_permutation']==[1,0]
    assert result['proof']['source_quality_clearance']is False


@pytest.mark.parametrize('change',['wrong_word','wrong_number','duplicate_independent','far_gap','different_baseline','numeric_value','two_columns','dropped_line'])
def test_disagreement_geometry_and_content_negatives(change):
    p,v=fixture()
    if change=='wrong_word':v['lines'][0]['text']='4.4 Procedure without connection'
    elif change=='wrong_number':v['lines'][0]['text']='4.5 Procedure for connection'
    elif change=='duplicate_independent':v['lines']*=2
    elif change in('far_gap','two_columns'):p['ocr']['lines'][0]['bbox'][0]=.6
    elif change=='different_baseline':p['ocr']['lines'][1]['bbox']=[.13,.65,.16,.67]
    elif change=='numeric_value':
        p['ocr']['lines'][0]['text']='Tariff rate 51 percent';p['text']='\n'.join(l['text']for l in p['ocr']['lines']);p['text_sha256']=hashlib.sha256(p['text'].encode()).hexdigest();v['lines'][0]['text']='4.4 Tariff rate 51 percent'
    else:p['ocr']['lines'].pop()
    with pytest.raises(ValueError):plan(p,v)
