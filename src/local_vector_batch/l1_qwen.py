"""PDF2Data source-bound, offline vision repair commands.

Model readings are evidence candidates, never publication approval. Existing L1
packages are immutable; this module writes only a separate versioned workspace.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines
import hashlib,json,os,re,time,unicodedata
from pathlib import Path

MODEL='mlx-community/Qwen3-VL-8B-Instruct-4bit'
REVISION='defcdea7cc7a4b0858fea563cbbce171d328e457'
PROMPT=('Transcribe exactly all visible text on this original PDF page in reading order. '
        'Preserve dates, numbers, negation, operators, subscripts, headings and footnotes. '
        'For tables output HTML with the original rows, columns and blank cells. '
        'Do not summarize, infer, correct or add facts. Return only the transcription.')
SCHEMA='pdf2data.local-qwen.v1'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
def read(path):return json.loads(Path(path).read_text())
def bound(case):
    if digest(case['image'])!=case['image_sha256']:raise ValueError('source image hash mismatch')
    folder=Path(case['source_package'])
    if digest(folder/'original.pdf')!=case['source_pdf_sha256']:raise ValueError('source PDF hash mismatch')
    pages=[json.loads(line) for line in jsonl_physical_lines((folder/'extraction/pages.jsonl').read_text()) if line.strip(' \t\r')]
    page=next(p for p in pages if p['page']==case['page'])
    if page['text_sha256']!=case['page_text_sha256'] or hashlib.sha256(page['text'].encode()).hexdigest()!=page['text_sha256']:
        raise ValueError('selected source page hash mismatch')
    return page

def prepare(root:Path,output:Path):
    import pypdfium2 as pdfium
    from .legal_l1 import verify_document
    root=root.resolve();output=output.resolve()
    if output==root or output.is_relative_to(root):raise ValueError('vision workspace must be outside immutable source packages')
    if output.exists():raise ValueError('use a new vision workspace; output already exists')
    folders=sorted(p for p in root.iterdir() if (p/'l1-package.json').exists())
    if not folders:raise ValueError('no L1 packages found')
    # Verify all sources before writing any model inputs.
    for folder in folders:verify_document(folder)
    output.mkdir(parents=True);cases=[]
    for folder in folders:
        quality=read(folder/'quality.json')
        with pdfium.PdfDocument(folder/'original.pdf') as pdf:
            for row in quality['results']:
                if row['outcome']=='auto_accepted':continue
                n=row['page'];ident=f'{folder.name}-p{n:03}';image=output/(ident+'.png')
                page=pdf[n-1]
                try:page.render(scale=2.5).to_pil().convert('RGB').save(image)
                finally:page.close()
                cases.append({'id':ident,'image':str(image),'image_sha256':digest(image),
                    'source_pdf_sha256':quality['source_pdf_sha256'],'page':n,
                    'page_text_sha256':row['page_text_sha256'],'source_package':str(folder),
                    'failed_components':row['failed_components'],'crop':'whole_original_physical_page','dpi':180})
    save(output/'cases.json',{'schema_version':SCHEMA,'cases':cases,'originals_modified':False})
    return {'command':'l1-qwen-prepare','cases':len(cases),'output':str(output/'cases.json'),'source_packages_verified':len(folders)}

def run(cases_path:Path,output:Path,*,resume=False,max_tokens=4096,model_path:Path|None=None,device="gpu"):
    if os.environ.get('HF_HUB_OFFLINE')!='1' or os.environ.get('TRANSFORMERS_OFFLINE')!='1':
        raise ValueError('set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1; downloads are disabled')
    if not 256<=max_tokens<=16384:raise ValueError('max tokens must be 256..16384')
    data=read(cases_path)
    if data.get('schema_version')!=SCHEMA:raise ValueError('unsupported cases schema')
    cases=data['cases'];ids=[c['id'] for c in cases]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate case identity')
    for case in cases:bound(case)
    output=output.resolve()
    if output==cases_path.resolve() or any(output.is_relative_to(Path(c['source_package']).resolve()) for c in cases):
        raise ValueError('model output cannot overwrite source inputs')
    if model_path is None:model_path=Path.home()/'.cache/huggingface/hub'/('models--'+MODEL.replace('/','--'))/'snapshots'/REVISION
    model_path=model_path.resolve()
    if not model_path.is_dir() or model_path.name!=REVISION:raise ValueError('pinned Qwen snapshot must be cached locally')
    contract={'schema_version':SCHEMA,'model':MODEL,'revision':REVISION,'cases_sha256':digest(cases_path),'prompt':PROMPT,'max_tokens':max_tokens}
    output.parent.mkdir(parents=True,exist_ok=True)
    from filelock import FileLock
    with FileLock(str(output)+'.lock',timeout=0):
        record={**contract,'offline':True,'results':[],'cloudflare_operations':0,'publication_eligible':False}
        if output.exists():
            if not resume:raise ValueError('output exists; pass --resume with the identical source/model contract')
            record=read(output)
            if any(record.get(k)!=v for k,v in contract.items()):raise ValueError('resume contract changed; use a new output')
            if len({r['id'] for r in record['results']})!=len(record['results']):raise ValueError('duplicate saved result')
            if any(r['id'] not in ids for r in record['results']):raise ValueError('saved result outside source cases')
        done={r['id'] for r in record['results'] if not r.get('error') and not r.get('possible_token_cap')}
        if len(done)==len(cases):return {'command':'l1-qwen-run','completed':len(done),'cases':len(cases),'reused':True,'output':str(output)}
        import mlx.core as mx
        if device not in {'gpu','cpu'}:raise ValueError('unsupported MLX device')
        if device=='gpu' and not mx.metal.is_available():raise ValueError('Apple Metal GPU unavailable; no silent CPU fallback')
        if record.get('requested_device',device)!=device:raise ValueError('resume device changed; use a new output')
        mx.set_default_device(mx.gpu if device=='gpu' else mx.cpu)
        record['requested_device']=device
        record['mlx_default_device']=str(mx.default_device())
        record['metal_available']=mx.metal.is_available()
        record['legacy_readings_without_device_metadata']=[r['id'] for r in record['results'] if 'device' not in r]
        from mlx_vlm import load,generate
        from importlib.metadata import version
        start=time.perf_counter();model,processor=load(str(model_path));record['model_load_seconds']=round(time.perf_counter()-start,3)
        record['runtime']={p:version(p) for p in ('mlx','mlx-vlm','transformers')}
        try:
            for case in cases:
                if case['id'] in done:continue
                bound(case)
                template=processor.apply_chat_template([{'role':'user','content':[{'type':'image'},{'type':'text','text':PROMPT}]}],tokenize=False,add_generation_prompt=True)
                start=time.perf_counter()
                result=generate(model,processor,template,image=case['image'],max_tokens=max_tokens,temp=0.0,verbose=False)
                tokens=getattr(result,'generation_tokens',None)
                row={'id':case['id'],'image_sha256':case['image_sha256'],'source_pdf_sha256':case['source_pdf_sha256'],
                    'page_text_sha256':case['page_text_sha256'],'page':case['page'],'text':result.text.strip(),
                    'generated_tokens':tokens,'possible_token_cap':tokens is None or tokens>=max_tokens,
                    'elapsed_seconds':round(time.perf_counter()-start,3),'device':str(mx.default_device()),'peak_device_memory_bytes':mx.get_peak_memory()}
                record['results']=[r for r in record['results'] if r['id']!=case['id']]+[row]
                save(output,record)
                print(json.dumps({'event':'page_read','id':case['id'],'seconds':row['elapsed_seconds'],'possible_token_cap':row['possible_token_cap'],'device':row['device']}),flush=True)
        finally:
            del model,processor
            import gc,mlx.core as mx
            gc.collect();mx.clear_cache()
    return {'command':'l1-qwen-run','completed':len(record['results']),'cases':len(cases),'output':str(output),'publication_eligible':False}

def compare(selected:str,candidate:str):
    from .quality_gate import WORD,NUMBER,_normalized,_operators,load_policy
    a,b=_normalized(selected),_normalized(candidate)
    # No fuzzy matching authorizes a source correction.
    return {'literal_nonspace_equal':re.sub(r'\s+','',unicodedata.normalize('NFC',selected))==re.sub(r'\s+','',unicodedata.normalize('NFC',candidate)),
        'word_order_equal':WORD.findall(a)==WORD.findall(b),
        'number_order_equal':NUMBER.findall(a)==NUMBER.findall(b),
        'operator_order_equal':_operators(a,load_policy())==_operators(b,load_policy())}

def validate(cases_path:Path,readings_path:Path,output:Path):
    cases=read(cases_path);readings=read(readings_path)
    if cases.get('schema_version')!=SCHEMA or readings.get('schema_version')!=SCHEMA:raise ValueError('unsupported vision schema')
    if readings.get('cases_sha256')!=digest(cases_path) or readings.get('model')!=MODEL or readings.get('revision')!=REVISION or readings.get('prompt')!=PROMPT:
        raise ValueError('reading contract mismatch')
    rows=readings['results'];ids=[r['id'] for r in rows];expected={c['id'] for c in cases['cases']}
    if len(ids)!=len(set(ids)) or not set(ids)<=expected:raise ValueError('unexpected or duplicate reading identity')
    if output.resolve() in {cases_path.resolve(),readings_path.resolve()}:raise ValueError('validation cannot overwrite inputs')
    for c in cases['cases']:
        if output.resolve().is_relative_to(Path(c['source_package']).resolve()):raise ValueError('validation cannot overwrite immutable package')
    byid={r['id']:r for r in rows};results=[]
    for case in cases['cases']:
        page=bound(case);row=byid.get(case['id']);item={'id':case['id'],'page':case['page'],'source_pdf_sha256':case['source_pdf_sha256'],'page_text_sha256':case['page_text_sha256'],'publication_eligible':False,'source_quality_changed':False}
        if row is None:item['status']='not_run'
        elif any(row.get(k)!=case[k] for k in ('image_sha256','source_pdf_sha256','page_text_sha256','page')):raise ValueError('stale reading source identity')
        elif row.get('error') or row.get('possible_token_cap') is not False:item['status']='incomplete_reading'
        else:
            item['comparison']=compare(page['text'],row['text'])
            item['status']='literal_agreement_requires_layout_proof' if all(item['comparison'].values()) else 'source_difference_requires_repair'
            from difflib import unified_diff
            item['diff']='\n'.join(unified_diff(page['text'].splitlines(),row['text'].splitlines(),fromfile='selected',tofile='qwen',lineterm=''))
            if '<table' in row['text'].lower():item['table_comparison']=compare_source_tables(case,row['text'])
        results.append(item)
    from collections import Counter
    report={'schema_version':SCHEMA,'cases_sha256':digest(cases_path),'readings_sha256':digest(readings_path),'counts':dict(Counter(r['status'] for r in results)),
        'cases':len(results),'results':results,'cloudflare_operations':0,'publication_eligible':False,
        'acceptance_rule':'Literal agreement is supporting evidence only. Layout, complete source coverage, tables and any correction require independent proof; immutable L1 packages remain unchanged.'}
    output.parent.mkdir(parents=True,exist_ok=True);save(output,report)
    return {'command':'l1-qwen-validate','cases':len(results),'counts':report['counts'],'output':str(output),'source_quality_changed':False}

def reproof(cases_path:Path,readings_path:Path,packages:Path,output:Path):
    """Publish a new immutable L1 version using exact visual agreement as witness.

    Text is never replaced here. Unaccounted ink, table layout and other original
    gates still have to pass. Hash-bound model evidence is preserved in each copy.
    """
    import shutil,tempfile
    from .legal_l1 import verify_document,rows,write_rows,write_json,readiness_ledger
    from .quality_gate import assess_package
    from .quality_routes import matching_routes
    import pypdfium2 as pdfium
    from io import BytesIO
    packages=packages.resolve();output=output.resolve()
    if output.exists() or output==packages or output.is_relative_to(packages):raise ValueError('reproof requires a new output outside immutable inputs')
    if not packages.is_dir():raise ValueError('missing source packages')
    folders=sorted(p for p in packages.iterdir() if (p/'l1-package.json').exists())
    if not folders:raise ValueError('no L1 packages found')
    for folder in folders:verify_document(folder)
    cases=read(cases_path)['cases'];readings=read(readings_path)
    source_ids={digest(f/'original.pdf') for f in folders}
    if any(c['source_pdf_sha256'] not in source_ids for c in cases):raise ValueError('case outside declared source identity set')
    for folder in folders:
        current={p['page']:p['text_sha256'] for p in rows(folder/'extraction/pages.jsonl')}
        for case in cases:
            if case['source_pdf_sha256']==digest(folder/'original.pdf') and current.get(case['page'])!=case['page_text_sha256']:
                raise ValueError('selected source text changed; rerun local reading validation')
    # Rerender every model input from the captured original, never trust a crop label.
    for case in cases:
        with pdfium.PdfDocument(Path(case['source_package'])/'original.pdf') as pdf:
            page=pdf[case['page']-1]
            try:im=page.render(scale=2.5).to_pil().convert('RGB')
            finally:page.close()
        stream=BytesIO();im.save(stream,format='PNG')
        if hashlib.sha256(stream.getvalue()).hexdigest()!=case['image_sha256']:raise ValueError('model image does not match original page raster')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.qwen-reproof-',dir=output.parent) as temp:
        stage=Path(temp)/'packages';stage.mkdir()
        validation_path=Path(temp)/'validation.json'
        validate(cases_path,readings_path,validation_path)
        validated=read(validation_path)
        if any(r['status'] in {'not_run','incomplete_reading'} for r in validated['results']):raise ValueError('finish all complete readings before reproof')
        eligible={r['id'] for r in validated['results'] if r['status']=='literal_agreement_requires_layout_proof'}
        byid={r['id']:r for r in readings['results']};results=[];newly_accepted=0
        for folder in folders:
            dest=stage/folder.name;shutil.copytree(folder,dest)
            local=[c for c in cases if c['source_pdf_sha256']==digest(folder/'original.pdf') and c['id'] in eligible]
            if local:
                layout=rows(dest/'layout.jsonl');witnesses={c['page']:byid[c['id']] for c in local}
                for row in layout:
                    if row['page'] in witnesses:
                        witness=witnesses[row['page']]
                        row.update(quality_independent_text=witness['text'],quality_independent_text_sha256=hashlib.sha256(witness['text'].encode()).hexdigest(),quality_independent_basis='source_rerendered_pinned_local_qwen_exact_literal_agreement')
                write_rows(dest/'layout.jsonl',layout)
                quality=assess_package(dest/'extraction',dest/'original.pdf',derive_ocr_source_raster=True,
                    independent_by_page={p['page']:p['quality_independent_text'] for p in layout if p['quality_independent_text'] is not None},derive_independent_native=False)
                quality['independent_extraction_mode']='native_readers_plus_exact_pinned_qwen_source_raster_witness'
                write_json(dest/'quality.json',quality)
                write_json(dest/'readiness.json',readiness_ledger(read(dest/'structure.json'),quality))
                jobs=[]
                for r in quality['results']:
                    if r['outcome']=='auto_accepted':continue
                    jobs.append({'page':r['page'],'source_pdf_sha256':quality['source_pdf_sha256'],'page_text_sha256':r['page_text_sha256'],'status':'awaiting_source_proof','failed_components':r.get('failed_components',[]),'missing_evidence':r.get('missing_evidence',[]),'routes':[{'id':rule['id'],'action':rule['action']} for rule in matching_routes(r)],'mutation_policy':'Repair a new extraction version; never rewrite this immutable L1 package.'})
                write_rows(dest/'repair-queue.jsonl',jobs)
                evidence={'schema_version':SCHEMA,'parent_package_sha256':digest(folder/'l1-package.json'),'readings_sha256':digest(readings_path),'cases_sha256':digest(cases_path),'model':MODEL,'revision':REVISION,'prompt':PROMPT,'max_tokens':readings.get('max_tokens'),'runtime':readings.get('runtime'),'offline':readings.get('offline'),'source_raster_rerender_verified':True,'text_modified':False,'results':[byid[c['id']] for c in local],'publication_eligible':False}
                write_json(dest/'vision-evidence.json',evidence)
                manifest=read(dest/'l1-package.json')
                manifest['request_sha256']=hashlib.sha256(json.dumps({'parent':manifest['request_sha256'],'vision':digest(dest/'vision-evidence.json')},sort_keys=True).encode()).hexdigest()
                manifest['artifacts']['vision-evidence.json']=digest(dest/'vision-evidence.json')
                for name in manifest['artifacts']:manifest['artifacts'][name]=digest(dest/name)
                write_json(dest/'l1-package.json',manifest)
            before=read(folder/'quality.json')['counts']['auto_accepted']
            result=verify_document(dest);newly_accepted+=result['quality_counts']['auto_accepted']-before
            results.append({'id':folder.name,**result,'exact_qwen_witness_pages':[c['page'] for c in local]})
        stage.rename(output)
    summary={'command':'l1-qwen-reproof','documents':len(results),'pages':sum(r['pages'] for r in results),'newly_accepted':newly_accepted,'auto_accepted':sum(r['quality_counts']['auto_accepted'] for r in results),'repair_required':sum(r['quality_counts']['repair_required'] for r in results),'source_text_modified':False,'publication_eligible':False,'results':results,'output':str(output)}
    save(output/'reproof-receipt.json',summary)
    return {k:v for k,v in summary.items() if k!='results'}

def verify_vision_evidence(folder:Path,pages:list,pdf_sha:str):
    """Reopen and rebind optional L1 visual witnesses to the actual original."""
    from io import BytesIO
    import pypdfium2 as pdfium
    evidence=read(folder/'vision-evidence.json')
    if evidence.get('schema_version')!=SCHEMA or evidence.get('model')!=MODEL or evidence.get('revision')!=REVISION or evidence.get('prompt')!=PROMPT:
        raise ValueError('unsupported L1 vision evidence contract')
    rows=evidence.get('results',[]);ids=[r.get('page') for r in rows]
    if not rows or len(ids)!=len(set(ids)):raise ValueError('duplicate or empty L1 vision evidence')
    bypage={p['page']:p for p in pages}
    layout={p['page']:p for p in map(json.loads,jsonl_physical_lines((folder/'layout.jsonl').read_text()))}
    with pdfium.PdfDocument(folder/'original.pdf') as pdf:
        for row in rows:
            p=bypage.get(row.get('page'))
            if p is None or row.get('source_pdf_sha256')!=pdf_sha or row.get('page_text_sha256')!=p['text_sha256'] or row.get('possible_token_cap') is not False or not all(compare(p['text'],row['text']).values()):
                raise ValueError('L1 vision witness does not exactly support selected source text')
            if layout[p['page']]['quality_independent_text']!=row['text']:raise ValueError('L1 layout does not retain vision witness')
            page=pdf[p['page']-1]
            try:image=page.render(scale=2.5).to_pil().convert('RGB')
            finally:page.close()
            stream=BytesIO();image.save(stream,format='PNG')
            if hashlib.sha256(stream.getvalue()).hexdigest()!=row.get('image_sha256'):raise ValueError('L1 vision source raster changed')
    return True

def html_tables(text:str):
    """Read model HTML as data only; never execute or render it."""
    from html.parser import HTMLParser
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None;self.spans=False;self.invalid=False
        def handle_starttag(self,tag,attrs):
            if tag=='table':
                if self.table is not None:self.invalid=True
                else:self.table=[];self.spans=False
            if tag=='tr' and self.table is not None:
                if self.row is not None:self.invalid=True
                self.row=[]
            if tag in ('td','th') and self.table is not None:
                if self.cell is not None or self.row is None:self.invalid=True
                self.cell='';a=dict(attrs)
                self.spans|=a.get('rowspan','1')!='1' or a.get('colspan','1')!='1'
            if tag=='br' and self.cell is not None:self.cell+='\n'
        def handle_data(self,text):
            if self.cell is not None:self.cell+=text
        def handle_endtag(self,tag):
            if tag in ('td','th') and self.cell is not None:
                if self.row is not None:self.row.append(self.cell)
                self.cell=None
            if tag=='tr' and self.row is not None:
                self.table.append(self.row);self.row=None
            if tag=='table' and self.table is not None:
                self.tables.append({'rows':self.table,'unsupported_spans':self.spans,'invalid_structure':self.invalid or self.row is not None or self.cell is not None});self.table=None
    parser=Reader();parser.feed(text);parser.close()
    if parser.table is not None:return [],False
    return parser.tables,not parser.invalid

def compare_source_tables(case:dict,text:str):
    """Check each distinct illustration and every cell against its source grid."""
    from .l1_review import preview_tables
    tables,valid=html_tables(text)
    source=preview_tables(Path(case['source_package'])/'original.pdf',case['page'],case['source_pdf_sha256'])
    report={'source_table_count':len(source),'qwen_table_count':len(tables),'count_exact':valid and len(source)==len(tables),'tables':[],'page_approved':False}
    for index,table in enumerate(source):
        grid=[['' for _ in range(table['column_count'])] for _ in range(table['row_count'])]
        for c in table['cell_spans']:grid[c['row']][c['column']]=c.get('text') or ''
        candidate=tables[index] if index<len(tables) else None
        actual=candidate['rows'] if candidate else []
        shape=bool(candidate and not candidate['unsupported_spans'] and not candidate['invalid_structure'] and len(actual)==len(grid) and all(len(r)==table['column_count'] for r in actual))
        checks=[]
        if shape:
            for r,row in enumerate(grid):
                for col,value in enumerate(row):
                    checks.append({'row':r,'column':col,'source_value':value,'qwen_value':actual[r][col],'exact':all(compare(value,actual[r][col]).values())})
        report['tables'].append({'source_table_version_id':table['table_version_id'],'caption':table['caption'],'shape_exact':shape,'expected_shape':[len(grid),table['column_count']],'actual_row_widths':[len(r) for r in actual],
            'all_cells_exact':shape and all(c['exact'] for c in checks),'data_cells_exact':shape and all(c['exact'] for c in checks if c['row']>0),
            'independent_native_cell_agreement':all(c['readers_agree'] for c in table['cell_spans']),'cell_checks':checks})
    return report
