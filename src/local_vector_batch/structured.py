"""Offline layout-guided visual reading. All generated content remains candidate data.

The detector sees original PDFs; the reader receives only source image crops and
fixed task prompts. Neither stage accepts benchmark labels or answer text.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
from html import escape
from html.parser import HTMLParser
import hashlib,json,math,os,platform,re,subprocess,sys,time
from importlib.metadata import version

MODEL='mlx-community/PaddleOCR-VL-1.5-4bit'
REVISION='3404726b40ac30b6e4e3d37c504e3f96c9c6c984'
LAYOUT_MODEL='docling-project/docling-layout-heron'
LAYOUT_REVISION='8f39ad3c0b4c58e9c2d2c84a38465abf757272d8'
PROMPTS={'text':'OCR:','table':'Table Recognition:','formula':'Formula Recognition:'}
FURNITURE={'page_header','page_footer'}


def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def sources(path):
    data=load(path)
    if data.get('schema_version')!='pdf2data.source-batch.v1':raise ValueError('unsupported source batch')
    rows=data.get('sources',[])
    if not rows or len({r['id'] for r in rows})!=len(rows):raise ValueError('empty or duplicate sources')
    for r in rows:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',r['id']):raise ValueError('invalid source ID')
        if sha(Path(r['path']).read_bytes())!=r['sha256']:raise ValueError('source PDF changed')
    return rows


def geometry_order(regions):
    """Recursive whitespace cuts; no text, labels or benchmark knowledge.

    Full-width headings separate before column splits. Unsplittable regions use
    top-left order. This is a candidate reading order, never a verified order.
    """
    if len(regions)<2:return list(regions)
    def gaps(axis):
        intervals=sorted((r['bbox'][axis],r['bbox'][axis+2]) for r in regions)
        end=intervals[0][1];result=[]
        for start,stop in intervals[1:]:
            if start>end:result.append((start-end,(start+end)/2))
            end=max(end,stop)
        return result
    x0=min(r['bbox'][0] for r in regions);x1=max(r['bbox'][2] for r in regions)
    y0=min(r['bbox'][1] for r in regions);y1=max(r['bbox'][3] for r in regions)
    # Paragraph-sized horizontal gaps delimit full-width bands before columns.
    gx=max(gaps(0),key=lambda g:(g[0],-g[1]),default=(0,None));gy=max(gaps(1),key=lambda g:(g[0],-g[1]),default=(0,None))
    axis=None;cut=None
    if gx[0]>=max(6,(x1-x0)*.025):axis,cut=0,gx[1]
    elif gy[0]>=max(3,(y1-y0)*.015):axis,cut=1,gy[1]
    if axis is None:return sorted(regions,key=lambda r:(r['bbox'][1],r['bbox'][0],r['id']))
    first=[r for r in regions if r['bbox'][axis+2]<=cut];second=[r for r in regions if r['bbox'][axis]>=cut]
    if len(first)+len(second)!=len(regions) or not first or not second:return sorted(regions,key=lambda r:(r['bbox'][1],r['bbox'][0],r['id']))
    return geometry_order(first)+geometry_order(second)


def contained_region_partition(regions):
    """Read a detected table once; retain fully contained text as evidence.

    Only strict source-box containment establishes ownership. Partial overlaps,
    neighbouring captions and ambiguous nested containers remain independent.
    """
    active=[];suppressed=[]
    for region in regions:
        if region['kind']=='table':active.append(region);continue
        b=region['bbox'];owners=[t for t in regions if t['kind']=='table' and t['bbox'][0]<=b[0] and t['bbox'][1]<=b[1] and t['bbox'][2]>=b[2] and t['bbox'][3]>=b[3]]
        if len(owners)==1:suppressed.append({**region,'represented_by':owners[0]['id'],'reason':'inside_table_source_crop'})
        else:active.append(region)
    return active,suppressed


def detect(manifest,output,device='mps',dpi=180):
    rows=sources(manifest);output=output.resolve()
    if type(dpi) is not int or not 72<=dpi<=300:raise ValueError('layout DPI must be 72..300')
    if output.exists():raise ValueError('layout output exists')
    if device not in {'mps','cpu','cuda'}:raise ValueError('explicit layout device required')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter,PdfFormatOption
    from .pdf_compare import _source_raster,page_region_records
    import torch
    if device=='mps' and not torch.backends.mps.is_available():raise ValueError('MPS unavailable; no CPU fallback')
    options=PdfPipelineOptions(do_ocr=False,do_table_structure=False,accelerator_options=AcceleratorOptions(device=device,num_threads=4))
    options.layout_options.keep_empty_clusters=True
    options.layout_options.skip_cell_assignment=True
    options.layout_options.create_orphan_clusters=False
    options.layout_options.model_spec.repo_id=LAYOUT_MODEL
    options.layout_options.model_spec.revision=LAYOUT_REVISION
    model_cache=Path(os.environ.get('HF_HUB_CACHE',Path.home()/'.cache/huggingface/hub'))/('models--'+LAYOUT_MODEL.replace('/','--'))/'snapshots'/LAYOUT_REVISION
    if not model_cache.is_dir():raise ValueError('pinned detector must be cached locally')
    detector_identity={'repo':LAYOUT_MODEL,'revision':LAYOUT_REVISION,'files_sha256':{p.name:sha(p.read_bytes()) for p in model_cache.iterdir() if p.is_file()},'runtime':{n:version(n) for n in ['docling','docling-core','torch','transformers']},'options':options.layout_options.model_dump(mode='json')}
    converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    start=time.perf_counter();converter.initialize_pipeline(InputFormat.PDF)
    forward=[];hooks=[]
    for pipeline in converter.initialized_pipelines.values():
        model=getattr(getattr(getattr(pipeline,'layout_model',None),'engine',None),'_model',None)
        if model is not None:
            def observe(module,args,kwargs):forward.append({'parameters':sorted({str(p.device) for p in module.parameters()}),'inputs':sorted({str(v.device) for v in kwargs.values() if hasattr(v,'device')})})
            hooks.append(model.register_forward_pre_hook(observe,with_kwargs=True))
    output.mkdir(parents=True);documents=[]
    from pypdf import PdfReader
    from PIL import Image
    import numpy as np
    try:
        for source in rows:
            document={'id':source['id'],'source_pdf_sha256':source['sha256'],'source_pdf_path':source['path'],'pages':[]}
            folder=output/source['id'];folder.mkdir()
            reader=PdfReader(source['path']);page_count=len(reader.pages)
            if source.get('pages',page_count)!=page_count:raise ValueError('source page count changed')
            for number in range(1,page_count+1):
                from .ocr import bounded_ocr_dpi
                box=reader.pages[number-1].mediabox
                actual_dpi=bounded_ocr_dpi(float(box.width),float(box.height),base_dpi=dpi)
                t=time.perf_counter();raster=_source_raster(Path(source['path']),number,folder,dpi=actual_dpi)
                width,height=raster['page_size_points'];regions=[];error=None;event_start=len(forward)
                try:
                    result=converter.convert(source['path'],page_range=(number,number));data=result.document.export_to_dict()
                    clusters=[c.model_dump(mode='json') for p in result.pages if p.page_no==number and p.predictions.layout for c in p.predictions.layout.clusters]
                    page={'id':source['id']+':p'+str(number),'page':number,'text_sha256':sha(b'')}
                    candidates=page_region_records(data,page,source_sha=source['sha256'],render_sha=raster['rendered_image_sha256'],page_size=(width,height),clusters=clusters)
                    # Tree positions are retained as metadata; geometry is used
                    # uniformly because empty scan clusters may lack tree refs.
                    for index,r in enumerate(candidates):
                        b=r['bbox']
                        if any(not math.isfinite(v) for v in b) or not 0<=b[0]<b[2]<=width+.5 or not 0<=b[1]<b[3]<=height+.5:raise ValueError('layout box outside original source')
                        regions.append({'id':f'{source["id"]}:p{number}:r{index}','kind':r['kind'],'bbox':b,'detector_tree_order':r['reading_order_index'],'order_verified':False})
                    if not regions:raise ValueError('detector returned no regions')
                except Exception as exc:
                    error=str(exc)[:400];regions=[{'id':f'{source["id"]}:p{number}:fallback','kind':'unresolved_full_page','bbox':[0,0,width,height],'detector_tree_order':None,'order_verified':False}]
                image_path=folder/f'page-{number}.source.png'
                with Image.open(image_path) as image:
                    ink=np.asarray(image.convert('L'))<100;covered=np.zeros(ink.shape,dtype=bool)
                    for region in regions:
                        b=region['bbox'];bounds=[max(0,math.floor(b[0]/width*image.width)-2),max(0,math.floor(b[1]/height*image.height)-2),min(image.width,math.ceil(b[2]/width*image.width)+2),min(image.height,math.ceil(b[3]/height*image.height)+2)]
                        crop=folder/(region['id'].split(':')[-1]+f'-p{number}.png');image.crop(bounds).save(crop)
                        region.update(crop_path=str(crop),crop_sha256=sha(crop.read_bytes()),crop_bbox_pixels=bounds)
                        covered[bounds[1]:bounds[3],bounds[0]:bounds[2]]=True
                    coverage={'source_dark_pixels':int(ink.sum()),'uncovered_dark_pixels':int((ink&~covered).sum()),'semantic_coverage_verified':False}
                regions,suppressed=contained_region_partition(regions)
                ordered=geometry_order(regions)
                for index,r in enumerate(ordered):r['reading_order_index']=index
                document['pages'].append({'page':number,'source_pdf_sha256':source['sha256'],'source_image':str(image_path),'source_image_sha256':raster['rendered_image_sha256'],'page_size_points':[width,height], 'render':raster,'regions':ordered,'contained_regions':suppressed,'coverage':coverage,'layout_error':error,'layout_forward_events':forward[event_start:],'seconds':time.perf_counter()-t})
                write(output/'layout.json',{'schema_version':'pdf2data.region-layout.v1','source_manifest_sha256':sha(Path(manifest).read_bytes()),'documents':documents+[document], 'device_requested':device,'detector':'docling-heron','docling_version':version('docling'),'detector_identity':detector_identity,'publication_eligible':False})
                print(json.dumps({'event':'layout_page','id':source['id'],'page':number,'regions':len(regions),'fallback':bool(error)}),flush=True)
            documents.append(document)
    finally:
        for hook in hooks:hook.remove()
    record={'schema_version':'pdf2data.region-layout.v1','source_manifest_sha256':sha(Path(manifest).read_bytes()),'documents':documents,'device_requested':device,'detector':'docling-heron','docling_version':version('docling'),'detector_identity':detector_identity,'wall_seconds':time.perf_counter()-start,'publication_eligible':False}
    write(output/'layout.json',record);return {'output':str(output/'layout.json'),'documents':len(documents),'wall_seconds':record['wall_seconds'],'failed_regions':record.get('total_failed_regions',0)}


class TableParser(HTMLParser):
    """Parse only explicit model cells; never render arbitrary generated HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True);self.tables=[];self.rows=None;self.row=None;self.cell=None;self.depth=0
    def handle_starttag(self,tag,attrs):
        if tag=='table':
            if self.depth:raise ValueError('nested candidate table')
            self.depth=1;self.rows=[]
        elif self.depth and tag=='tr':
            if self.row is not None:raise ValueError('unclosed candidate row')
            self.row=[]
        elif self.depth and tag in {'th','td'}:
            if self.row is None or self.cell is not None:raise ValueError('candidate cell outside row or nested')
            attrs=dict(attrs);rs=int(attrs.get('rowspan',1));cs=int(attrs.get('colspan',1))
            if not 1<=rs<=100 or not 1<=cs<=100:raise ValueError('invalid candidate span')
            self.cell={'text':'','row_span':rs,'column_span':cs,'role_candidate':'header' if tag=='th' else 'unresolved'}
        elif self.cell is not None and tag=='br':self.cell['text']+='\n'
    def handle_data(self,text):
        if self.cell is not None:self.cell['text']+=text
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.depth:
            if self.cell is None:raise ValueError('unmatched cell close')
            self.cell['text']=self.cell['text'].strip();self.row.append(self.cell);self.cell=None
        elif tag=='tr' and self.depth:
            if self.cell is not None:raise ValueError('unclosed table cell')
            self.rows.append(self.row or []);self.row=None
        elif tag=='table':
            if not self.depth or self.row is not None or self.cell is not None:raise ValueError('unclosed candidate table')
            self.tables.append(self.rows);self.depth=0;self.rows=None


