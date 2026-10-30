"""Exact positive/inverse source pixels and additive codepoint spans."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image,ImageDraw
from local_vector_batch.native_image_reconstruction import raster_proof,reconstructed_text,load
from local_vector_batch.quality_gate import load_policy

class Source:
 width=100;height=100;chars=[];rects=[]
 def __init__(self,image):self.image=image
 def to_image(self,resolution):return SimpleNamespace(original=self.image)
class Independent:
 def get_objects(self):return []

def fixture():
 image=Image.new('RGB',(100,100),'white');d=ImageDraw.Draw(image);d.rectangle([10,10,90,30],fill='black');d.text((20,13),'A',fill='white');d.text((60,13),'B',fill='white');d.line([10,40,90,40],fill='black');
 for x in (10,50,90):d.line([x,30,x,40],fill='black')
 d.text((20,31),'1',fill='black');d.text((60,31),'2',fill='black')
 units=[{'literal':'A','source_bbox_PDF_points':[18,12,30,24]},{'literal':'B','source_bbox_PDF_points':[58,12,70,24]},{'literal':'1','source_bbox_PDF_points':[18,31,30,40]},{'literal':'2','source_bbox_PDF_points':[58,31,70,40]}]
 region={'region_id':'r','source_crop_bbox_PDF_points':[0,0,100,100],'source_bound_line_candidates':units};mixed={'regions':[region],'current_structured_observations':[{'region_id':'r','table_candidate':{'columns':['A','B'],'printed_numeric_literals':['1','2']}}]}
 rules=load(Path(__file__).parents[1]/'src/local_vector_batch/native_image_reconstruction_rules.json')
 return image,mixed,rules

def test_actual_inverse_foreground_and_rulings_are_separately_accounted():
 image,mixed,rules=fixture();p=raster_proof(Source(image),Independent(),mixed,load_policy(),rules)
 assert p['remaining_unaccounted_ink_pixels']==0
 assert p['source_paint_classes'][0]['inverse_foreground_coverage_unresolved_components']==0
 assert len(p['source_paint_classes'][0]['source_column_cells'])==2
 assert p['source_paint_classes'][0]['pixel_mask_interior_not_assumed']is True

@pytest.mark.parametrize('case',['missing_white_word','missing_dark_word','oversized_box','wrong_column_association','extra_data_row','missing_boundary'])
def test_real_uncovered_or_ambiguous_content_is_not_painted_away(case):
 image,mixed,rules=fixture();d=ImageDraw.Draw(image)
 if case=='missing_white_word':d.rectangle([40,14,45,20],fill='white')
 elif case=='missing_dark_word':d.rectangle([2,70,6,75],fill='black')
 elif case=='oversized_box':mixed['regions'][0]['source_bound_line_candidates'][0]['source_bbox_PDF_points']=[0,0,100,100]
 elif case=='wrong_column_association':mixed['current_structured_observations'][0]['table_candidate']['columns']=['B','A']
 elif case=='extra_data_row':d.line([10,45,90,45],fill='black')
 else:d.line([50,31,50,39],fill='white')
 with pytest.raises(ValueError):raster_proof(Source(image),Independent(),mixed,load_policy(),rules)

def test_additive_spans_preserve_every_original_native_unicode_codepoint():
 native=[{'kind':'native_source_line','text':'4.5 原文 ₹ ९१,७५० — intact.\n','source_bbox_PDF_points':[0,30,100,40],'source_quote':True}]
 image={'region_id':'r','source_crop_bbox_PDF_points':[0,0,100,20],'source_crop_render_sha256':'a'*64,'original_reader_record_count':2,'source_bound_line_candidates':[{'literal':'200','source_bbox_PDF_points':[10,5,20,12],'original_reader_refs':[{'reader':'one'},{'reader':'two'}]}]}
 text,spans=reconstructed_text(native,[image]);assert ''.join(s['exact_quote']for s in spans if s['kind']=='native_source_line')==native[0]['text']
 assert all(text[s['char_start']:s['char_start']+s['char_length']]==s['exact_quote']for s in spans)
 assert sum(s['char_length']for s in spans)==len(text)

def test_native_image_overlap_needs_explicit_source_deduplication():
 native=[{'kind':'native_source_line','text':'Native','source_bbox_PDF_points':[0,5,100,15]}]
 image={'source_crop_bbox_PDF_points':[0,0,100,20]}
 with pytest.raises(ValueError):reconstructed_text(native,[image])


def table_span_fixture():
 from local_vector_batch.native_image_reconstruction import raster_table_records
 specs=[('A',[10,0,20,8]),('B',[60,0,70,8]),('1',[10,20,20,28]),('2',[60,20,70,28])];text='';spans=[]
 for value,bbox in specs:
  spans.append({'kind':'reviewed_raster_literal','region_id':'r','source_bbox_PDF_points':bbox,'char_start':len(text),'char_length':len(value),'exact_quote':value,'original_reader_refs':[]});text+=value+' '
 mixed={'regions':[{'region_id':'r','source_crop_bbox_PDF_points':[0,0,100,40],'source_crop_render_sha256':'a'*64}],'current_structured_observations':[{'region_id':'r','table_candidate':{'columns':['A','B'],'rows':[['1','2']]}}]}
 return raster_table_records,mixed,spans,text

def test_normal_raster_tables_keep_each_exact_cell_quote_and_geometry():
 function,mixed,spans,text=table_span_fixture();tables=function(mixed,spans,1,'doc:p1','f'*64,text)
 assert tables[0]['cells']==[['A','B'],['1','2']]and len(tables[0]['cell_spans'])==4
 assert tables[0]['id'].split(':table')[0]=='doc:p1'
 assert tables[0]['table_semantics_reviewed']is False
 assert all(text[c['char_start']:c['char_start']+c['char_length']]==c['exact_quote']for c in tables[0]['cell_spans'])

@pytest.mark.parametrize('case',['wrong_header','missing_value','wrong_span','repeated_row_anchor'])
def test_table_association_cannot_infer_missing_or_ambiguous_cells(case):
 function,mixed,spans,text=table_span_fixture()
 if case=='wrong_header':mixed['current_structured_observations'][0]['table_candidate']['columns']=['B','A']
 elif case=='missing_value':spans.pop()
 elif case=='wrong_span':spans[-1]['char_start']=0
 else:spans.append({**spans[2],'source_bbox_PDF_points':[10,30,20,38]})
 with pytest.raises(ValueError):function(mixed,spans,1,'doc:p1','f'*64,text)
