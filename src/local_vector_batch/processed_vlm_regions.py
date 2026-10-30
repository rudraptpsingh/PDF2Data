"""Turn bound local vision readings into processed candidate data, never votes.

HTML is parsed as inert cell strings; model tags do not establish header roles.
Merged or conflicting structure stays explicit and raw readings are retained.
"""
import argparse,hashlib,json
from html.parser import HTMLParser
from pathlib import Path
from .held_crop_vlm import SCHEMA,PROMPTS,render
from .l1_qwen import html_tables

CONTRACT='pdf2data.processed-vlm-region.v1'
def sha(value):return hashlib.sha256(value).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def model_identity(runtime):
 value={k:runtime.get(k)for k in('provider','model','revision','model_digest','model_details')if runtime.get(k)is not None}
 value['identity_state']='reported_model_identity'if value.get('model')and(value.get('revision')or value.get('model_digest'))else'incomplete_identity'
 value['identity_sha256']=sha(canonical(value).encode());return value

def structure_candidate(text,kind):
 tables,valid=html_tables(text);flags=[]
 if kind=='table'and(not tables or not valid):flags.append('model_table_structure_incomplete_or_invalid')
 class Markup(HTMLParser):
  def __init__(self):
   super().__init__();self.cells=[];self.active=[];self.lines=[0]
   for line in text.splitlines(keepends=True):self.lines.append(self.lines[-1]+len(line))
  def source_offset(self):
   line,column=self.getpos();return self.lines[line-1]+column
  def handle_starttag(self,tag,attrs):
   if tag in('td','th'):
    cell={'markup_tag':tag,'raw_attributes':dict(attrs),'semantic_header_role':'not_inferred','model_markup_char_start':self.source_offset()};self.cells.append(cell);self.active.append(cell)
  def handle_endtag(self,tag):
   if tag in('td','th')and self.active:
    cell=self.active.pop();start=cell['model_markup_char_start'];end=text.find('>',self.source_offset())+1
    if end>self.source_offset():cell.update(model_markup_char_length=end-start,exact_model_markup=text[start:end],offset_encoding='codepoint')
 markup=Markup();markup.feed(text);markup.close()
 for table in tables:
  table['row_lengths']=[len(row)for row in table['rows']];table['cell_count']=sum(table['row_lengths']);table['header_semantics']='not_inferred_from_td_or_th'
  if table['unsupported_spans']:flags.append('merged_cell_geometry_not_resolved')
  if table['invalid_structure']:flags.append('model_table_structure_incomplete_or_invalid')
  if len(set(table['row_lengths']))>1:flags.append('unequal_row_lengths_need_source_geometry')
 return {'kind':'model_table_candidate'if tables else'model_text_candidate','tables':tables,'raw_markup_cells':markup.cells,'text_lines':text.splitlines()if not tables else None,'structure_flags':sorted(set(flags)),'semantic_review':'not_run'}

def native_table_comparison(case,structure):
 """Compare a retained native candidate only; either side can still be wrong."""
 path=Path(case['source_pdf']).parent/'extraction/tables.json'
 if case['kind']!='table'or not path.exists():return None,[]
 candidates=[];box=case['bbox']
 for table in read(path):
  b=table['bbox']
  if table['page']==case['page']and b[0]>=box[0]-.5 and b[1]>=box[1]-.5 and b[2]<=box[2]+.5 and b[3]<=box[3]+.5:candidates.append(table)
 if len(candidates)!=1 or len(structure['tables'])!=1:return {'state':'native_candidate_comparison_unavailable','native_candidates_in_crop':len(candidates)},[]
 native=candidates[0];matrix=native.get('cells',[]);actual=structure['tables'][0]['rows']
 same=matrix==actual
 return {'state':'exact_raw_matrix_agreement'if same else'model_native_candidate_conflict','native_table_version_id':native.get('table_version_id'),'native_row_lengths':[len(row)for row in matrix],'model_row_lengths':[len(row)for row in actual],'comparison_is_not_source_quality_or_semantic_approval':True},[]if same else['model_native_candidate_matrix_conflict']