def otsl_table(text,page,region):
    """Strict OTSL continuation links; no ragged-row padding or value filling.

    Token meanings follow the public Docling OTSL format. Unlike a permissive
    renderer, malformed continuations are rejected rather than repaired.
    """
    text=text.strip()
    if text.startswith('<otsl>') and text.endswith('</otsl>'):text=text[6:-7]
    pattern=re.compile(r'<(fcel|ecel|ched|rhed|srow|lcel|ucel|xcel|nl)>')
    matches=list(pattern.finditer(text))
    if not matches or text[:matches[0].start()].strip():raise ValueError('invalid OTSL prefix')
    rows=[];row=[]
    for index,match in enumerate(matches):
        kind=match.group(1);value=text[match.end():matches[index+1].start() if index+1<len(matches) else len(text)]
        value=re.sub(r'</'+kind+r'>\s*$','',value).strip()
        if kind=='nl':
            if value:raise ValueError('text attached to OTSL newline')
            if not row:raise ValueError('empty OTSL row')
            rows.append(row);row=[]
        else:row.append((kind,value))
    if row:rows.append(row)
    if not rows or len({len(row) for row in rows})!=1:raise ValueError('ragged or empty OTSL grid')
    owners={};anchors={}
    for r,row in enumerate(rows):
        for c,(kind,value) in enumerate(row):
            if kind in {'fcel','ecel','ched','rhed','srow'}:
                if kind=='ecel' and value:raise ValueError('text attached to empty OTSL cell')
                owners[r,c]=(r,c);anchors[r,c]={'text':value,'role_candidate':'header' if kind in {'ched','rhed'} else 'unresolved'}
            else:
                if value:raise ValueError('text attached to continuation OTSL cell')
                left=owners.get((r,c-1));up=owners.get((r-1,c))
                owner=left if kind=='lcel' else up if kind=='ucel' else left if left==up else None
                if owner is None:raise ValueError('orphan OTSL continuation')
                owners[r,c]=owner
    if not any(anchor['text'].strip() for anchor in anchors.values()):raise ValueError('empty candidate table')
    grid=[[None]*len(rows[0]) for _ in rows];spans=[]
    for (r,c),anchor in anchors.items():
        slots={slot for slot,owner in owners.items() if owner==(r,c)}
        rs=max(y for y,x in slots)-r+1;cs=max(x for y,x in slots)-c+1
        if slots!={(y,x) for y in range(r,r+rs) for x in range(c,c+cs)}:raise ValueError('nonrectangular OTSL merge')
        grid[r][c]=anchor['text'];spans.append({**anchor,'row':r,'column':c,'row_span':rs,'column_span':cs})
    table={'id':region['id']+':t0','page':page,'bbox':region['bbox'],'cells':grid,'cell_spans':spans,'extraction_basis':'paddleocr_vl_source_crop_otsl_candidate','source_crop_sha256':region['crop_sha256'],'status':'needs_row_column_review'}
    from .document_blocks import validated_cells
    validated_cells(table);return [table]


