import copy,json
from pathlib import Path
import pytest
from local_vector_batch.processed_vlm_regions import structure_candidate,model_identity,consume,sha
from local_vector_batch.held_crop_vlm import SCHEMA,PROMPTS

def test_html_is_data_and_th_does_not_approve_header():
 result=structure_candidate('<table><tr><th>Year</th><td>42.52</td></tr><tr><td></td><td>40</td></tr></table>','table')
 assert result['tables'][0]['rows']==[['Year','42.52'],['','40']];assert result['tables'][0]['header_semantics']=='not_inferred_from_td_or_th';assert all(c['semantic_header_role']=='not_inferred'for c in result['raw_markup_cells'])

def test_merged_cells_remain_raw_unsupported_without_padding():
 result=structure_candidate('<table><tr><td colspan="2">Hydro</td></tr><tr><td>40</td><td>years</td></tr></table>','table');assert result['tables'][0]['rows'][0]==['Hydro'];assert result['tables'][0]['unsupported_spans'];assert'merged_cell_geometry_not_resolved'in result['structure_flags'];assert result['raw_markup_cells'][0]['raw_attributes']['colspan']=='2'

def test_incomplete_html_is_not_complete_processed_table():assert'model_table_structure_incomplete_or_invalid'in structure_candidate('<table><tr><td>40','table')['structure_flags']
def test_plain_hindi_and_numbers_remain_literal():
 r=structure_candidate('टांडा टीपीएस (यूनिट 1)\n42.52','cell');assert r['text_lines']==['टांडा टीपीएस (यूनिट 1)','42.52'];assert not r['tables']
def test_revision_identity_is_not_performance_timing():
 a=model_identity({'model':'Qwen','revision':'abc','load_seconds':5});b=model_identity({'model':'Qwen','revision':'abc','load_seconds':10});assert a==b;assert model_identity({'model':'unknown'})['identity_state']=='incomplete_identity'

def fixture(tmp_path):
 source=tmp_path/'source.pdf';source.write_bytes(b'%PDF synthetic source binding fixture');image=tmp_path/'crop.png';image.write_bytes(b'fixed source crop fixture');case={'id':'one','source_pdf':str(source),'source_pdf_sha256':sha(source.read_bytes()),'page':1,'bbox':[1,1,2,2],'dpi':180,'image':str(image),'image_sha256':sha(image.read_bytes()),'kind':'cell','prompt':PROMPTS['cell'],'max_tokens':256};cp=tmp_path/'cases.json';cp.write_text(json.dumps({'schema_version':SCHEMA,'cases':[case]}));result={k:case[k]for k in('id','source_pdf_sha256','page','bbox','dpi','image_sha256','kind')};result.update(status='processed_candidate',text='40 years',generated_tokens=4,possible_token_cap=False);doc={'schema_version':SCHEMA,'cases_sha256':sha(cp.read_bytes()),'results':[result],'runtime':{'model':'Qwen','revision':'abc'},'source_quality_clearances':0,'human_approvals':0,'publication_eligible':False};rp=tmp_path/'readings.json';rp.write_text(json.dumps(doc));return cp,rp,doc,image

def test_bound_processing_no_vote_or_page_clearance(tmp_path,monkeypatch):
 from local_vector_batch import processed_vlm_regions as module
 cp,rp,doc,image=fixture(tmp_path);monkeypatch.setattr(module,'render',lambda case:image.read_bytes());out=tmp_path/'out';r=consume(cp,rp,out);assert r['processed_regions']==1 and r['source_quality_pages_cleared']==0 and r['human_votes_created']==0;assert json.loads((out/'processed-regions.jsonl').read_text())['raw_model_reading']=='40 years'

@pytest.mark.parametrize('mutation',['source','crop','approval','cap'])
def test_wrong_source_image_approval_or_truncation_rejected(tmp_path,monkeypatch,mutation):
 from local_vector_batch import processed_vlm_regions as module
 cp,rp,doc,image=fixture(tmp_path);monkeypatch.setattr(module,'render',lambda case:image.read_bytes())
 if mutation=='source':doc['results'][0]['source_pdf_sha256']='0'*64
 elif mutation=='crop':image.write_bytes(b'different crop')
 elif mutation=='approval':doc['human_approvals']=1
 else:doc['results'][0]['possible_token_cap']=True
 rp.write_text(json.dumps(doc))
 with pytest.raises(ValueError):consume(cp,rp,tmp_path/'out')

def test_missing_reading_is_retry_not_a_human_question(tmp_path):
 cp,rp,doc,image=fixture(tmp_path);doc['results']=[];rp.write_text(json.dumps(doc));out=tmp_path/'out';r=consume(cp,rp,out);assert r['processed_regions']==0 and r['retry_or_unrun_regions']==1 and r['uncertain_or_conflict_regions']==0


def test_model_cell_markup_offsets_are_exact_and_not_source_page_spans():
 text='<table>\n<tr><td>भारत &amp; 40</td><th colspan="2">years</th></tr></table>'
 result=structure_candidate(text,'table')
 for cell in result['raw_markup_cells']:
  a=cell['model_markup_char_start'];b=a+cell['model_markup_char_length'];assert text[a:b]==cell['exact_model_markup'];assert cell['offset_encoding']=='codepoint';assert cell['semantic_header_role']=='not_inferred'
