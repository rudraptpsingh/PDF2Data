import hashlib
from copy import deepcopy
import pytest
from local_vector_batch.ocr_policy_selection import select


def evidence(text='English source text '*20):
    independent={'source_pdf_sha256':'a'*64,'page':1,'source_image_sha256':'b'*64,
       'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'mean_confidence':.99}
    review={'source_pdf_sha256':'a'*64,'page':1,'source_image_sha256':'b'*64,
       'origin':'model_assisted_original_source_raster_orientation','source_orientation_degrees':0,'human_approval':False}
    return independent,review


def choose(independent,review=None):
    return select(independent,source_pdf_sha256='a'*64,page=1,source_image_sha256='b'*64,orientation_review=review)


def test_latin_selects_English_model_not_always_Devanagari():
    i,r=evidence();s=choose(i,r)
    assert s['ocr_language']=='eng'and s['ocr_use_angle_classifier']is False
    assert not s['quality_approval']and not s['requires_script_exception_review']


def test_Devanagari_or_mixed_retains_matching_recognizer():
    i,r=evidence('भारत सरकार विद्युत आयोग '*20)
    assert choose(i,r)['ocr_language']=='eng+hin'


def test_no_source_orientation_review_keeps_provider_default():
    i,_=evidence();assert choose(i)['ocr_use_angle_classifier']is None


def test_reader_geometry_can_only_propose_upright_candidate_not_approve_it():
    i,_=evidence();i.update(source_image_dimensions=[1000,2000],source_pdf_rotation_degrees=0,
       lines=[{'text':'Actual independently read horizontal source line','bbox':[.1,.1+n*.1,.9,.12+n*.1]}for n in range(4)])
    s=choose(i);assert s['ocr_use_angle_classifier']is False and not s['orientation_proved']and not s['quality_approval']
    i['source_pdf_rotation_degrees']=180;assert choose(i)['ocr_use_angle_classifier']is None
    i['source_pdf_rotation_degrees']=0;i['mean_confidence']=.8;assert choose(i)['ocr_use_angle_classifier']is None


def test_vertical_or_stale_box_does_not_propose_upright_candidate():
    i,_=evidence();i.update(source_image_dimensions=[1000,2000],source_pdf_rotation_degrees=0,
       lines=[{'text':'Actual independently read source line','bbox':[.1,.1,.12,.9]}for n in range(4)])
    assert choose(i)['ocr_use_angle_classifier']is None
    i['lines'][0]['bbox']=[0,0,2,.5];assert choose(i)['ocr_use_angle_classifier']is None


@pytest.mark.parametrize('change',['pdf','page','image','text','low_confidence','unknown_script','false_human','rotation'])
def test_stale_source_rejects_and_low_confidence_routes_without_approval(change):
    i,r=evidence()
    if change=='pdf':i['source_pdf_sha256']='c'*64
    elif change=='page':i['page']=2
    elif change=='image':r['source_image_sha256']='c'*64
    elif change=='text':i['text']='invented'
    elif change=='low_confidence':
        i['mean_confidence']=.6;assert choose(i,r)['requires_script_exception_review'];return
    elif change=='unknown_script':
        i,r=evidence('123');s=choose(i,r);assert s['ocr_language']is None and s['requires_script_exception_review'];return
    elif change=='false_human':r['human_approval']=True
    else:r['source_orientation_degrees']=180
    with pytest.raises(ValueError):choose(i,r)


def test_explicit_classifier_false_passes_public_RapidOCR_option(monkeypatch):
    import local_vector_batch.ocr as o
    calls=[]
    class Result:
        boxes=[];txts=[];scores=[];word_results=[]
    def reader(image,**kw):calls.append(kw);return Result()
    monkeypatch.setattr(o,'_rapidocr_reader',lambda name:reader)
    o._rapidocr_lines(None,'en',use_angle_classifier=False)
    assert calls==[{'return_word_box':True,'use_cls':False}]
    calls.clear();o._rapidocr_lines(None,'en');assert calls==[{'return_word_box':True}]


def test_angle_control_rejects_nonboolean_without_model_calls():
    from local_vector_batch.ocr import LocalOCR
    with pytest.raises(ValueError,match='angle classifier'):LocalOCR('off',use_angle_classifier='false')


def test_single_thread_budget_reaches_cached_factory(monkeypatch):
    import local_vector_batch.ocr as o
    calls=[]
    class Result:
        boxes=[];txts=[];scores=[];word_results=[]
    def factory(*args):calls.append(args);return lambda image,**kw:Result()
    monkeypatch.setattr(o,'_rapidocr_reader',factory)
    o._rapidocr_lines(None,'en',cpu_threads=1)
    assert calls==[('en',1)]
    for invalid in(0,True,9):
        with pytest.raises(ValueError,match='CPU threads'):o.LocalOCR('off',cpu_threads=invalid)