def parse_tables(text,page,region):
    if re.search(r'<(?:fcel|ecel|ched)>',text):return otsl_table(text,page,region)
    parser=TableParser();parser.feed(text);parser.close()
    if parser.depth:raise ValueError('truncated candidate table')
    if not parser.tables:raise ValueError('table task did not return explicit HTML cells')
    result=[]
    for index,rows in enumerate(parser.tables):
        spans=[];occupied=set();max_column=0
        for r,row in enumerate(rows):
            c=0
            for raw in row:
                while (r,c) in occupied:c+=1
                cell={**raw,'row':r,'column':c}
                if r+cell['row_span']>len(rows):raise ValueError('candidate span outside row count')
                slots={(y,x) for y in range(r,r+cell['row_span']) for x in range(c,c+cell['column_span'])}
                if slots&occupied:raise ValueError('overlapping candidate table cells')
                occupied|=slots;spans.append(cell);c+=cell['column_span'];max_column=max(max_column,c)
        if not rows or not any(str(c['text']).strip() for c in spans):raise ValueError('empty candidate table')
        grid=[[None]*max_column for _ in rows]
        for cell in spans:grid[cell['row']][cell['column']]=cell['text']
        table={'id':region['id']+':t'+str(index),'page':page,'bbox':region['bbox'],'cells':grid,'cell_spans':spans,'extraction_basis':'paddleocr_vl_source_crop_candidate','source_crop_sha256':region['crop_sha256'],'status':'needs_row_column_review'}
        from .document_blocks import validated_cells
        validated_cells(table);result.append(table)
    return result