def consume(cases_path,readings_path,output):
 cases_path,readings_path,output=map(Path,(cases_path,readings_path,output))
 if output.exists():raise ValueError('Choose new immutable processed-data output')
 cases_doc=read(cases_path);readings=read(readings_path)
 if cases_doc.get('schema_version')!=SCHEMA or readings.get('schema_version')!=SCHEMA or readings.get('cases_sha256')!=sha(cases_path.read_bytes()):raise ValueError('Vision cases/reading contract differs')
 if readings.get('source_quality_clearances')!=0 or readings.get('human_approvals')!=0 or readings.get('publication_eligible')is not False:raise ValueError('Vision reading contains unsupported approval')
 cases={c['id']:c for c in cases_doc['cases']};results={r['id']:r for r in readings['results']}
 if len(cases)!=len(cases_doc['cases'])or len(results)!=len(readings['results'])or not set(results)<=set(cases):raise ValueError('Unknown or duplicate vision region')
 identity=model_identity(readings.get('runtime',{}));processed=[];questions=[];retries=[];sources={}
 for key,case in cases.items():
  result=results.get(key)
  if not result or result.get('status')!='processed_candidate':
   retries.append({'id':key,'reason':'model_reading_absent_or_incomplete','last_status':result.get('status')if result else'not_run','human_review_required':False,'source_quality_clearance':False});continue
  for field in('source_pdf_sha256','page','bbox','dpi','image_sha256','kind'):
   if result.get(field)!=case.get(field):raise ValueError('Model result source binding differs: '+field)
  if case.get('prompt')!=PROMPTS[case['kind']]or result.get('possible_token_cap')is not False or type(result.get('generated_tokens'))is not int or not 0<=result['generated_tokens']<case['max_tokens']:raise ValueError('Unbounded or incomplete model reading')
  source=Path(case['source_pdf']);before=source.stat()
  if source not in sources:sources[source]=sha(source.read_bytes())
  if sources[source]!=case['source_pdf_sha256']or sha(Path(case['image']).read_bytes())!=case['image_sha256']or sha(render(case))!=case['image_sha256']:raise ValueError('Original PDF/crop differs from bound source')
  after=source.stat()
  if(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('Source changed during processed-region derivation')
  raw=result['text'];raw.encode('utf-8');structure=structure_candidate(raw,case['kind']);comparison,extra=native_table_comparison(case,structure);flags=sorted(set(structure['structure_flags']+extra+(['model_identity_incomplete']if identity['identity_state']=='incomplete_identity'else[])))
  row={'contract_version':CONTRACT,'id':key,'source':{k:case[k]for k in('source_pdf','source_pdf_sha256','page','bbox','dpi','image_sha256','kind')},'model_identity':identity,'raw_model_reading':raw,'raw_model_reading_sha256':sha(raw.encode()),'model_text_span':{'char_start':0,'char_length':len(raw),'offset_encoding':'codepoint','exact_quote':raw},'text_to_source_mapping':{'source_region_bbox':case['bbox'],'per_cell_source_coordinates':'unresolved_not_inferred_from_HTML','model_text_span_is_not_a_source_page_text_span':True},'generated_tokens':result['generated_tokens'],'model_elapsed_seconds':result.get('elapsed_seconds'),'candidate_structure':structure,'native_candidate_comparison':comparison,'uncertainty_flags':flags,'processing_state':'processed_candidate','review_origin':'local_vision_model_not_human','source_quality_clearance':False,'semantic_review':'not_run','publication_eligible':False,'cases_sha256':sha(cases_path.read_bytes()),'readings_sha256':sha(readings_path.read_bytes())};processed.append(row)
  if flags:questions.append({'id':key,'source':row['source'],'reasons':flags,'processed_region_id':key,'question_type':'source_or_structure_conflict_only','human_vote_created':False,'source_quality_clearance':False})
 output.mkdir(parents=True)
 for filename,rows in [('processed-regions.jsonl',processed),('uncertain-regions.jsonl',questions),('retry-regions.jsonl',retries)]:
  (output/filename).write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n'for row in rows))
 summary={'contract_version':CONTRACT,'status':'complete_readings_snapshot'if len(processed)==len(cases)else'partial_readings_snapshot','model_identity':identity,'input_regions':len(cases),'processed_regions':len(processed),'uncertain_or_conflict_regions':len(questions),'retry_or_unrun_regions':len(retries),'processed_source_pages':len({(r['source']['source_pdf_sha256'],r['source']['page'])for r in processed}),'source_quality_pages_cleared':0,'human_votes_created':0,'publication_eligible':False,'raw_model_outputs_preserved':True,'implementation_sha256':sha(Path(__file__).read_bytes()),'readings_sha256':sha(readings_path.read_bytes()),'cases_sha256':sha(cases_path.read_bytes())}
 (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cases',required=True,type=Path);p.add_argument('--readings',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();print(json.dumps(consume(a.cases,a.readings,a.output)))
