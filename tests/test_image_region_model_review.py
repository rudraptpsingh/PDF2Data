"""Model-origin candidate review never acquires human/page approval."""
from copy import deepcopy
import pytest
from local_vector_batch.image_region_review import validate_model_review,ORIGIN

@pytest.fixture()
def data():
 region={'id':'r','source_pdf_sha256':'a'*64,'page':1,'page_text_sha256':'b'*64,'parent_package_sha256':'c'*64,'source_crop_bbox_PDF_points':[10,20,30,40],'source_crop_render_sha256':'d'*64,'render_dpi':240}
 rep={'regions':[region]};lines=[{'region_id':'r','reader':'vision_cpu','line_index':0,'literal':'नाम ₹ ९१,७५०','source_bbox_PDF_points':[11,21,29,25]},{'region_id':'r','reader':'rapidocr_cpu','line_index':0,'literal':'ambiguous I/l','source_bbox_PDF_points':[11,21,29,25]}];gates={'source_render':{'state':'unknown'},'layout':{'state':'unknown'}};comp={'unresolved_readings':lines,'gates':gates}
 bindings={'source_packet_manifest_sha256':'e'*64,'representation_sha256':'f'*64,'completeness_sha256':'0'*64}
 image={'region_id':'r','source_pdf_sha256':region['source_pdf_sha256'],'page':1,'page_text_sha256':region['page_text_sha256'],'parent_package_sha256':region['parent_package_sha256'],'crop_bbox_PDF_points':region['source_crop_bbox_PDF_points'],'crop_render_sha256':region['source_crop_render_sha256'],'render_dpi':240,'entire_image_visually_inspected':True,'review_origin':ORIGIN,'human_approval':False,'table_candidate':{'columns':['field'],'rows':[['नाम ₹ ९१,७५०']]}}
 decisions=[{**lines[0],'source_crop_render_sha256':region['source_crop_render_sha256'],'source_crop_bbox_PDF_points':region['source_crop_bbox_PDF_points'],'status':'model_assisted_resolved','reviewed_literal':'नाम ₹ ९१,७५०','reason':'Synthetic source-observation binding fixture only','review_origin':ORIGIN,'human_approval':False,'promotes_page':False},{**lines[1],'source_crop_render_sha256':region['source_crop_render_sha256'],'source_crop_bbox_PDF_points':region['source_crop_bbox_PDF_points'],'status':'unresolved','reviewed_literal':None,'reason':'Cannot infer indistinguishable glyph','review_origin':ORIGIN,'human_approval':False,'promotes_page':False}]
 review={'contract_version':'pdf2data.image-region-model-visual-review.v1','reviewer_kind':'model','review_origin':ORIGIN,**bindings,'whole_images_reviewed':[image],'reading_decisions':decisions,'counts':{'raw_reader_disagreement_records':2,'model_assisted_resolved_records':1,'still_unresolved_records':1},'human_approvals':0,'source_quality_clearances':0,'selected_text_modified':False,'source_packages_modified':False,'legal_correctness_certified':False,'publication_eligible':False,'all_old_readings_preserved':True}
 return rep,comp,review,bindings

def test_review_keeps_unicode_raw_literals_and_every_quality_hold(data):
 rep,comp,review,b=data;r=validate_model_review(*data)
 assert r['counts']==review['counts']and r['resolved_candidate_observations'][0]['reviewed_literal']=='नाम ₹ ९१,७५०'
 assert comp['unresolved_readings'][0]['literal']=='नाम ₹ ९१,७५०'
 assert r['source_quality_gates_preserved']==comp['gates']
 assert not r['page_complete']and not r['embedding_candidate_allowed']and r['human_approvals']==0

@pytest.mark.parametrize('change',['human_origin','quality_promotion','false_counts','missing_image','wrong_crop','wrong_original_literal','missing_decision','duplicate_decision','invented_unclear','invalid_unicode','wrong_hash','boolean_index'])
def test_origin_scope_and_source_bindings_fail_closed(data,change):
 rep,comp,review,b=deepcopy(data)
 if change=='human_origin':review['review_origin']='human_user_current_thread'
 elif change=='quality_promotion':review['source_quality_clearances']=1
 elif change=='false_counts':review['counts']['model_assisted_resolved_records']=2
 elif change=='missing_image':review['whole_images_reviewed']=[]
 elif change=='wrong_crop':review['whole_images_reviewed'][0]['crop_render_sha256']='x'*64
 elif change=='wrong_original_literal':review['reading_decisions'][0]['literal']='changed'
 elif change=='missing_decision':review['reading_decisions'].pop()
 elif change=='duplicate_decision':review['reading_decisions'][1]=deepcopy(review['reading_decisions'][0])
 elif change=='invented_unclear':review['reading_decisions'][1]['reviewed_literal']='Id.'
 elif change=='invalid_unicode':review['reading_decisions'][0]['reviewed_literal']='\udc65'
 elif change=='wrong_hash':review['completeness_sha256']='x'*64
 else:review['reading_decisions'][0]['line_index']=False
 with pytest.raises((ValueError,UnicodeError)):validate_model_review(rep,comp,review,b)


def test_UI_retries_are_idempotent_and_focus_scoped():
 from local_vector_batch.image_region_review import deduplicate_ui_feedback,UI_KEYS
 question={key:'hash-'+key for key in UI_KEYS};question['page']=8
 row={**question,'kind':'ocr_disagreement_feedback','decision':'independent','note':'','disagreement_kind':'text_change','proposal_choice':None,'proposal_basis':'needs_source_review','promotes_page':False,'created_at':'first'}
 effective,retries=deduplicate_ui_feedback([row,{**row,'created_at':'retry'}],[question])
 assert len(effective)==1 and retries==1
 assert len(next(iter(effective.values()))['events'])==2

@pytest.mark.parametrize('change',['stale_focus','opposite_retry','page_promotion','unknown_kind'])
def test_UI_feedback_cannot_cross_scope_or_conflict(change):
 from local_vector_batch.image_region_review import deduplicate_ui_feedback,UI_KEYS
 question={key:'hash-'+key for key in UI_KEYS};question['page']=8
 row={**question,'kind':'ocr_disagreement_feedback','decision':'independent','note':'','disagreement_kind':'text_change','proposal_choice':None,'proposal_basis':'needs_source_review','promotes_page':False};other=deepcopy(row)
 if change=='stale_focus':other['focus_sha256']='different'
 elif change=='opposite_retry':other['decision']='selected'
 elif change=='page_promotion':other['promotes_page']=True
 else:other['kind']='human_approval'
 with pytest.raises(ValueError):deduplicate_ui_feedback([row,other],[question])
