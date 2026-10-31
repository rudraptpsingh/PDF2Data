"""Optional source-only Ollama OCR experiment using the existing benchmark schema.

No model pull, cloud endpoint, expected text or legal approval is performed.
Raw model replies and rendered original pages remain separate from predictions.
"""
import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from .hashing import file_sha256

PROMPT = '\n<|grounding|>Convert the document to markdown.'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('OCR endpoint redirects are not allowed')


class Reader:
    def __init__(self, host, model, timeout=240):
        parsed=urlsplit(host)
        if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1')
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('','/')):
            raise ValueError('Ollama OCR requires an explicit loopback HTTP endpoint')
        self.host=host.rstrip('/');self.model=model;self.timeout=timeout
        self.opener=build_opener(ProxyHandler({}),NoRedirect())
        version=self.request('/api/version')['version']
        self.identity={'model':model,'digest':self.model_digest(),'ollama_version':version,'endpoint':self.host}

    def request(self, path, body=None):
        raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if body is not None else None
        request=Request(self.host+path,data=raw,headers={'Content-Type':'application/json'})
        with self.opener.open(request,timeout=self.timeout) as response:
            value=response.read(16*1024*1024+1)
        if len(value)>16*1024*1024:
            raise ValueError('OCR response exceeds the bounded read limit; not accepted as complete')
        return json.loads(value)

    def model_digest(self):
        matches=[m['digest'] for m in self.request('/api/tags').get('models',[])
                 if m.get('name')==self.model or m.get('model')==self.model]
        if len(set(matches))!=1:
            raise ValueError('Install the exact OCR model tag explicitly before running this command')
        return matches[0]

    def read(self, image, max_tokens):
        body={'model':self.model,'messages':[{'role':'user','content':PROMPT,
               'images':[base64.b64encode(image.read_bytes()).decode()]}],
              'stream':False,'options':{'temperature':0,'seed':0,'num_ctx':8192,'num_predict':max_tokens},
              'keep_alive':'10m'}
        result=self.request('/api/chat',body)
        try:observed=self.model_digest()
        except Exception:observed=None
        try:placement=self.request('/api/ps')
        except Exception as error:placement={'placement_unavailable':str(error)[:200]}
        placement['model_digest_after']=observed
        return result,sha256(json.dumps(body,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),placement


def prediction(reply):
    if reply.get('done') is not True:
        raise ValueError('OCR response is incomplete')
    if reply.get('done_reason') not in ('stop',):
        raise ValueError('OCR generation did not finish normally: '+str(reply.get('done_reason')))
    text=reply.get('message',{}).get('content')
    if not isinstance(text,str) or not text.strip():
        raise ValueError('OCR returned empty content')
    # Remove only well-formed, line-level model grounding metadata. The raw
    # response retains every token. Unknown/unclosed markers are not repaired.
    pattern=r'(?m)^<\|ref\|>[^\n]*?<\|/ref\|><\|det\|>\[\[[0-9., \[\]]+\]\]<\|/det\|>\s*\n?'
    text=re.sub(pattern,'',text)
    text=re.sub(r'(?m)^<\|im_(?:start|end)\|>(?:assistant|user)?[ \t]*(?:\n|$)','',text)
    if not text.strip():raise ValueError('OCR returned only model control/grounding metadata; no parsed content')
    return text


def run(suite_path, output, host='http://127.0.0.1:11434', model='deepseek-ocr:3b',
        max_tokens=8192, dpi=180, timeout=240, resume=False):
    from .benchmark import load,write,validate_suite,implementation_hashes
    import pypdfium2 as pdfium
    suite=load(suite_path);validate_suite(suite);output=Path(output).resolve()
    if not 128<=max_tokens<=8192 or not 72<=dpi<=240 or not 1<=timeout<=600:
        raise ValueError('Unsupported OCR budget, resolution or timeout')
    reader=Reader(host,model,timeout)
    config={'suite_sha256':suite['suite_sha256'],'profile':'deepseek-ocr-ollama',
            'reader':reader.identity,'prompt':PROMPT,'dpi':dpi,'max_tokens':max_tokens,
            'context_length':8192,'timeout_seconds':timeout,'temperature':0,'seed':0,
            'implementation_sha256':implementation_hashes(),'page_mode':'whole_original_page',
            'workers':1,'model_pull_performed':False}
    if output.exists() and not resume:raise ValueError('Benchmark output exists; use a fresh path')
    output.mkdir(parents=True,exist_ok=True)
    if (output/'config.json').exists() and load(output/'config.json')!=config:
        raise ValueError('OCR resume source/runtime/model/configuration changed')
    write(output/'config.json',config);started=time.perf_counter();completed=[];reused=0
    for case in suite['cases']:
        original=Path(case['path'])
        if file_sha256(original)!=case['sha256']:raise ValueError('Original benchmark PDF changed')
        folder=output/'sources'/case['id'];receipt=folder/'case.json'
        if receipt.exists():
            item=load(receipt)
            for artifact in item['artifacts']+item.get('source_artifacts',[]):
                path=(output/artifact['path']).resolve()
                if not path.is_relative_to(output) or file_sha256(path)!=artifact['sha256']:
                    raise ValueError('Saved OCR source/prediction artifact changed')
            completed.append(item);reused+=1;continue
        folder.mkdir(parents=True,exist_ok=True)
        item={'id':case['id'],'pdf':case['pdf'],'source_sha256':case['sha256'],'status':'completed',
              'artifacts':[],'source_artifacts':[],'page_results':[],'empty_nonblank_pages':[],
              'review_required_pages':list(range(1,case['pages']+1)),'methods':{'ollama_deepseek_ocr':case['pages']}}
        doc=pdfium.PdfDocument(str(original))
        try:
            if len(doc)!=case['pages']:raise ValueError('Original physical page count changed')
            for number in range(1,len(doc)+1):
                image=folder/f'page-{number:04d}.png';page=doc[number-1];bitmap=page.render(scale=dpi/72)
                try:
                    rendered=bitmap.to_pil().convert('RGB')
                    try:rendered.save(image)
                    finally:rendered.close()
                finally:bitmap.close();page.close()
                item['source_artifacts'].append({'path':image.relative_to(output).as_posix(),'sha256':file_sha256(image),'page':number})
                began=time.perf_counter();page_result={'page':number,'source_image_sha256':file_sha256(image)}
                try:
                    reply,request_hash,placement=reader.read(image,max_tokens)
                    raw_path=folder/f'page-{number:04d}-response.json';write(raw_path,reply)
                    item['source_artifacts'].append({'path':raw_path.relative_to(output).as_posix(),'sha256':file_sha256(raw_path),'page':number})
                    page_result.update(request_sha256=request_hash,done_reason=reply.get('done_reason'),
                                       model_metrics={k:reply.get(k) for k in ('total_duration','load_duration','prompt_eval_count','prompt_eval_duration','eval_count','eval_duration')},
                                       placement=placement)
                    if placement.get('model_digest_after')!=reader.identity['digest']:
                        raise ValueError('OCR model changed or identity could not be revalidated; raw response retained')
                    text=prediction(reply)
                    path=folder/f'page-{number:04d}.md';path.write_bytes(text.encode('utf-8'))
                    item['artifacts'].append({'page':number,'mode':'tables','path':path.relative_to(output).as_posix(),'sha256':file_sha256(path)})
                    page_result['status']='generated_unverified'
                except Exception as error:
                    page_result.update(status='reader_failed',error=str(error)[:500])
                    item.update(status='error',failure_stage='extraction',error='One or more OCR pages failed; all cases retained')
                    item['empty_nonblank_pages'].append(number)
                page_result['elapsed_seconds']=time.perf_counter()-began;item['page_results'].append(page_result)
        finally:doc.close()
        if file_sha256(original)!=case['sha256']:raise ValueError('Original changed while OCR was running')
        write(receipt,item);completed.append(item)
        print(json.dumps({'event':'ocr_case_completed','id':case['id'],'status':item['status'],'completed':len(completed),'total':len(suite['cases'])}),flush=True)
        write(output/'progress.json',{'completed':len(completed),'total':len(suite['cases']),'cases':completed})
    record={'schema_version':'pdf2data.benchmark-run.v1','config':config,'cases':completed,
            'errors':sum(c['status']!='completed'for c in completed),'reused_cases':reused,
            'fresh_extraction_run':reused==0,
            'wall_seconds_this_invocation':time.perf_counter()-started,'output':str(output),
            'publication_eligible':False,'pipeline_notes':'Whole-page Ollama Markdown; no native-table reconciliation; GPU placement is reported, not a peak-memory measurement'}
    write(output/'run.json',record)
    return {k:v for k,v in record.items()if k not in ('config','cases')}


def subset_run(suite_path,parent_path,output):
    """Project cached predictions to a frozen source/rule subset; no inference."""
    from .benchmark import load,write,validate_suite,safe,implementation_hashes
    import shutil
    suite=load(suite_path);validate_suite(suite);parent_path=Path(parent_path).resolve();output=Path(output).resolve()
    parent_raw=(parent_path/'run.json').read_bytes();parent_sha=sha256(parent_raw).hexdigest()
    parent=json.loads(parent_raw);cases={c['id']:c for c in parent['cases']}
    if output.exists():raise ValueError('Subset output requires a fresh directory')
    if len(cases)!=len(parent['cases']):raise ValueError('Duplicate parent source cases')
    selected=[];output.mkdir(parents=True)
    for source in suite['cases']:
        case=deepcopy(cases.get(source['id']))
        if not case or case['source_sha256']!=source['sha256'] or case['pdf']!=source['pdf']:
            raise ValueError('Subset is not source-identical to the parent run')
        for a in case['artifacts']+case.get('source_artifacts',[]):
            original=safe(parent_path,a['path'])
            if file_sha256(original)!=a['sha256']:raise ValueError('Parent prediction changed')
            target=safe(output,a['path']);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
            if file_sha256(target)!=a['sha256']:raise ValueError('Parent artifact changed while copying')
        selected.append(case)
    record={**parent,'config':{**parent['config'],'suite_sha256':suite['suite_sha256'],
                              'subset_implementation_sha256':implementation_hashes()},'cases':selected,
            'projection':{'kind':'source_identical_frozen_subset','parent_run_sha256':parent_sha,
                          'model_inference_rerun':False},'wall_seconds_this_invocation':0,
            'fresh_extraction_run':False,
            'errors':sum(c['status']!='completed'for c in selected)}
    if file_sha256(parent_path/'run.json')!=parent_sha:raise ValueError('Parent run changed during projection')
    write(output/'run.json',record)
    return {'output':str(output),'documents':len(selected),'model_inference_rerun':False}


def replay(suite_path,parent_path,output):
    """Revalidate frozen source-only raw responses; no model or annotation call."""
    from .benchmark import load,write,validate_suite,safe,implementation_hashes
    import shutil
    suite=load(suite_path);validate_suite(suite);parent_path=Path(parent_path).resolve();output=Path(output).resolve()
    parent_raw=(parent_path/'run.json').read_bytes();parent_sha=sha256(parent_raw).hexdigest();parent=json.loads(parent_raw)
    if parent['config']['suite_sha256']!=suite['suite_sha256']:raise ValueError('Raw replay suite differs')
    if output.exists():raise ValueError('Raw replay output requires a fresh directory')
    sources={c['id']:c for c in suite['cases']}
    if len(parent['cases'])!=len(sources) or {c['id']for c in parent['cases']}!=set(sources):raise ValueError('Raw replay source coverage differs')
    output.mkdir(parents=True);cases=[]
    for previous in parent['cases']:
        source=sources[previous['id']]
        if previous['source_sha256']!=source['sha256'] or previous['pdf']!=source['pdf']:raise ValueError('Raw replay source identity differs')
        case=deepcopy(previous);case['artifacts']=[];case['empty_nonblank_pages']=[];case['status']='completed'
        case.pop('error',None);case.pop('failure_stage',None)
        if [p['page']for p in case['page_results']]!=list(range(1,source['pages']+1)):raise ValueError('Raw replay physical page coverage differs')
        by_page={}
        for artifact in case['source_artifacts']:
            original=safe(parent_path,artifact['path'])
            if file_sha256(original)!=artifact['sha256']:raise ValueError('Saved raw source/response changed')
            target=safe(output,artifact['path']);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
            if file_sha256(target)!=artifact['sha256']:raise ValueError('Raw artifact changed while copying')
            if original.name.endswith('-response.json'):by_page[artifact['page']]=target
        for page in case['page_results']:
            try:
                if page.get('placement',{}).get('model_digest_after')!=parent['config']['reader']['digest']:
                    raise ValueError('Original model identity was not revalidated')
                if page['page'] not in by_page:raise ValueError('Original raw response was not saved')
                text=prediction(load(by_page[page['page']]))
                path=by_page[page['page']].with_name(f'page-{page["page"]:04d}.md');path.write_bytes(text.encode('utf-8'))
                case['artifacts'].append(dict(page=page['page'],mode='tables',path=path.relative_to(output).as_posix(),sha256=file_sha256(path)))
                page['status']='generated_unverified';page.pop('error',None)
            except Exception as error:
                page.update(status='reader_failed',error=str(error)[:500]);case['status']='error';case['empty_nonblank_pages'].append(page['page'])
        if case['status']=='error':case.update(failure_stage='extraction',error='One or more raw OCR pages failed completeness revalidation')
        cases.append(case)
    record={**parent,'config':{**parent['config'],'implementation_sha256':implementation_hashes(),
            'prediction_policy':'reject_control_only_and_truncated_replies_v2'},'cases':cases,
            'errors':sum(c['status']!='completed'for c in cases),'fresh_extraction_run':False,
            'wall_seconds_this_invocation':0,'source_inference_wall_seconds':parent.get('source_inference_wall_seconds',parent['wall_seconds_this_invocation']),
            'output':str(output),'projection':{'kind':'source_bound_raw_response_revalidation',
            'parent_run_sha256':parent_sha,'model_inference_rerun':False,'model_calls':0}}
    if file_sha256(parent_path/'run.json')!=parent_sha:raise ValueError('Parent run changed during raw replay')
    write(output/'run.json',record)
    return dict(output=str(output),documents=len(cases),errors=record['errors'],model_calls=0)
