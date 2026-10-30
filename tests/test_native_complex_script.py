"""A shared native PDF font map is not an independent linguistic proof."""
import hashlib
from local_vector_batch.quality_gate import assess_page, load_policy
from local_vector_batch.text_script import native_script_verification

def page(text):return {'page':1,'method':'pdf_text','text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'selected_quality':{'needs_ocr':False}}
def assess(value,independent,metadata=None):
 return assess_page(value,source_pdf_sha256='a'*64,package_pdf_sha256='a'*64,independent_text=independent,independent_metadata=metadata)
def test_unicode_and_two_native_readers_do_not_verify_complex_script_glyphs():
 value=page('भारत विनियम और 2026')
 result=assess(value,value['text'])
 assert result['outcome']=='repair_required'
 assert result['components']['language']['state']=='unknown'
 assert result['components']['language']['reason']=='native_complex_script_glyph_mapping_unverified'
 assert value['text']=='भारत विनियम और 2026'
def test_configuration_routes_observed_script_without_word_replacements():
 policy=load_policy();gate=native_script_verification('CERC भारत 2026',policy)
 assert gate['required_scripts']==['devanagari']
 assert native_script_verification('CERC 2026',policy)['state']=='not_required'
 policy['native_complex_script_min_characters']=100
 assert native_script_verification('CERC भारत 2026',policy)['state']=='not_required'
def test_independent_raster_reader_mismatch_is_flagged_not_repaired():
 value=page('भारत विनियम और 2026');other='भारत जिजियम और 2027'
 metadata={'source_pdf_sha256':'a'*64,'page':1,'selected_page_text_sha256':value['text_sha256'],'text_sha256':hashlib.sha256(other.encode()).hexdigest(),'engine':'rapidocr_onnx','version':'test','recognition_model':'devanagari_PP-OCRv5_rec_mobile','recognition_model_sha256':'b'*64,'recognition_languages':['hi']}
 result=assess(value,other,metadata)
 assert result['components']['language']['state']=='pass'
 assert result['components']['words_and_order']['state']=='fail'
 assert result['components']['ordered_numbers']['state']=='fail'
 assert result['outcome']=='repair_required'
 assert value['text']=='भारत विनियम और 2026'
def test_unbound_raster_language_metadata_does_not_unlock_native_script():
 value=page('भारत विनियम')
 result=assess(value,value['text'],{'engine':'rapidocr_onnx','recognition_languages':['hi']})
 assert result['components']['language']['state']=='unknown'

def test_stale_native_ready_cannot_bypass_new_policy_without_receipt(tmp_path):
    from local_vector_batch.quality_gate import apply_machine_quality_decisions
    value = {**page('भारत विनियम'), 'status': 'ready'}
    rows, accepted = apply_machine_quality_decisions([value], 'a'*64, tmp_path/'missing.json')
    assert rows[0]['status'] == 'review_required'
    assert rows[0]['script_verification']['state'] == 'unverified'
    assert accepted == 0
    assert value['status'] == 'ready'  # caller input remains unchanged
