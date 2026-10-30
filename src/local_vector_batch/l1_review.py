"""Prepare native-PDF L1 repair review with the existing source-bound review UI."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json

from .legal_l1 import digest, rows, write_json, write_rows, verify_document
from .review_disagreements import discrepancies


def native_focus_boxes(raw: dict, start: int, end: int) -> list[list[float]]:
    """Candidate review crops from exact equal alignment blocks, not OCR proof."""
    wanted=[]
    for op in raw['alignment']:
        if op['kind']!='equal':continue
        lo=max(start,op['selected_start']);hi=min(max(start+1,end),op['selected_end'])
        if hi>lo:wanted.append((op['glyph_start']+lo-op['selected_start'],op['glyph_start']+hi-op['selected_start']))
    selected=[];cursor=0
    for g in raw['glyphs']:
        stop=cursor+len(g['text'])
        if g['text'].strip() and any(cursor<hi and stop>lo for lo,hi in wanted):selected.append(g['bbox'])
        cursor=stop
    if not selected:return []
    # Complete physical text bands prevent clipped neighbouring glyphs from
    # being fed to a human or a local model as if they were whole characters.
    bands=[]
    for g in sorted((g for g in raw['glyphs'] if g['text'].strip()),key=lambda g:(g['bbox'][1],g['bbox'][0])):
        box=g['bbox']
        if bands and box[1]<bands[-1]['bottom']-.1:
            bands[-1]['boxes'].append(box);bands[-1]['bottom']=max(bands[-1]['bottom'],box[3])
        else:bands.append({'bottom':box[3],'boxes':[box]})
    result=[]
    for band in bands:
        boxes=band['boxes']
        if not any(box in selected for box in boxes):continue
        result.append([max(0,(min(b[0] for b in boxes)-.3)/raw['width']),
            max(0,(min(b[1] for b in boxes)-.3)/raw['height']),
            min(1,(max(b[2] for b in boxes)+.3)/raw['width']),
            min(1,(max(b[3] for b in boxes)+.3)/raw['height'])])
    return result[:3]



def build_review(packages: Path, output: Path) -> dict:
    """Queue every repair_required page, with all non-formatting discrepancies."""
    packages=Path(packages).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('choose a new review directory')
    planned=[];page_count=0
    for package in sorted(packages.iterdir()):
        if not package.is_dir():continue
        verify_document(package)
        quality=json.loads((package/'quality.json').read_text())
        raw={r['page']:r for r in rows(package/'layout.jsonl')}
        pages={p['page']:p for p in rows(package/'extraction/pages.jsonl')}
        source=json.loads((package/'source-receipt.json').read_text())['declared']
        for result in quality['results']:
            if result['outcome']!='repair_required':continue
            page_count+=1;page=pages[result['page']];layout=raw[page['page']]
            other=layout['secondary_layout_text_raw'] or ''
            diffs=[d for d in discrepancies(page['text'],other,page,max_items=10000,include_all=True) if d['kind']!='formatting']
            for d in diffs:d['focus']['boxes']=native_focus_boxes(layout,*d['focus']['selected_span'])
            # Source-ink and table/layout failures need review even if both strings agree.
            visual=result.get('unresolved_visual_regions',[])
            if visual or not diffs:
                boxes=[v['bbox_normalized'] for v in visual[:3]]
                # Expand the candidate crop so the reviewer sees neighboring characters.
                boxes=[[max(0,b[0]-.04),max(0,b[1]-.025),min(1,b[2]+.04),min(1,b[3]+.025)] for b in boxes]
                diffs.append({'kind':'text_change','priority':5,'focus':{'kind':'text_change','selected_excerpt':'','independent_excerpt':'','boxes':boxes}})
            for d in diffs:
                priority=0 if d['kind'] in {'date','number','legal_operator','math_operator','currency_unit_candidate'} else d.get('priority',6)
                reason='; '.join(f"{k}: {v.get('reason','unknown')}" for k,v in result['components'].items() if v['state']!='pass')
                prompt=f"Compare the source for this {d['kind'].replace('_',' ')}. Which reading is correct? {reason}"[:1000]
                planned.append({'package':str(package),'page_data':page,'raw':layout,'other':other,'source':source,'quality':result,'focus':d['focus'],'prompt':prompt,'priority':priority,'page_total_discrepancies':len(diffs)})
    # Prioritize numeric/logical discrepancies, then group questions on the same page.
    planned.sort(key=lambda x:(x['priority'],x['source']['id'],x['page_data']['page']))
    output.mkdir(parents=True)
    manifest=[];written=set()
    for entry in planned:
        page=entry['page_data'];source=entry['source'];key=f"{source['id']}-p{page['page']}"
        selected=output/f'{key}-selected.txt';independent=output/f'{key}-independent.json'
        if key not in written:
            selected.write_text(page['text'],encoding='utf-8')
            write_json(independent,{'text':entry['other'],'engine':'pypdf_layout','recognition_model':None,'recognition_languages':[],'source_pdf_sha256':source['sha256'],'page':page['page']})
            written.add(key)
        manifest.append({'title':f"{source['id']} · extraction repair_required",'source_pdf':str(Path(entry['package'])/'original.pdf'),
            'source_pdf_sha256':source['sha256'],'source_url':source.get('url',''),'page':page['page'],
            'selected_text':str(selected),'selected_text_sha256':digest(page['text'].encode()),
            'independent_json':str(independent),'independent_text_sha256':digest(entry['other'].encode()),
            'prompt':entry['prompt'],'focus':entry['focus'],'selected_meta':{'engine':page['method']},
            'page_total_discrepancies':entry['page_total_discrepancies'],'page_queued_discrepancies':entry['page_total_discrepancies'],
            'quality_outcome':'repair_required','l1_package':entry['package']})
    write_rows(output/'review-manifest.jsonl',manifest)
    result={'pages_requiring_repair':page_count,'review_questions':len(manifest),'quality_metric':'repair_required',
        'kinds':dict(Counter(m['focus']['kind'] for m in manifest)),'manifest':str(output/'review-manifest.jsonl'),
        'feedback':str(output/'review-feedback.jsonl'),'feedback_is_page_approval':False}
    write_json(output/'summary.json',result)
    return result


def sync_feedback_jobs(manifest: Path, feedback: Path, output: Path) -> dict:
    """Turn every source-bound user choice into an active, idempotent repair job.

    A selected/independent vote identifies a region to validate, never a blanket
    page approval. Legacy page 'ready' status does not override repair_required.
    """
    from .review_queue import load_item
    from filelock import FileLock
    from datetime import datetime,timezone
    manifest,feedback,output=map(Path,(manifest,feedback,output))
    with FileLock(str(output)+'.lock',timeout=10):
        entries=rows(manifest)
        choices=rows(feedback) if feedback.is_file() else []
        identity=('source_pdf_sha256','page','selected_text_sha256','independent_text_sha256','prompt_sha256','focus_sha256','proposal_sha256')
        latest={tuple(c.get(k) for k in identity):c for c in choices}
        jobs=[];seen=set()
        for i,row in enumerate(entries):
            if not row.get('l1_package'):continue
            candidates=[key for key in latest if key[:4]==tuple(row.get(k) for k in identity[:4])]
            if not candidates:continue
            item=load_item(manifest,i);key=tuple(item[k] for k in identity)
            if key not in latest:continue
            choice=latest[key];seen.add(key)
            package=Path(row['l1_package']);verify_document(package)
            quality=json.loads((package/'quality.json').read_text())
            current=next(p for p in quality['results'] if p['page']==item['page'])
            if current['page_text_sha256']!=item['selected_text_sha256']:raise ValueError('feedback text version changed')
            decision=choice['decision']
            if decision not in {'selected','independent','neither','unclear'}:raise ValueError('invalid feedback decision')
            job_id=digest(json.dumps(key).encode())
            jobs.append({'id':job_id,'kind':'extraction_repair_job','quality_outcome':current['outcome'],
                'repair_state':'queued_for_source_revalidation','workflow_state':'active',
                **{k:item[k] for k in identity},'package':str(package),'decision':decision,
                'action':'validate_selected_region' if decision=='selected' else 'evaluate_independent_region_repair' if decision=='independent' else 'run_alternate_source_reader',
                'focus':item['focus'],'user_note':choice.get('note',''),'feedback_at':choice.get('created_at'),
                'page_approval':False,'automatic_legal_promotion':False})
        if any(key not in seen for key in latest):raise ValueError('feedback does not match this L1 queue')
        temporary=output.with_suffix('.tmp');write_rows(temporary,jobs);temporary.replace(output)
        result={'feedback_entries':len(choices),'unique_jobs':len(jobs),'quality_metric':'repair_required',
            'workflow_state':'active','actions':dict(Counter(j['action'] for j in jobs)),
            'updated_at':datetime.now(timezone.utc).isoformat(),'output':str(output)}
        write_json(output.with_suffix('.summary.json'),result)
        return result


def preview_tables(source_pdf: Path, physical_page: int, source_sha: str) -> list[dict]:
    """Recompute a table repair preview; never rewrite an immutable package."""
    import pdfplumber
    import pypdfium2 as pdfium
    import re
    from .native_tables import find_native_tables,table_shape,glyph_audit
    from .native_consensus import agrees
    if digest(Path(source_pdf).read_bytes())!=source_sha:raise ValueError('table preview source changed')
    result=[]
    with pdfplumber.open(source_pdf) as pdf, pdfium.PdfDocument(source_pdf) as other:
        page=pdf.pages[physical_page-1];other_page=other[physical_page-1];text_page=other_page.get_textpage()
        try:
            for index,found in enumerate(find_native_tables(page)):
                cells=found.extract()
                if len(cells)<2 or not cells or len(cells[0])<2:continue
                caption=None;caption_page=physical_page
                preceding=[line for line in page.extract_text_lines() if line['bottom']<found.bbox[1] and re.search(r'Illustration\s+\d+',line['text'],re.I)]
                if preceding:caption=preceding[-1]['text'].strip().rstrip(':')
                elif index==0 and found.bbox[1]<100 and physical_page>1:
                    previous=pdf.pages[physical_page-2].extract_text() or ''
                    matches=list(re.finditer(r'Illustration\s+\d+\s*:',previous[-700:],re.I))
                    if matches:caption=matches[-1].group().strip().rstrip(':');caption_page=physical_page-1
                identity=f'{source_sha[:16]}:p{physical_page}:table{index}'
                shape=table_shape(found,cells,identity)
                for cell in shape.get('cell_spans',[]):
                    x0,top,x1,bottom=cell['bbox']
                    value=text_page.get_text_bounded(left=x0+.2,bottom=page.height-bottom+.2,right=x1-.2,top=page.height-top-.2)
                    cell['independent_text']=value
                    cell['selected_cell_sha256']=digest((cell['text'] or '').encode())
                    cell['independent_cell_sha256']=digest(value.encode())
                    cell['independent_engine']='pdfium_source_cell'
                    cell['readers_agree']=agrees(cell['text'] or '',value,exact_characters=True)
                    cell['independent_glyph_audit']=glyph_audit(value)
                result.append({'id':identity,'page':physical_page,'source_pdf_sha256':source_sha,
                    'bbox':[float(x) for x in found.bbox],'page_width':page.width,'page_height':page.height,
                    'caption':caption or f'Source table {index+1}', 'caption_source_page':caption_page,**shape,
                    'status':'repair_preview_not_approved','source_unchanged':True})
        finally:text_page.close();other_page.close()
    return result


def record_cell_decision(manifest: Path, output: Path, request: dict) -> dict:
    """Save a human choice for ONE source table/version/cell, never the page."""
    from .review_queue import load_item
    from filelock import FileLock
    from datetime import datetime,timezone
    if not isinstance(request,dict) or type(request.get('index')) is not int:raise ValueError('invalid cell review')
    item=load_item(manifest,request['index'])
    if request.get('source_pdf_sha256')!=item['source_pdf_sha256'] or request.get('page')!=item['page']:raise ValueError('cell source changed')
    tables=[t for t in item['table_previews'] if t['table_version_id']==request.get('table_version_id')]
    if len(tables)!=1:raise ValueError('source illustration/table version changed')
    table=tables[0];cells=[c for c in table['cell_spans'] if c['id']==request.get('cell_id')]
    if len(cells)!=1:raise ValueError('cell does not belong to this source table')
    cell=cells[0]
    if any(request.get(k)!=cell[k] for k in ('selected_cell_sha256','independent_cell_sha256')):raise ValueError('cell readings changed')
    decision=request.get('decision');correction=request.get('correction','');note=request.get('note','')
    if decision not in {'selected','independent','corrected','unclear'}:raise ValueError('invalid cell decision')
    if not isinstance(correction,str) or len(correction)>2000 or not isinstance(note,str) or len(note)>2000:raise ValueError('invalid cell correction')
    if decision=='corrected' and not correction.strip():raise ValueError('enter the corrected value')
    receipt={'kind':'extraction_table_cell_feedback','created_at':datetime.now(timezone.utc).isoformat(),
        'source_pdf_sha256':item['source_pdf_sha256'],'page':item['page'],
        'table_version_id':table['table_version_id'],'table_caption':table['caption'],
        'cell_id':cell['id'],'row':cell['row'],'column':cell['column'],'bbox':cell['bbox'],
        'selected_cell_sha256':cell['selected_cell_sha256'],'independent_cell_sha256':cell['independent_cell_sha256'],
        'selected_text':cell['text'],'independent_text':cell['independent_text'],
        'decision':decision,'correction':correction if decision=='corrected' else None,'note':note,
        'repair_state':'queued_for_source_revalidation','workflow_state':'active','promotes_page':False}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(str(output)+'.lock',timeout=10):
        with output.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(receipt,ensure_ascii=False)+'\n');stream.flush()
    return receipt
