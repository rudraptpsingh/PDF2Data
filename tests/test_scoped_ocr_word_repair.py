"""Synthetic exact-scope controls; actual-source result is a separate receipt."""
from copy import deepcopy
import pytest
from local_vector_batch import scoped_ocr_word_repair as m


def fixture():
    old='पदधति,';new='पद्धति,';box=[.1,.2,.3,.3]
    raw=[{'text':'😀 संदर्भ','words':[{'text':'😀','bbox':[0,0,.1,.1]},{'text':'संदर्भ','bbox':[.1,0,.2,.1]}],'bbox':[0,0,.2,.1]},
        {'text':old+' आवेदन','words':[{'text':old,'bbox':box},{'text':'आवेदन','bbox':[.4,.2,.5,.3]}],'bbox':[.1,.2,.5,.3]}]
    text='\n'.join(l['text']for l in raw);page={'page':1,'method':'rapidocr','text':text,'text_sha256':m.sha(text),'ocr':{'lines':raw,'text':text},'pdf_width':200,'pdf_height':200}
    other={'engine':'vision','page':1,'source_pdf_sha256':'a'*64,'selected_page_text_sha256':page['text_sha256'],'text':new+' आवेदन','text_sha256':m.sha(new+' आवेदन'),
        'lines':[{'text':new+' आवेदन','words':[{'text':new,'bbox':box},{'text':'आवेदन','bbox':[.4,.2,.5,.3]}]}]}
    profile=m.rules()['permitted_model_profiles'][0]
    model={'runtime':profile,'results':[{'id':'case','status':'processed_candidate','review_origin':'local_vision_model','source_pdf_sha256':'a'*64,'page':1,'bbox':[0,0,200,200],'dpi':240,'image_sha256':'b'*64,'text':new+' आवेदन'}]}
    decision={'contract_version':m.rules()['contract_version'],'rule':m.rules()['allowed_rule'],'review_origin':m.rules()['allowed_review_origin'],'source_pdf_sha256':'a'*64,'page':1,
        'original_page_text_sha256':page['text_sha256'],'independent_canonical_sha256':m.sha(m.canonical(other)),'primary_line_index':1,'primary_word_index':0,'independent_line_index':0,'independent_word_index':0,
        'original_literal':old,'proposed_literal':new,'crop':{'bbox':[0,0,200,200],'dpi':240,'sha256':'b'*64},'model_readings_sha256':m.sha(m.canonical(model)),'model_case_id':'case'}
    return page,other,decision,model


def test_exact_codepoint_span_single_word_only_and_raw_inputs_immutable():
    page,other,d,model=fixture();before=deepcopy((page,other,d,model));r=m.plan(page,other,d,model)
    assert (page,other,d,model)==before
    old=r['proof']['original_span'];new=r['proof']['new_span'];assert old['char_start']==len('😀 संदर्भ\n')
    assert page['text'][old['char_start']:old['char_start']+old['char_length']]=='पदधति,'
    assert r['proposed_page']['text'][new['char_start']:new['char_start']+new['char_length']]=='पद्धति,'
    assert r['proof']['whole_page_quality_clearance']is False and r['proof']['human_review']is False
    assert r['proposed_page']['text'].endswith(' आवेदन')


@pytest.mark.parametrize('attack',['letter','number','word_whitespace','wrong_source','wrong_page_type','wrong_independent','model_wrong','model_duplicate','model_hash','crop_clipped','geometry','unmapped_prefix','ambiguous_independent','weaken_quality'])
def test_inexact_or_wide_or_conflicting_changes_are_rejected(attack):
    page,other,d,model=fixture();policy=m.rules()
    if attack=='letter':d['proposed_literal']='बद्धति,'
    elif attack=='number':d['proposed_literal']='5'
    elif attack=='word_whitespace':d['proposed_literal']='पद्धति, आवेदन'
    elif attack=='wrong_source':other['source_pdf_sha256']='f'*64;d['independent_canonical_sha256']=m.sha(m.canonical(other))
    elif attack=='wrong_page_type':d['page']=True
    elif attack=='wrong_independent':other['lines'][0]['words'][0]['text']='अन्य';d['independent_canonical_sha256']=m.sha(m.canonical(other))
    elif attack=='model_wrong':model['results'][0]['text']='अनुज्ञाप्तिधारी';d['model_readings_sha256']=m.sha(m.canonical(model))
    elif attack=='model_duplicate':model['results'][0]['text']*=2;d['model_readings_sha256']=m.sha(m.canonical(model))
    elif attack=='model_hash':d['model_readings_sha256']='0'*64
    elif attack=='crop_clipped':d['crop']['bbox']=[50,50,60,60];model['results'][0]['bbox']=[50,50,60,60];d['model_readings_sha256']=m.sha(m.canonical(model))
    elif attack=='geometry':other['lines'][0]['words'][0]['bbox']=[.7,.7,.8,.8];d['independent_canonical_sha256']=m.sha(m.canonical(other))
    elif attack=='unmapped_prefix':page['ocr']['lines'][1]['text']='missing '+page['ocr']['lines'][1]['text'];page['text']='\n'.join(l['text']for l in page['ocr']['lines']);page['text_sha256']=m.sha(page['text']);d['original_page_text_sha256']=page['text_sha256'];other['selected_page_text_sha256']=page['text_sha256'];d['independent_canonical_sha256']=m.sha(m.canonical(other))
    elif attack=='ambiguous_independent':other['lines'][0]['words'].append(deepcopy(other['lines'][0]['words'][0]));d['independent_canonical_sha256']=m.sha(m.canonical(other))
    else:policy['whole_page_gates_unchanged']=False
    with pytest.raises(ValueError):m.plan(page,other,d,model,policy)


def test_a_failed_word_does_not_permit_canonicalized_matra_loss():
    assert m.base('कि')==m.base('के')
    page,other,d,model=fixture();d['proposed_literal']='पदधति,'
    with pytest.raises(ValueError):m.plan(page,other,d,model)


def test_source_supported_single_printed_digit_keeps_raw_double_script_reading():
    page,other,d,model=fixture()
    old,new='5५','5'
    page['ocr']['lines'][1]['text']=old+' आवेदन'
    page['ocr']['lines'][1]['words'][0]['text']=old
    page['text']='\n'.join(l['text']for l in page['ocr']['lines'])
    page['text_sha256']=m.sha(page['text'])
    other['text']=new+' आवेदन';other['text_sha256']=m.sha(other['text'])
    other['lines'][0]['text']=other['text'];other['lines'][0]['words'][0]['text']=new
    other['selected_page_text_sha256']=page['text_sha256']
    model['results'][0]['text']=new+' आवेदन'
    d.update(rule=m.rule_for(old,new),original_literal=old,proposed_literal=new,
             original_page_text_sha256=page['text_sha256'],independent_canonical_sha256=m.sha(m.canonical(other)),
             model_readings_sha256=m.sha(m.canonical(model)))
    before=deepcopy(page);result=m.plan(page,other,d,model)
    assert page==before and result['proposed_page']['text'].endswith('5 आवेदन')
    assert result['proof']['numeric_value_unchanged']is True
    assert result['proof']['base_letters_digits_operators_unchanged']is False
    assert result['proof']['whole_page_quality_clearance']is False


@pytest.mark.parametrize('old,new',[('55','5'),('५५','५'),('5६','5'),('55५','5'),('5५','6'),('५','5'),('1१','2')])
def test_digit_rule_does_not_suppress_actual_repetition_or_translate_other_values(old,new):
    assert m.rule_for(old,new)!='independent_crop_supported_mixed_script_duplicate_digit'
