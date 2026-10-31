"""Persistent local vision reader for source-bound held regions.

This stage emits processed candidate text, not human votes or page approvals.
One model load serves the entire run; each crop gets a durable terminal result.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, time
from io import BytesIO
from pathlib import Path

SCHEMA='pdf2data.held-crop-vlm.v1'
PROMPTS={
 'cell':'Transcribe only the visible text in this source cell. Preserve Hindi, superscripts, numbers, signs and punctuation. Do not infer missing text or explain. Return only the literal transcription.',
 'table':'Transcribe this source table as minimal HTML table markup. Preserve every row, column, merged cell, blank cell, number and Hindi character. Use rowspan and colspan when printed. Do not add CSS, explanations, inferred labels or repeated padding. Return only the table.',
 'region':'Transcribe all visible text in this source region in reading order. Preserve Hindi, numbers, signs, headings and footnotes. Do not infer obscured characters. Return only the transcription.'}

def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def identity(path):
 s=Path(path).stat();return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def save(path,data):
 path=Path(path);temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');temp.replace(path)

def render(case):
 import pdfplumber
 box=case['bbox'];dpi=case['dpi']
 if len(box)!=4 or not all(type(v)in(int,float)and math.isfinite(v)for v in box)or not(72<=dpi<=300):raise ValueError('Invalid source crop')
 with pdfplumber.open(case['source_pdf'])as pdf:
  if type(case['page'])is not int or not 1<=case['page']<=len(pdf.pages):raise ValueError('Invalid physical page')
  page=pdf.pages[case['page']-1]
  if not(0<=box[0]<box[2]<=page.width and 0<=box[1]<box[3]<=page.height):raise ValueError('Crop outside physical page')
  image=page.crop(tuple(box)).to_image(resolution=dpi).original
  buf=BytesIO();image.save(buf,format='PNG');return buf.getvalue()

def prepare(tasks,output):
 output=Path(output).resolve()
 if output.exists():raise ValueError('New immutable crop workspace required')
 ids=[t['id']for t in tasks]
 if not ids or len(set(ids))!=len(ids):raise ValueError('Empty or duplicate crop identities')
 verified={};cases=[]
 for task in tasks:
  source=Path(task['source_pdf']).resolve()
  if output==source or output.is_relative_to(source.parent):raise ValueError('Crop workspace cannot alter source package')
  if source not in verified:verified[source]=sha(source.read_bytes())
  if verified[source]!=task['source_pdf_sha256']:raise ValueError('Source PDF identity differs')
  if task['kind']not in PROMPTS:raise ValueError('Unknown crop kind')
 output.mkdir(parents=True)
 for i,task in enumerate(tasks):
  case={**task,'source_pdf':str(Path(task['source_pdf']).resolve()),'dpi':task.get('dpi',180)}
  data=render(case);image=output/f'crop-{i+1:04}.png';image.write_bytes(data)
  case.update(image=str(image),image_sha256=sha(data),prompt=PROMPTS[case['kind']],max_tokens=256 if case['kind']=='cell'else 1536)
  cases.append(case)
 save(output/'cases.json',{'schema_version':SCHEMA,'cases':cases,'source_packages_modified':False})
 return output/'cases.json'

def qwen_engine():
 from .l1_qwen import MODEL,REVISION
 if os.environ.get('HF_HUB_OFFLINE')!='1'or os.environ.get('TRANSFORMERS_OFFLINE')!='1':raise ValueError('Offline model execution required')
 import mlx.core as mx
 from mlx_vlm import load as load_model,generate
 if not mx.metal.is_available():raise ValueError('Metal GPU unavailable')
 mx.set_default_device(mx.gpu)
 path=Path.home()/'.cache/huggingface/hub'/('models--'+MODEL.replace('/','--'))/'snapshots'/REVISION
 if not path.is_dir():raise ValueError('Pinned local Qwen snapshot unavailable')
 tick=time.perf_counter();model,processor=load_model(str(path))
 metadata={'model':MODEL,'revision':REVISION,'device':str(mx.default_device()),'load_seconds':time.perf_counter()-tick}
 def infer(case):
  prompt=processor.apply_chat_template([{'role':'user','content':[{'type':'image'},{'type':'text','text':case['prompt']}]}],tokenize=False,add_generation_prompt=True)
  result=generate(model,processor,prompt,image=case['image'],max_tokens=case['max_tokens'],temp=0.,verbose=False)
  return {'text':result.text.strip(),'generated_tokens':getattr(result,'generation_tokens',None)}
 return metadata,infer

def run(cases_path,output,*,engine_factory=qwen_engine,resume=False):
 from filelock import FileLock
 cases_path,output=Path(cases_path).resolve(),Path(output).resolve();data=load(cases_path)
 if data.get('schema_version')!=SCHEMA:raise ValueError('Unknown crop schema')
 cases=data['cases'];ids=[c['id']for c in cases]
 if not ids or len(ids)!=len(set(ids)):raise ValueError('Duplicate or empty crop identities')
 if output==cases_path or any(output.is_relative_to(Path(c['source_pdf']).parent)for c in cases):raise ValueError('Output cannot overwrite source inputs')
 for c in cases:
  if c['kind']not in PROMPTS or c['prompt']!=PROMPTS[c['kind']]or c['max_tokens']!=(256 if c['kind']=='cell'else 1536):raise ValueError('Unexpected crop prompt/budget')
 output.parent.mkdir(parents=True,exist_ok=True)
 contract={'schema_version':SCHEMA,'cases_sha256':sha(cases_path.read_bytes())}
 with FileLock(str(output)+'.lock',timeout=0):
  record={**contract,'results':[],'source_quality_clearances':0,'publication_eligible':False,'source_packages_modified':False,'human_approvals':0}
  if output.exists():
   if not resume:raise ValueError('Output exists; explicit resume required')
   record=load(output)
   if any(record.get(k)!=v for k,v in contract.items()):raise ValueError('Resume source contract changed')
   if len({r['id']for r in record['results']})!=len(record['results'])or any(r['id']not in ids for r in record['results']):raise ValueError('Invalid saved result identity')
  done={r['id']for r in record['results']if r.get('status')=='processed_candidate'}
  # Bind the source once per run. A fresh final crop comparison below detects
  # source edits affecting this region; no document-wide proof is repeated.
  source_hashes={};source_states={}
  for c in cases:
   p=Path(c['source_pdf'])
   if p not in source_hashes:
    before=identity(p);source_hashes[p]=sha(p.read_bytes())
    if identity(p)!=before:raise ValueError('Source changed during binding')
    source_states[p]=before
   if source_hashes[p]!=c['source_pdf_sha256']or sha(Path(c['image']).read_bytes())!=c['image_sha256']or sha(render(c))!=c['image_sha256']:raise ValueError('Source/crop identity differs')
  if len(done)==len(cases):return record
  metadata,infer=engine_factory();record['runtime']=metadata;save(output,record)
  for c in cases:
   if c['id']in done:continue
   start=time.perf_counter();result={k:c[k]for k in ('id','source_pdf_sha256','page','bbox','dpi','image_sha256','kind')}
   try:
    reading=infer(c);tokens=reading.get('generated_tokens');text=reading['text'];text.encode('utf-8')
    if identity(c['source_pdf'])!=source_states[Path(c['source_pdf'])]or sha(Path(c['image']).read_bytes())!=c['image_sha256']or sha(render(c))!=c['image_sha256']:raise ValueError('Original source/crop changed during reading')
    complete=type(tokens)is int and 0<=tokens<c['max_tokens']and bool(text.strip())
    result.update(text=text,generated_tokens=tokens,status='processed_candidate'if complete else'incomplete_retry',possible_token_cap=tokens is None or tokens>=c['max_tokens'],review_origin='local_vision_model',source_quality_clearance=False)
   except Exception as error:result.update(status='failed_retry',error=type(error).__name__+': '+str(error))
   result['elapsed_seconds']=time.perf_counter()-start
   record['results']=[r for r in record['results']if r['id']!=c['id']]+[result];save(output,record)
   print(json.dumps({'id':c['id'],'status':result['status'],'seconds':result['elapsed_seconds']},ensure_ascii=False),flush=True)
 return record

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tasks',type=Path);p.add_argument('--cases',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--resume',action='store_true');a=p.parse_args()
 if a.tasks:print(prepare(load(a.tasks),a.output))
 else:run(a.cases,a.output,resume=a.resume)
