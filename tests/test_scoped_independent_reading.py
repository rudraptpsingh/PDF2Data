"""Synthetic closed-contract controls; source results are separate receipts."""
from copy import deepcopy
import pytest
from local_vector_batch import scoped_independent_reading as m


def fixture(old='51',new='61',primary='6१'):
 box=[.1,.2,.3,.3]
 selected={'page':1,'method':'rapidocr','text':'😀 '+primary,'text_sha256':m.sha('😀 '+primary),'pdf_width':100,'pdf_height':100,'ocr':{'engine':'rapidocr','engine_package_version':'3.10.0','recognition_model':'fixture','recognition_model_sha256':'d'*64,'lines':[{'text':'😀 '+primary,'words':[{'text':'😀','bbox':[0,0,.05,.05]},{'text':primary,'bbox':box}]}]}}
 raw={'engine':'vision','page':1,'source_pdf_sha256':'a'*64,'text':'😀 '+old,'text_sha256':m.sha('😀 '+old),'lines':[{'text':'😀 '+old,'words':[{'text':'😀','bbox':[0,0,.05,.05]},{'text':old,'bbox':box}]}]}
 case={'id':'case','source_pdf_sha256':'a'*64,'page':1,'raw_independent_sha256':m.sha(m.canonical(raw)),'selected_page_sha256':m.sha(m.canonical(selected)),'selected_model_profile':m.profile(selected),'independent_line_index':0,'independent_word_index':1,'primary_line_index':0,'primary_word_index':1,'primary_literal':primary,'original_literal':old,'reviewed_literal':new,'original_span':{'char_start':2,'char_length':len(old),'exact_quote':old,'offset_encoding':'codepoint'},'crop':{'bbox':[0,0,100,100],'dpi':240,'sha256':'b'*64}}
 policy={'contract_version':'fixture.v1','rule':'fixture_rule','review_origin':'fixture_review','minimum_word_box_iou':.3,'whole_page_gates_unchanged':True,'cases':[case]}
 decision={'contract_version':'fixture.v1','rule':'fixture_rule','review_origin':'fixture_review','policy_sha256':m.sha(m.canonical(policy)),'source_pdf_sha256':'a'*64,'page':1,'raw_independent_sha256':m.sha(m.canonical(raw)),'selected_page_sha256':m.sha(m.canonical(selected)),'cases':['case']}
 return selected,raw,decision,policy


@pytest.mark.parametrize('old,new,primary',[('51','61','6१'),('759','75%','75%')])
def test_exact_codepoint_correction_is_separate_from_raw_provider(old,new,primary):
 selected,raw,decision,policy=fixture(old,new,primary);before=deepcopy((selected,raw,decision,policy))
 result=m.plan(selected,raw,decision,policy)
 assert (selected,raw,decision,policy)==before
 assert result['corrected_reading']['text']=='😀 '+new
 assert result['corrected_reading']['engine']=='source_review_corrected_independent'
 assert result['corrected_reading']['raw_reader_engine']=='vision'
 assert result['proof']['source_quality_clearance']is False
 assert result['proof']['changes'][0]['original_span']['char_start']==2


@pytest.mark.parametrize('attack',['wrong61','wrong75','stale_span','stale_raw_hash','wrong_page','bool_page','model','profile','source','duplicate_case','unknown_case','extra_field','crop_clip','different_word_box','not_exact_lines','raw_engine','weaken_policy'])
def test_stale_wide_or_unapproved_corrections_fail_closed(attack):
 selected,raw,d,policy=fixture()
 if attack=='wrong61':d['reviewed_literal']='62'
 elif attack=='wrong75':d['reviewed_literal']='75'
 elif attack=='stale_span':policy['cases'][0]['original_span']['char_start']=3;d['policy_sha256']=m.sha(m.canonical(policy))
 elif attack=='stale_raw_hash':d['raw_independent_sha256']='f'*64
 elif attack=='wrong_page':d['page']=2
 elif attack=='bool_page':d['page']=True
 elif attack=='model':selected['ocr']['recognition_model_sha256']='0'*64
 elif attack=='profile':policy['cases'][0]['selected_model_profile']['recognition_model']='other';d['policy_sha256']=m.sha(m.canonical(policy))
 elif attack=='source':d['source_pdf_sha256']='f'*64
 elif attack=='duplicate_case':d['cases']*=2
 elif attack=='unknown_case':d['cases']=['unknown']
 elif attack=='extra_field':d['confidence_approval']=1
 elif attack=='crop_clip':policy['cases'][0]['crop']['bbox']=[70,70,80,80];d['policy_sha256']=m.sha(m.canonical(policy))
 elif attack=='different_word_box':raw['lines'][0]['words'][1]['bbox']=[.7,.7,.8,.8];d['raw_independent_sha256']=m.sha(m.canonical(raw));policy['cases'][0]['raw_independent_sha256']=d['raw_independent_sha256'];d['policy_sha256']=m.sha(m.canonical(policy))
 elif attack=='not_exact_lines':raw['lines'][0]['text']+=' invented';d['raw_independent_sha256']=m.sha(m.canonical(raw))
 elif attack=='raw_engine':raw['engine']='qwen';d['raw_independent_sha256']=m.sha(m.canonical(raw))
 else:policy['whole_page_gates_unchanged']=False;d['policy_sha256']=m.sha(m.canonical(policy))
 with pytest.raises(ValueError):m.plan(selected,raw,d,policy)
