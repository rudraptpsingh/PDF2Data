"""Reproducible output-only adapters for the pinned official olmOCR test rules."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from html import escape
import hashlib,json,os,platform,re,subprocess,sys,time

UPSTREAM='f7cfe4c22098b154c76b6ec950d1c0a464eecf8d'
DATASET='54a96a6fb6a2bd3b297e59869491db4d3625b711'
CATEGORIES={'arxiv_math','headers_footers','long_tiny_text','multi_column','old_scans','old_scans_math','table_tests'}
PROFILES={'native':{'ocr':'off','tables':True},'auto':{'ocr':'auto','tables':True},'layout-vlm':{'ocr':'pinned_local_paddleocr_vl_regions','tables':True}}


def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)
def rows(path):return [json.loads(line) for line in jsonl_physical_lines(Path(path).read_text()) if line.strip(' \t\r')]
def safe(root,name):
    path=(root/name).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('benchmark path escaped root')
    return path


def prepare(dataset_root,output,per_category=None,seed='pdf2data-v1',custom=False):
    """Freeze inputs by seeded filename hash, never by outcomes or label values."""
    dataset_root=dataset_root.resolve()
    if per_category is not None and per_category<1:raise ValueError('per-category must be positive')
    if output.exists():raise ValueError('benchmark suite output exists')
    if not custom and {p.stem for p in dataset_root.glob('*.jsonl')}!=CATEGORIES:raise ValueError('pinned public dataset categories are incomplete')
    pdfs=dataset_root/'pdfs';annotations=[];groups={};ids=set()
    def download_proof(path):
        metadata=dataset_root.parent/'.cache/huggingface/download'/path.relative_to(dataset_root.parent)
        metadata=Path(str(metadata)+'.metadata')
        if not metadata.is_file():raise ValueError('missing pinned Hugging Face download receipt: '+path.name)
        lines=jsonl_physical_lines(metadata.read_text())
        if len(lines)<2 or lines[0]!=DATASET:raise ValueError('dataset download revision mismatch')
        content=path.read_bytes();etag=lines[1]
        actual=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest() if len(etag)==40 else sha(content)
        if etag!=actual:raise ValueError('dataset file differs from pinned download receipt')
    for path in sorted(dataset_root.glob('*.jsonl')):
        if not custom:download_proof(path)
        category=path.stem
        for rule in rows(path):
            if rule['id'] in ids:raise ValueError('duplicate upstream rule ID')
            ids.add(rule['id']);source=safe(pdfs,rule['pdf'])
            # Full annotation inventory is required, but a frozen sample needs only its selected PDFs.
            if type(rule['page']) is not int or rule['page']<1:raise ValueError('invalid upstream page')
            groups.setdefault(category,set()).add(rule['pdf']);annotations.append((category,rule))
    if not annotations:raise ValueError('no benchmark JSONL annotations')
    source_pool=set().union(*groups.values())
    if not custom and len(source_pool)!=1403:raise ValueError('pinned public dataset PDF inventory is incomplete')
    selected=set()
    for category,names in groups.items():
        ranked=sorted(names,key=lambda name:sha((seed+'\0'+name).encode()))
        selected.update(ranked if per_category is None else ranked[:per_category])
    from pypdf import PdfReader
    cases=[]
    for name in sorted(selected):
        source=safe(pdfs,name)
        if not custom:download_proof(source)
        count=len(PdfReader(source).pages)
        cases.append({'id':sha(name.encode())[:20],'pdf':name,'path':str(source),'sha256':sha(source.read_bytes()),'pages':count,
                      'categories':sorted(c for c,n in groups.items() if name in n)})
    rules=[{'category':category,'rule':rule} for category,rule in annotations if rule['pdf'] in selected]
    for entry in rules:
        case=next(c for c in cases if c['pdf']==entry['rule']['pdf'])
        if entry['rule']['page']>case['pages']:raise ValueError('upstream annotation outside PDF page count')
    manifest={'schema_version':'pdf2data.benchmark-suite.v1','dataset':'custom_local' if custom else 'allenai/olmOCR-bench','dataset_revision':None if custom else DATASET,
              'complete_public_dataset':not custom and selected==source_pool,'source_pool_documents':len(source_pool),
              'upstream_revision':UPSTREAM,'selection':{'per_category':per_category,'seed':seed,'strategy':'seeded_filename_hash_no_label_selection'},
              'source_root':str(dataset_root.resolve()),'cases':cases,'rules':rules,'category_rule_counts':dict(Counter(r['category'] for r in rules)),
              'annotation_sha256':{p.name:sha(p.read_bytes()) for p in sorted(dataset_root.glob('*.jsonl'))},
              'dataset_license':'ODC-BY; original document rights remain with source owners','publication_eligible':False}
    manifest['suite_sha256']=sha(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode())
    output.mkdir(parents=True);write(output/'suite.json',manifest)
    return {'output':str(output/'suite.json'),'documents':len(cases),'pages':sum(c['pages'] for c in cases),'rules':len(rules),'categories':manifest['category_rule_counts'],'suite_sha256':manifest['suite_sha256']}


def validate_suite(suite):
    raw={k:v for k,v in suite.items() if k!='suite_sha256'}
    if suite.get('schema_version')!='pdf2data.benchmark-suite.v1' or sha(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode())!=suite.get('suite_sha256'):raise ValueError('benchmark suite hash mismatch')
    if len({c['id'] for c in suite['cases']})!=len(suite['cases']) or len({c['pdf'] for c in suite['cases']})!=len(suite['cases']):raise ValueError('duplicate benchmark source')
    if (suite.get('dataset')!='custom_local' and suite.get('dataset_revision')!=DATASET) or suite.get('upstream_revision')!=UPSTREAM:raise ValueError('unsupported benchmark revision')
    for case in suite['cases']:
        if sha(Path(case['path']).read_bytes())!=case['sha256']:raise ValueError('benchmark source changed')


def page_body(page,tables,mode='tables'):
    """No titles, links, hashes, extracted facts or other metadata in predictions.

    Candidate tables are explicitly additive, matching the evidence package's
    selected-text-plus-table views. They are not claimed as placed inline. The
    text-only ablation is exported from the exact same extraction artifacts.
    """
    text=page['text']
    if mode=='text':return text
    from .document_blocks import table_html
    return text+'\n\n'+'\n\n'.join(table_html(t) for t in tables if t['page']==page['page'])


def extract_case(case,profile,output,launcher,python):
    output=output.resolve()
    dest=output/'packages'/case['id'];receipt=dest/'benchmark-case.json'
    if receipt.exists():raise ValueError('case output exists without explicit resume')
    dest.mkdir(parents=True,exist_ok=True)
    if sha(Path(case['path']).read_bytes())!=case['sha256']:raise ValueError('benchmark PDF changed')
    command=[str(launcher),'pdf-extract','--input',case['path'],'--output',str(dest),'--ocr',PROFILES[profile]['ocr'],'--tables','--json']
    start=time.perf_counter();phase='extraction'
    try:
        done=subprocess.run(command,env={**os.environ,'PDF2DATA_PYTHON':str(python),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'},capture_output=True,text=True,timeout=180)
        duration=time.perf_counter()-start
        (dest/'stdout.log').write_text(done.stdout);(dest/'stderr.log').write_text(done.stderr)
        if done.returncode:raise ValueError('extractor nonzero exit '+str(done.returncode))
        phase='artifact_validation'
        pages=rows(dest/'pages.jsonl');tables=load(dest/'tables.json');report=load(dest/'report.json')
        if len(pages)!=case['pages'] or {p['page'] for p in pages}!=set(range(1,case['pages']+1)) or report['source_pdf_sha256']!=case['sha256']:raise ValueError('page or source coverage mismatch')
        phase='adapter';artifacts=[]
        for page in pages:
            if page['text_sha256']!=sha(page['text'].encode()):raise ValueError('page text hash mismatch')
            for mode in ('text','tables'):
                name=str(Path(case['pdf']).with_suffix(''))+f'_pg{page["page"]}_repeat1.md'
                path=safe(output/mode,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(page_body(page,tables,mode))
                artifacts.append({'mode':mode,'path':str(path.relative_to(output)),'sha256':sha(path.read_bytes()),'page':page['page']})
        result={'id':case['id'],'pdf':case['pdf'],'source_sha256':case['sha256'],'status':'completed','seconds':duration,
                'command':command,'artifacts':artifacts,'methods':dict(Counter(p['method'] for p in pages)),
                'empty_nonblank_pages':[p['page'] for p in pages if not p['text'].strip() and p['status']!='blank'],
                'review_required_pages':[p['page'] for p in pages if p['status']=='review_required'],
                'table_candidates':len(tables),'table_output_mode':'additive_candidates_unresolved_placement',
                'ocr_compute':report['ocr_compute']}
    except Exception as error:
        result={'id':case['id'],'pdf':case['pdf'],'source_sha256':case['sha256'],'status':'error','seconds':time.perf_counter()-start,'command':command,'failure_stage':phase,'error':str(error)}
    write(receipt,result);return result


def run(suite_path,profile,output,python,workers=4,resume=False,layout_python=None,reuse_readings=None,text_fallback='none',reuse_layout=None):
    output=output.resolve()
    invocation_start=time.perf_counter()
    suite=load(suite_path);validate_suite(suite)
    if profile not in PROFILES or not 1<=workers<=8:raise ValueError('invalid benchmark profile/workers')
    if profile=='layout-vlm':
        if workers!=1 or layout_python is None:raise ValueError('layout-vlm requires --workers 1 and --layout-python')
        return structured_run(suite,output,python,layout_python,resume,reuse_readings,text_fallback,reuse_layout)
    if text_fallback!='none' or reuse_layout is not None:raise ValueError('layout reuse and text fallback require layout-vlm')
    if output.exists() and not resume:raise ValueError('benchmark run output exists; use resume')
    output.mkdir(parents=True,exist_ok=True)
    config={'suite_sha256':suite['suite_sha256'],'profile':profile,'profile_config':PROFILES[profile],
            'implementation_sha256':implementation_hashes(),'python':str(python.absolute()),'python_binary':str(python.resolve()),'workers':workers,'runtime':runtime(python)}
    if (output/'config.json').exists() and load(output/'config.json')!=config:raise ValueError('benchmark implementation or inputs changed; start a fresh run')
    write(output/'config.json',config);launcher=Path(__file__).parents[2]/'pdf2data';completed=[];pending=[]
    for case in suite['cases']:
        receipt=output/'packages'/case['id']/'benchmark-case.json'
        if resume and receipt.exists():
            saved=load(receipt)
            if saved['source_sha256']!=case['sha256']:raise ValueError('resume source mismatch')
            for a in saved.get('artifacts',[]):
                if sha(safe(output,a['path']).read_bytes())!=a['sha256']:raise ValueError('resume prediction changed')
            completed.append(saved)
        else:pending.append(case)
    reused_errors=sum(r['status']=='error' for r in completed)
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(extract_case,case,profile,output,launcher,python) for case in pending]
        for future in as_completed(futures):
            completed.append(future.result())
            if len(completed)%10==0:print(json.dumps({'event':'benchmark_progress','profile':profile,'completed':len(completed),'total':len(suite['cases'])}),flush=True)
    result={'schema_version':'pdf2data.benchmark-run.v1','config':config,'cases':sorted(completed,key=lambda r:r['id']),
            'wall_seconds_this_invocation':time.perf_counter()-start,'end_to_end_seconds':time.perf_counter()-invocation_start,'reused_cases':len(suite['cases'])-len(pending),
            'fresh_extraction_run':not resume,'cold_os_cache_controlled':False,'reused_error_cases':reused_errors,'errors':sum(r['status']=='error' for r in completed),'platform':platform.platform(),
            'selected_methods':dict(sum((Counter(r.get('methods',{})) for r in completed),Counter())),
            'table_placement':'additive_candidates_not_proven_inline','publication_eligible':False}
    if config['implementation_sha256']!=implementation_hashes():raise ValueError('parser code changed during benchmark')
    write(output/'run.json',result);return {k:v for k,v in result.items() if k!='cases'}


def structured_run(suite,output,python,layout_python,resume=False,reuse_readings=None,text_fallback='none',reuse_layout=None):
    """The same external rules; only source IDs, paths and hashes reach readers."""
    if output.exists() and not resume:raise ValueError('benchmark output exists; use resume')
    output.mkdir(parents=True,exist_ok=True)
    config={'suite_sha256':suite['suite_sha256'],'profile':'layout-vlm','profile_config':PROFILES['layout-vlm'],
            'implementation_sha256':implementation_hashes(),'python':str(python.absolute()),'workers':1,'runtime':runtime(python),
            'layout_python':str(layout_python.absolute()),'layout_runtime':runtime(layout_python),'reuse_readings':str(reuse_readings.resolve()) if reuse_readings else None}
    config['text_fallback']=text_fallback
    config['reuse_layout']={'path':str(reuse_layout.resolve()),'sha256':sha(reuse_layout.read_bytes())} if reuse_layout else None
    if (output/'config.json').exists() and load(output/'config.json')!=config:raise ValueError('structured benchmark resume contract changed')
    write(output/'config.json',config)
    if resume and (output/'run.json').exists():
        record=load(output/'run.json')
        for case in record['cases']:
            for artifact in case.get('artifacts',[]):
                if sha(safe(output,artifact['path']).read_bytes())!=artifact['sha256']:raise ValueError('saved prediction changed')
        return {'output':str(output/'run.json'),'reused':True,'errors':record['errors']}
    source_rows=[{'id':c['id'],'path':c['path'],'sha256':c['sha256'],'pages':c['pages']} for c in suite['cases']]
    write(output/'sources.json',{'schema_version':'pdf2data.source-batch.v1','sources':source_rows})
    if reuse_layout is not None:
        target=output/'layout/layout.json'
        if target.exists() and target.read_bytes()!=reuse_layout.read_bytes():raise ValueError('saved reused layout differs')
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(reuse_layout.read_bytes())
    launcher=Path(__file__).parents[2]/'pdf2data';commands=[];start=time.perf_counter()
    def invoke(command,environment,log):
        commands.append(command)
        with (output/(log+'.stdout.log')).open('w') as stdout,(output/(log+'.stderr.log')).open('w') as stderr:
            done=subprocess.run(command,env={**os.environ,'PDF2DATA_PYTHON':str(environment),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'},stdout=stdout,stderr=stderr,timeout=7200)
        write(output/'commands.json',commands)
        if done.returncode:raise ValueError('structured CLI stage failed: '+log+'; see logs')
    reused_layout=(output/'layout/layout.json').exists()
    if not reused_layout:
        invoke([str(launcher),'pdf-layout-detect','--manifest',str(output/'sources.json'),'--output',str(output/'layout'),'--device','mps','--json'],layout_python,'layout')
    layout=load(output/'layout/layout.json')
    if len(layout['documents'])!=len(suite['cases']) or layout['source_manifest_sha256']!=sha((output/'sources.json').read_bytes()) or {d['id'] for d in layout['documents']}!={c['id'] for c in suite['cases']}:raise ValueError('incomplete or mismatched layout source batch')
    command=[str(launcher),'pdf-region-read','--layout',str(output/'layout/layout.json'),'--output',str(output/'read'),'--device','gpu','--max-tokens','4096','--json']
    if reuse_readings is not None:command+=['--reuse-readings',str(reuse_readings.resolve())]
    if text_fallback!='none':command+=['--text-fallback',text_fallback]
    if (output/'read').exists():command+=['--resume']
    invoke(command,python,'read')
    reading=load(output/'read/read.json');completed=[];docs={d['id']:d for d in reading['documents']}
    for case in suite['cases']:
        dest=output/'read'/case['id'];page_rows=rows(dest/'pages.jsonl');tables=load(dest/'tables.json')
        item={'id':case['id'],'pdf':case['pdf'],'source_sha256':case['sha256'],'status':'completed','artifacts':[],
              'review_required_pages':[p['page'] for p in page_rows],'empty_nonblank_pages':[p['page'] for p in page_rows if not p['text'].strip()],
              'failed_regions':docs[case['id']]['failed_regions'],'visual_review_regions':sum(len(p['visual_review_regions']) for p in page_rows),
              'methods':dict(Counter(p.get('method','layout_paddleocr_vl') for p in page_rows)),'table_candidates':len(tables)}
        if len(page_rows)!=case['pages'] or {p['page'] for p in page_rows}!=set(range(1,case['pages']+1)):raise ValueError('structured page coverage mismatch')
        if item['failed_regions']:
            item.update(status='error',failure_stage='extraction',error='incomplete region readings; retained for review')
        for page in page_rows:
            if page['text_sha256']!=sha(page['text'].encode()):raise ValueError('structured page text changed')
            for mode in ('text','tables'):
                body=page['text'] if mode=='text' else '\n\n'.join(b['markdown'] for b in page['blocks'] if b['scope']=='body')
                name=str(Path(case['pdf']).with_suffix(''))+f'_pg{page["page"]}_repeat1.md'
                path=safe(output/mode,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
                item['artifacts'].append({'mode':mode,'path':str(path.relative_to(output)),'sha256':sha(path.read_bytes()),'page':page['page']})
        completed.append(item)
    if config['implementation_sha256']!=implementation_hashes():raise ValueError('implementation changed during structured benchmark')
    record={'schema_version':'pdf2data.benchmark-run.v1','config':config,'cases':completed,'errors':sum(c['status']=='error' for c in completed),
            'wall_seconds_this_invocation':time.perf_counter()-start,'end_to_end_seconds':time.perf_counter()-start,
            'fresh_extraction_run':not reused_layout and reading['reused_regions']==0,'reused_cases':0,'reused_error_cases':0,
            'reused_layout':reused_layout,'reused_regions':reading['reused_regions'],'cold_os_cache_controlled':False,
            'selected_methods':dict(sum((Counter(c['methods']) for c in completed),Counter())),'model':reading['model'],'model_revision':reading['revision'],
            'reader_device':reading['device_used'],'model_files_sha256':reading['model_files_sha256'],'reader_runtime':reading['runtime'],
            'detector_identity':layout.get('detector_identity'),'table_placement':'source_detector_geometry_order_candidate','publication_eligible':False}
    record['text_fallback']=reading.get('text_fallback')
    write(output/'run.json',record);return {k:v for k,v in record.items() if k not in {'cases','config','model_files_sha256'}}


def reconcile_tables_run(suite_path,parent_path,native_path,output):
    suite=load(suite_path);validate_suite(suite);parent=load(parent_path/'run.json');native=load(native_path/'run.json');output=output.resolve()
    if output.exists():raise ValueError('reconciled benchmark output exists')
    if any(r['config']['suite_sha256']!=suite['suite_sha256'] for r in [parent,native]):raise ValueError('reconciliation source suites differ')
    if {c['id'] for c in parent['cases']}!={c['id'] for c in suite['cases']}:raise ValueError('parent source inventory differs')
    for case in parent['cases']:
        pages=rows(parent_path/'read'/case['id']/'pages.jsonl');by_page={p['page']:p for p in pages}
        for artifact in case.get('artifacts',[]):
            file=safe(parent_path,artifact['path'])
            if sha(file.read_bytes())!=artifact['sha256']:raise ValueError('parent prediction changed')
            p=by_page[artifact['page']];body=p['text'] if artifact['mode']=='text' else '\n\n'.join(b['markdown'] for b in p['blocks'] if b['scope']=='body')
            if body!=file.read_text():raise ValueError('parent candidate data differs from frozen predictions')
    output.mkdir(parents=True);launcher=Path(__file__).parents[2]/'pdf2data';start=time.perf_counter()
    command=[str(launcher),'pdf-table-reconcile','--layout',str(parent_path/'layout/layout.json'),'--read',str(parent_path/'read'),'--native-packages',str(native_path/'packages'),'--output',str(output/'reconciled'),'--json']
    done=subprocess.run(command,env={**os.environ,'PDF2DATA_PYTHON':sys.executable},capture_output=True,text=True,timeout=600)
    (output/'stdout.log').write_text(done.stdout);(output/'stderr.log').write_text(done.stderr);write(output/'command.json',{'command':command,'returncode':done.returncode})
    if done.returncode:raise ValueError('table reconciliation failed; see logs')
    reconciliation=load(output/'reconciled/reconciliation.json');completed=[];documents={d['id']:d for d in reconciliation['documents']}
    for original in parent['cases']:
        pages=rows(output/'reconciled'/original['id']/'pages.jsonl');item={**original,'artifacts':[],'failed_regions':documents[original['id']]['failed_regions']}
        item['table_candidates']=len(load(output/'reconciled'/original['id']/'tables.json'));item['methods']=dict(Counter(p['method'] for p in pages))
        item['status']='error' if item['failed_regions'] else 'completed';item['empty_nonblank_pages']=[p['page'] for p in pages if not p['text'].strip()]
        if not item['failed_regions']:
            for key in ['error','failure_stage']:item.pop(key,None)
        for page in pages:
            for mode in ['text','tables']:
                body=page['text'] if mode=='text' else '\n\n'.join(b['markdown'] for b in page['blocks'] if b['scope']=='body')
                path=safe(output/mode,str(Path(item['pdf']).with_suffix(''))+f'_pg{page["page"]}_repeat1.md');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
                item['artifacts'].append({'mode':mode,'path':str(path.relative_to(output)),'sha256':sha(path.read_bytes()),'page':page['page']})
        completed.append(item)
    record={**parent,'config':{**parent['config'],'profile':'layout-vlm-source-tables','parent_implementation_sha256':parent['config']['implementation_sha256'],'implementation_sha256':implementation_hashes()},
            'cases':completed,'errors':sum(c['status']=='error' for c in completed),'selected_methods':dict(sum((Counter(c['methods']) for c in completed),Counter())),'fresh_extraction_run':False,'reused_cases':len(completed),
            'projection':{'kind':'source_geometry_table_reconciliation','parent_run_sha256':sha((parent_path/'run.json').read_bytes()),'native_run_sha256':sha((native_path/'run.json').read_bytes()),
                          'reconciliation_seconds':time.perf_counter()-start,'runtime':runtime(Path(sys.executable)),'model_inference_rerun':False,'native_tables_selected':sum(d['native_tables_selected'] for d in reconciliation['documents'])},
            'timing_scope':'inherited parent processing wall; reconciliation time reported separately; no fresh throughput claim'}
    write(output/'run.json',record);return {'output':str(output/'run.json'),'documents':len(completed),'errors':record['errors'],'native_tables_selected':record['projection']['native_tables_selected'],'model_inference_rerun':False}


def structure_projection_run(suite_path,parent_path,structured_root,output):
    """Derive new source-structure predictions; preserve all parent OCR failures."""
    from .document_blocks import render_blocks,validate_blocks
    output=output.resolve()
    suite=load(suite_path);validate_suite(suite);parent=load(parent_path/'run.json')
    if output.exists():raise ValueError('structured benchmark output exists')
    if parent['config']['suite_sha256']!=suite['suite_sha256']:raise ValueError('structure benchmark suite differs')
    if {c['id'] for c in parent['cases']}!={c['id'] for c in suite['cases']}:raise ValueError('parent source inventory differs')
    for case in parent['cases']:
        for artifact in case.get('artifacts',[]):
            if sha(safe(parent_path,artifact['path']).read_bytes())!=artifact['sha256']:raise ValueError('parent benchmark prediction changed')
    output.mkdir(parents=True);start=time.perf_counter();completed=[];bindings=[]
    for case in parent['cases']:
        dest=structured_root/case['id'];manifest=load(dest/'manifest.json')
        if manifest['source_pdf_sha256']!=case['source_sha256']:raise ValueError('structure source differs')
        for name,expected in manifest['files'].items():
            if sha(safe(dest,name).read_bytes())!=expected:raise ValueError('structured export changed')
        data=load(dest/'data.json');validate_blocks(data['document_blocks'],data['pages'])
        item={**case,'artifacts':[]}
        for page in data['pages']:
            selected=[b for b in data['document_blocks']['blocks'] if b['spans'][0]['page']==page['page']]
            display={**data,'document_blocks':{**data['document_blocks'],'blocks':selected}}
            for mode in ('text','tables'):
                body=render_blocks(display)[0] if mode=='tables' else '\n\n'.join(''.join(a['exact_quote'] for a in b['spans']) for b in selected if b['kind'] not in ('footer','header','whitespace','alternative_reading'))
                name=str(Path(case['pdf']).with_suffix(''))+f'_pg{page["page"]}_repeat1.md'
                path=safe(output/mode,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
                item['artifacts'].append(dict(mode=mode,path=str(path.relative_to(output)),sha256=sha(path.read_bytes()),page=page['page']))
        completed.append(item);bindings.append(dict(id=case['id'],manifest_sha256=sha((dest/'manifest.json').read_bytes()),source_pdf_sha256=manifest['source_pdf_sha256']))
    record={**parent,'config':{**parent['config'],'profile':'layout-vlm-document-structure','implementation_sha256':implementation_hashes()},
            'cases':completed,'errors':sum(c['status']=='error' for c in completed),'fresh_extraction_run':False,'reused_cases':len(completed),
            'projection':dict(kind='shared_document_structure_export',parent_run_sha256=sha((parent_path/'run.json').read_bytes()),
                              structured_manifests=bindings,model_inference_rerun=False,projection_seconds=time.perf_counter()-start),
            'timing_scope':'inherited OCR timing; structure projection reported separately; not a fresh recognition run'}
    write(output/'run.json',record)
    return dict(output=str(output/'run.json'),documents=len(completed),errors=record['errors'],model_inference_rerun=False)


def score(suite_path,run_path,upstream,eval_python,output,mode='tables'):
    suite=load(suite_path);validate_suite(suite)
    if output.exists():raise ValueError('score output exists')
    run_record=load(run_path/'run.json')
    if run_record['config']['suite_sha256']!=suite['suite_sha256']:raise ValueError('score suite/run mismatch')
    actual=subprocess.run(['git','rev-parse','HEAD'],cwd=upstream,capture_output=True,text=True,check=True).stdout.strip()
    if actual!=UPSTREAM:raise ValueError('un-pinned official evaluator checkout')
    if subprocess.run(['git','status','--porcelain'],cwd=upstream,capture_output=True,text=True,check=True).stdout.strip():raise ValueError('upstream evaluator checkout modified')
    if any(c.get('failure_stage')=='adapter' for c in run_record['cases']):raise ValueError('adapter failure is not an OCR score; repair the benchmark run first')
    for case in run_record['cases']:
        for a in case.get('artifacts',[]):
            if sha(safe(run_path,a['path']).read_bytes())!=a['sha256']:raise ValueError('prediction changed after extraction')
    output.mkdir(parents=True)
    request={'suite':str(suite_path.resolve()),'run':str(run_path.resolve()),'upstream':str(upstream.resolve()),'mode':mode,'output':str(output.resolve())}
    write(output/'request.json',request)
    command=[str(eval_python),str(Path(__file__).with_name('benchmark_score.py')),str(output/'request.json')]
    done=subprocess.run(command,capture_output=True,text=True,timeout=3600)
    (output/'stdout.log').write_text(done.stdout);(output/'stderr.log').write_text(done.stderr)
    write(output/'command.json',{'command':command,'returncode':done.returncode})
    if done.returncode:raise ValueError('official rule evaluation failed; see score stderr log')
    return load(output/'report.json')



def implementation_hashes():
    return {p.name:sha(p.read_bytes()) for p in Path(__file__).parent.iterdir() if p.is_file() and p.suffix in {'.py','.json','.swift'}}


def runtime(python):
    script="""import sys,json,platform,hashlib,importlib.metadata,importlib.util