def verify_page_source(document,page):
    """Re-render the original PDF and compare the exact page/crop pixels."""
    from io import BytesIO
    from PIL import Image
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(document['source_pdf_path']) as pdf:
        physical=pdf[page['page']-1]
        try:
            if any(abs(a-b)>.01 for a,b in zip(physical.get_size(),page['page_size_points'])):raise ValueError('source page dimensions changed')
            bitmap=physical.render(scale=page['render']['render_dpi']/72)
            try:image=bitmap.to_pil().copy()
            finally:bitmap.close()
        finally:physical.close()
    stream=BytesIO();image.save(stream,format='PNG')
    if sha(stream.getvalue())!=page['source_image_sha256']:raise ValueError('page raster does not match original PDF')
    for region in page['regions']:
        bounds=region['crop_bbox_pixels']
        if len(bounds)!=4 or any(type(v) is not int for v in bounds) or not 0<=bounds[0]<bounds[2]<=image.width or not 0<=bounds[1]<bounds[3]<=image.height:raise ValueError('invalid source crop bounds')
        b=region['bbox'];width,height=page['page_size_points']
        expected_bounds=[max(0,math.floor(b[0]/width*image.width)-2),max(0,math.floor(b[1]/height*image.height)-2),min(image.width,math.ceil(b[2]/width*image.width)+2),min(image.height,math.ceil(b[3]/height*image.height)+2)]
        if bounds!=expected_bounds:raise ValueError('crop bounds differ from source geometry')
        path=Path(region['crop_path'])
        if sha(path.read_bytes())!=region['crop_sha256']:raise ValueError('source crop changed')
        with Image.open(path) as crop:
            expected=image.crop(bounds)
            if crop.mode!=expected.mode or crop.size!=expected.size or crop.tobytes()!=expected.tobytes():raise ValueError('crop differs from original source pixels')


