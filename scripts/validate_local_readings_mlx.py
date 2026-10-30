"""Offline, pinned local-model evaluation. Never rewrites extraction packages."""
from pathlib import Path
import argparse,gc,hashlib,json,os,re,time
from datetime import datetime,timezone
from importlib.metadata import version

parser=argparse.ArgumentParser();parser.add_argument('--model',choices=['paddle','qwen'],required=True);parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
if os.environ.get('HF_HUB_OFFLINE')!='1':raise SystemExit('HF_HUB_OFFLINE=1 is required')
models={'paddle':('mlx-community/PaddleOCR-VL-1.5-4bit','3404726b40ac30b6e4e3d37c504e3f96c9c6c984'),'qwen':('mlx-community/Qwen3-VL-8B-Instruct-4bit','defcdea7cc7a4b0858fea563cbbce171d328e457')}
name,revision=models[args.model];model_path=Path('/Users/rp/.cache/huggingface/hub')/('models--'+name.replace('/','--'))/'snapshots'/revision
if not model_path.is_dir():raise SystemExit('Pinned model is not cached; no download attempted')
from mlx_vlm import load,generate
import mlx.core as mx
start=time.perf_counter();model,processor=load(str(model_path));loaded=time.perf_counter()-start
cases=json.loads(args.cases.read_text())['cases'];results=[]
args.output.parent.mkdir(parents=True,exist_ok=True)
def normalize(value):return ' '.join(value.split()).strip('`').strip()
def notation(value):
    value=re.sub(r'<sub>\s*RE\s*</sub>','RE',value,flags=re.I)
    value=re.sub(r'_\{(?:\\mathrm\{)?RE\}?\}','RE',value)
    return value.replace('_{RE}','RE').replace('_RE','RE').replace('GNAᴿᴱ','GNARE')
def invoke(case,prompt,kind='transcription',max_tokens=None):
    image=Path(case['image']);assert hashlib.sha256(image.read_bytes()).hexdigest()==case['image_sha256']
    template=processor.apply_chat_template([{'role':'user','content':[{'type':'image'},{'type':'text','text':prompt}]}],tokenize=False,add_generation_prompt=True)
    begun=time.perf_counter()
    try:
        result=generate(model,processor,template,image=str(image),max_tokens=max_tokens or case['max_tokens'],temp=0.0,verbose=False)
        text=result.text.strip();tokens=getattr(result,'generation_tokens',None);cap=max_tokens or case['max_tokens']
        record={'id':case['id'],'kind':kind,'prompt':prompt,'image_sha256':case['image_sha256'],'text':text,'elapsed_seconds':round(time.perf_counter()-begun,3),'generated_tokens':tokens,'max_tokens':cap,'possible_token_cap':tokens is not None and tokens>=cap,'source_pdf_sha256':case['source_pdf_sha256'],'page':case['page']}
        if case['kind']=='subscript':record['expected_notation_terms_present']=all(t in notation(text) for t in case['expected_terms'])
        elif case['kind']!='table':record['exact_target_present']=normalize(case['expected']) in normalize(text)
        if kind=='column_value':record['exact_value_match']=normalize(text).strip('"\' .')==normalize(case['expected'])
        results.append(record);print(json.dumps({'model':args.model,'case':case['id'],'kind':kind,'seconds':record['elapsed_seconds'],'tokens':tokens,'cap':record['possible_token_cap']}),flush=True)
    except Exception as error:
        results.append({'id':case['id'],'kind':kind,'error':f'{type(error).__name__}: {error}','elapsed_seconds':round(time.perf_counter()-begun,3)});print('CASE ERROR',case['id'],str(error),flush=True)
    write()
def write():
    record={'executed_at':datetime.now(timezone.utc).isoformat(),'model':name,'revision':revision,'cached_model_path':str(model_path),'offline':True,'model_load_seconds':round(loaded,3),'runtime':{p:version(p) for p in ['mlx','mlx-vlm','transformers']},'results':results,'scope':'bounded local model outputs, not legal correctness or production approval','cloudflare_operations':0}
    args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
for case in cases:
    prompt='OCR:' if args.model=='paddle' else 'Transcribe exactly the visible text. Preserve dates, numbers, negation, subscripts and punctuation. Do not add facts. Return only the transcription.'
    if case['kind']=='table':prompt='Table Recognition:' if args.model=='paddle' else 'Transcribe this source table as an HTML table. Preserve the original rows, columns and every date/value. Do not fill or infer blank cells. Output only the table.'
    invoke(case,prompt)
    if args.model=='qwen' and case['kind']=='table':
        invoke(case,'Read the single data row in this source table. What is printed in the GNA effectiveness date column? Return only that cell value. Do not substitute the COD or another date.',kind='column_value',max_tokens=96)
write();del model,processor;gc.collect();mx.clear_cache()