from pathlib import Path
versions={}
for name in ('pdfplumber','pypdf','pypdfium2','fonttools','opencv-python-headless','rapidocr','onnxruntime','filelock','docling','docling-core','torch','transformers','mlx','mlx-vlm'):
 try:versions[name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:versions[name]=None
weights={}
spec=importlib.util.find_spec('rapidocr')
if spec and spec.origin:
 for path in Path(spec.origin).parent.rglob('*.onnx'):weights[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({'python_version':sys.version,'platform':platform.platform(),'dependencies':versions,'ocr_weights':weights,'vision_model_version':'OS-managed; artifact identity not exposed'}))
"""
    done=subprocess.run([str(python),'-c',script],capture_output=True,text=True,check=True)
    return json.loads(done.stdout)


def compare(score_paths,output):
    if output.exists():raise ValueError('comparison output exists')
    reports=[load(p/'report.json') for p in score_paths]
    if len(reports)<2 or len({r['suite_sha256'] for r in reports})!=1:raise ValueError('comparison needs matching frozen suites')
    if any(not r['valid_evaluation'] for r in reports):raise ValueError('cannot compare invalid evaluator runs')
    evidence=[]
    for path,report in zip(score_paths,reports):
        request=load(path/'request.json');run_path=Path(request['run'])/'run.json';run_record=load(run_path)
        evidence.append({'label':path.name,'score_path':str(path.resolve()),'report_sha256':sha((path/'report.json').read_bytes()),
                         'run_path':str(run_path),'run_sha256':sha(run_path.read_bytes()),'parser_implementation_sha256':run_record['config']['implementation_sha256'],
                         'extraction_errors':run_record['errors'],'fresh_extraction_run':run_record.get('fresh_extraction_run'),
                         'projection':run_record.get('projection')})
    output.mkdir(parents=True)
    comparison={'schema_version':'pdf2data.benchmark-comparison.v1','suite_sha256':reports[0]['suite_sha256'],
                'evidence':evidence,'profiles':[{k:r[k] for k in ('profile','mode','public_documents','public_rules','categories','raw_category_macro','coverage_adjusted_category_macro','errors','evaluation_seconds','extraction_wall_seconds')} for r in reports],
                'score_scope':'pinned upstream rule classes plus missing-page baselines; no leaderboard submission',
                'ground_truth':'public benchmark labels, not generated from PDF2Data output','publication_eligible':False}
    write(output/'comparison.json',comparison)
    lines=['# PDF2Data benchmark comparison','','Scores use the same frozen source PDFs and external rule labels. Raw and coverage-adjusted scores are separate. Extraction and evaluator times are separate. OS caches were not cleared.','',
           '| Label | Profile | Page body | Raw macro | Coverage adjusted | Extraction wall seconds | Evaluation seconds |',
           '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for path,r in zip(score_paths,reports):lines.append(f"| {path.name} | {r['profile']} | {r['mode']} | {r['raw_category_macro']:.2%} | {r['coverage_adjusted_category_macro']:.2%} | {r['extraction_wall_seconds']:.1f} | {r['evaluation_seconds']:.1f} |")
    lines+=['','## Per category','','| Category | '+' | '.join(p.name for p in score_paths)+' |','| --- | '+' | '.join('---:' for r in reports)+' |']
    for category in reports[0]['categories']:
        lines.append('| '+category+' | '+' | '.join(f"{r['categories'][category]['adjusted_rate']:.2%}" for r in reports)+' |')
    lines+=['','Tables are additive extracted candidates; inline placement and duplicate suppression are not established by this score. The text ablation reuses the same extraction and must not be timed as a fresh parser run.',
            '','The baseline category is separate. A public full-suite claim requires complete_public_dataset=true in the frozen suite. Custom probes are not an independent legal accuracy benchmark.']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n')
    bars=[]
    for path,r in zip(score_paths,reports):
        bars.append('<p>'+escape(path.name+' / '+r['profile']+' / '+r['mode'])+f" <strong>{r['coverage_adjusted_category_macro']:.1%}</strong></p><div style=\"height:16px;background:#dfe9e3;border-radius:8px\"><div style=\"height:100%;width:{100*r['coverage_adjusted_category_macro']:.2f}%;background:#34735b;border-radius:8px\"></div></div>")
    (output/'comparison.html').write_text('<!doctype html><meta charset="utf-8"><title>PDF2Data benchmark</title><main style="max-width:800px;margin:50px auto;font:16px/1.5 system-ui;padding:20px"><h1>PDF2Data benchmark comparison</h1><p>Coverage-adjusted category macro scores on identical frozen inputs.</p>'+''.join(bars)+'<p>See comparison.json and comparison.md for category scores, errors, scope and timing limits.</p></main>')
    return {'output':str(output),'profiles':len(reports),'documents':reports[0]['public_documents'],'rules':reports[0]['public_rules']}