def read_regions(layout_path,output,device='gpu',max_tokens=4096,resume=False,reuse_readings=None,text_fallback='none'):
    layout=load(layout_path);output=output.resolve()
    if layout.get('schema_version')!='pdf2data.region-layout.v1':raise ValueError('unsupported region layout')
    if output.exists() and not resume:raise ValueError('region reading output exists; use explicit resume')
    if device not in {'gpu','cpu'} or not 256<=max_tokens<=16384:raise ValueError('invalid visual reading settings')
    if text_fallback not in {'none','vision'}:raise ValueError('invalid text fallback')
    from .region_fallback import vision_identity,vision_attempt,validate_lines
    fallback_identity=vision_identity() if text_fallback=='vision' else None
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    cache=Path(os.environ.get('HF_HUB_CACHE',Path.home()/'.cache/huggingface/hub'))
    snapshot=cache/('models--'+MODEL.replace('/','--'))/'snapshots'/REVISION
    if not snapshot.is_dir():raise ValueError('pinned visual reader must be cached locally')
    import mlx.core as mx
    if device=='gpu' and not mx.metal.is_available():raise ValueError('Metal unavailable; no CPU fallback')
    mx.set_default_device(mx.gpu if device=='gpu' else mx.cpu)
    from mlx_vlm import load as load_model,generate
    model_hashes={p.name:sha(p.read_bytes()) for p in snapshot.iterdir() if p.is_file()}
    contract={'layout_sha256':sha(Path(layout_path).read_bytes()),'model':MODEL,'revision':REVISION,'model_files_sha256':model_hashes,'device':device,'max_tokens':max_tokens,'prompts':PROMPTS,'implementation_sha256':sha(Path(__file__).read_bytes()),'runtime':{n:version(n) for n in ['mlx','mlx-vlm','transformers']}}
    contract['text_fallback']=fallback_identity
    contract['retry_implementation_sha256']={name:sha(Path(__file__).with_name(name).read_bytes()) for name in ('region_fallback.py','ocr.py','vision_ocr.swift')}
    inherited={}
    if reuse_readings is not None:
        parent=Path(reuse_readings).resolve();parent_record=load(parent/'read.json')
        for key,expected in {'model':MODEL,'revision':REVISION,'model_files_sha256':model_hashes,'prompts':PROMPTS,'max_tokens':max_tokens,'runtime':contract['runtime']}.items():
            if parent_record.get(key)!=expected:raise ValueError('inherited reader model/runtime/input contract changed: '+key)
        if parent_record['device_used']!=str(mx.default_device()):raise ValueError('inherited device changed')
        parent_files={}
        for file in sorted(parent.glob('*/readings.json')):
            parent_files[str(file.relative_to(parent))]=sha(file.read_bytes())
            for row in load(file):
                if row.get('reader_engine')=='macos_vision' and (fallback_identity is None or parent_record.get('text_fallback')!=fallback_identity):continue
                if row['status']=='candidate' and row.get('text_sha256')==sha(row['text'].encode()):
                    key=(row['source_pdf_sha256'],row['page'],row['source_crop_sha256'],row['task'])
                    if key in inherited and inherited[key]['text']!=row['text']:raise ValueError('conflicting inherited visual readings')
                    inherited[key]=row
        contract['inherited_readings']={'path':str(parent),'read_sha256':sha((parent/'read.json').read_bytes()),'files_sha256':parent_files}
    output.mkdir(parents=True,exist_ok=True)
    if (output/'contract.json').exists() and load(output/'contract.json')!=contract:raise ValueError('reading resume contract changed')
    if resume and not (output/'contract.json').exists():raise ValueError('resume contract is missing')
    write(output/'contract.json',contract)
    start=time.perf_counter();model,processor=load_model(str(snapshot));load_seconds=time.perf_counter()-start
    documents=[];reused_regions=0
    for document in layout['documents']:
        if sha(Path(document['source_pdf_path']).read_bytes())!=document['source_pdf_sha256']:raise ValueError('source changed before visual reading')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',document['id']):raise ValueError('invalid layout document ID')
        dest=output/document['id'];dest.mkdir(exist_ok=True);pages=[];tables=[];readings=[]
        saved=load(dest/'readings.json') if resume and (dest/'readings.json').exists() else []
        if len({r['region_id'] for r in saved})!=len(saved):raise ValueError('duplicate saved region')
        prior={r['region_id']:r for r in saved}
        region_ids=[r['id'] for p in document['pages'] for r in p['regions']]
        if len(region_ids)!=len(set(region_ids)):raise ValueError('duplicate source region ID')
        numbers=[p['page'] for p in document['pages']]
        from pypdf import PdfReader
        if numbers!=list(range(1,len(PdfReader(document['source_pdf_path']).pages)+1)):raise ValueError('missing or duplicate physical page')
        for page in document['pages']:
            verify_page_source(document,page)
            if sha(Path(page['source_image']).read_bytes())!=page['source_image_sha256']:raise ValueError('source image changed')
            body=[];all_text=[];blocks=[];page_tables=[];problems=[];visual_review=[]
            for region in page['regions']:
                if sha(Path(region['crop_path']).read_bytes())!=region['crop_sha256']:raise ValueError('source crop changed')
                if region['kind'] in {'picture','chart','document_index'}:
                    visual_review.append({**region,'status':'visual_review_required','text_completeness_verified':False});continue
                task='table' if region['kind']=='table' else 'formula' if region['kind'] in {'formula','equation'} else 'text'
                prompt=processor.apply_chat_template([{'role':'user','content':[{'type':'image'},{'type':'text','text':PROMPTS[task]}]}],tokenize=False,add_generation_prompt=True)
                t=time.perf_counter();entry={'region_id':region['id'],'page':page['page'],'task':task,'source_crop_sha256':region['crop_sha256'],'source_pdf_sha256':document['source_pdf_sha256'],'bbox':region['bbox'],'kind':region['kind'],'status':'candidate','content_verified':False,'reading_order_verified':False}
                recovery={k:region[k] for k in ('recovery_parent_region','recovery_kind','table_fragment_of') if region.get(k) is not None}
                entry.update(recovery)
                result=None
                try:
                    previous=prior.get(region['id']) or inherited.get((document['source_pdf_sha256'],page['page'],region['crop_sha256'],task))
                    if previous and previous['status']=='candidate':
                        if previous['source_crop_sha256']!=region['crop_sha256'] or previous['task']!=task or previous.get('text_sha256')!=sha(previous['text'].encode()):raise ValueError('saved region identity mismatch')
                        from types import SimpleNamespace
                        result=SimpleNamespace(text=previous['text'],generation_tokens=previous['tokens']);reused_regions+=1
                    else:result=generate(model,processor,prompt,image=region['crop_path'],max_tokens=max_tokens,temp=0.0,verbose=False)
                    text=result.text.strip();tokens=getattr(result,'generation_tokens',None)
                    entry.update(text=text,tokens=tokens,text_sha256=sha(text.encode()),generation_reused=bool(previous and previous['status']=='candidate'))
                    reason='missing_token_count' if type(tokens) is not int else 'token_cap' if tokens>=max_tokens else 'empty_reading' if not text else None
                    if previous and previous.get('reader_engine')=='macos_vision':
                        attempt=previous.get('fallback_attempt',{})
                        if fallback_identity is None or not attempt.get('accepted') or validate_lines(attempt.get('lines')) or attempt.get('text')!=text:raise ValueError('saved fallback identity or lines changed')
                        entry.update(reader_engine='macos_vision',fallback_attempt=attempt,paddle_attempt=previous['paddle_attempt'])
                        reason=None
                    elif reason and task=='text' and text_fallback=='vision':
                        entry['paddle_attempt']=dict(text=text,tokens=tokens,failure_reason=reason,text_sha256=sha(text.encode()))
                        attempt=vision_attempt(region['crop_path']);entry['fallback_attempt']=attempt
                        if attempt['accepted']:
                            text=attempt['text'];tokens=None;reason=None
                            entry.update(text=text,tokens=None,text_sha256=sha(text.encode()),reader_engine='macos_vision')
                    if reason:
                        entry['failure_reason']=reason;raise ValueError(reason)
                    if task=='table':
                        candidate_tables=parse_tables(text,page['page'],region)
                        for table in candidate_tables:table.update(recovery)
                        page_tables.extend(candidate_tables)
                        from .document_blocks import table_html
                        display='\n\n'.join(table_html(table) for table in candidate_tables)
                        plain='\n'.join(' '.join(str(c) for c in row if c is not None) for table in candidate_tables for row in table['cells'])
                    elif task=='formula':
                        display=text if text.startswith(('\\[','$$','\\(')) else '\\[\n'+text+'\n\\]';plain=display
                    else:plain=display=text
                    all_text.append(plain)
                    blocks.append({'region_id':region['id'],'kind':region['kind'],'bbox':region['bbox'],'text':plain,'markdown':display,'scope':'furniture' if region['kind'] in FURNITURE else 'body','reader_engine':entry.get('reader_engine','paddleocr_vl'),**recovery})
                    if region['kind'] not in FURNITURE:body.append(plain)
                except Exception as error:
                    entry.update(status='reading_failed',error=str(error)[:400]);problems.append(region['id'])
                    # Raw generated response is retained even if table parsing or
                    # a completeness guard rejects its publication as a block.
                    if result is not None:entry['raw_response']=getattr(result,'text','')
                entry['seconds']=time.perf_counter()-t;readings.append(entry)
                write(dest/'readings.json',readings)
                print(json.dumps({'event':'region_read','id':document['id'],'page':page['page'],'region':region['id'],'task':task,'status':entry['status'],'seconds':entry['seconds']}),flush=True)
            text='\n\n'.join(body)
            method='layout_paddleocr_vl_with_vision_text_retry' if any(r.get('reader_engine')=='macos_vision' and r['page']==page['page'] and r['status']=='candidate' for r in readings) else 'layout_paddleocr_vl'
            pages.append({'page':page['page'],'text':text,'raw_region_text':'\n\n'.join(all_text),'text_sha256':sha(text.encode()),'method':method,'status':'review_required','blocks':blocks,'failed_regions':problems,'visual_review_regions':visual_review,'coverage':page['coverage'],'layout_error':page['layout_error']})
            tables.extend(page_tables)
        (dest/'pages.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in pages));write(dest/'tables.json',tables)
        md='\n\n'.join(b['markdown'] for p in pages for b in p['blocks'] if b['scope']=='body')
        (dest/'document.md').write_text(md+'\n')
        write(dest/'document.json',{'pages':pages,'tables':tables,'source_pdf_sha256':document['source_pdf_sha256'],'source_pdf_path':document['source_pdf_path'],'publication_eligible':False})
        html=[]
        for page in pages:
            html.append('<section><h2>Page '+str(page['page'])+'</h2>')
            for block in page['blocks']:
                html.append(block['markdown'] if block['kind']=='table' else '<pre>'+escape(block['text'])+'</pre>')
            html.append('<p>Source review required. Unread visual regions: '+str(len(page['visual_review_regions']))+'. Failed regions: '+str(len(page['failed_regions']))+'.</p></section>')
        (dest/'document.html').write_text('<!doctype html><meta charset="utf-8"><title>PDF2Data structured candidate</title><style>body{max-width:1000px;margin:40px auto;font:15px/1.5 system-ui;padding:20px}pre{white-space:pre-wrap;font:inherit}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #ddd;padding:6px}section{margin:30px 0}p{color:#856000}</style><h1>Structured candidate</h1><p>Unverified model transcription. Consult the original PDF and region receipts.</p>'+''.join(html))
        from .region_document import export_regions
        structured_export=export_regions(dest/'document.json',layout_path,dest/'structured',reuse_existing=resume)
        documents.append({'id':document['id'],'pages':len(pages),'failed_regions':sum(len(p['failed_regions']) for p in pages),'tables':len(tables),'output':str(dest),'structured_export':structured_export})
    record={'schema_version':'pdf2data.region-read.v1','layout_sha256':sha(Path(layout_path).read_bytes()),'model':MODEL,'revision':REVISION,'model_files_sha256':model_hashes,'prompts':PROMPTS,'max_tokens':max_tokens,'model_load_seconds':load_seconds,'wall_seconds':time.perf_counter()-start,'device_used':str(mx.default_device()),'peak_device_memory_bytes':mx.get_peak_memory(),'reused_regions':reused_regions,'runtime':{n:version(n) for n in ['mlx','mlx-vlm','transformers']},'documents':documents,'total_failed_regions':sum(d['failed_regions'] for d in documents),'publication_eligible':False}
    record['text_fallback']=fallback_identity
    write(output/'read.json',record);return {'output':str(output/'read.json'),'documents':len(documents),'device_used':record['device_used'],'wall_seconds':record['wall_seconds'],'failed_regions':record.get('total_failed_regions',0)}
