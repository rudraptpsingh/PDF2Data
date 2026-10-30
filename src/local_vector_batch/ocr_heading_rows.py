"""Order-only repair of adjacent OCR heading-label fragments on one baseline.

Raw provider lines remain separate. Independent merged-line agreement plus
fresh original-page raster and every normal gate are required. No OCR/model
inference, word substitutions, broad column sorting or legal review occurs.
"""
from collections import Counter
from copy import deepcopy
import hashlib,io,json,math,re
from pathlib import Path


def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def rules():return json.loads(Path(__file__).with_name('ocr_heading_row_rules.json').read_text())
def _literal(value):return ' '.join(value.split())
def _box(value):
    if (not isinstance(value,list)or len(value)!=4 or any(type(v)not in(int,float)or not math.isfinite(v)or not 0<=v<=1 for v in value)or
        value[0]>=value[2]or value[1]>=value[3]):raise ValueError('Invalid heading OCR line box')
    return value


def plan(page,independent,policy=None):
    """Nominate a line permutation only; this function grants no page pass."""
    from .quality_gate import WORD,NUMBER,_normalized,_operators,load_policy
    policy=policy or rules();original=(page.get('ocr')or{}).get('lines')or[]
    if page.get('method')!='rapidocr'or not original or page['text']!='\n'.join(l['text']for l in original):
        raise ValueError('Original selected text must preserve every raw RapidOCR line')
    if page['text_sha256']!=sha(page['text']):raise ValueError('Original OCR page hash differs')
    for line in original:_box(line.get('bbox'))
    other=independent.get('lines')or[]
    for line in other:_box(line.get('bbox'))
    order=list(range(len(original)));changes=[];index=0
    while index+1<len(original):
        right,left=original[index],original[index+1];rb,lb=right['bbox'],left['bbox'];label=left['text'];heading=right['text']
        if not (len(label)<=policy['maximum_left_label_characters']and re.fullmatch(policy['left_label_pattern'],label)and
            policy['minimum_heading_characters']<=len(heading)<=policy['maximum_heading_characters']and not any(c.isdigit()for c in heading)):
            index+=1;continue
        overlap=min(lb[3],rb[3])-max(lb[1],rb[1]);height=min(lb[3]-lb[1],rb[3]-rb[1]);gap=rb[0]-lb[2]
        if not (overlap>=policy['minimum_vertical_overlap_fraction']*height and
            abs((lb[1]+lb[3]-rb[1]-rb[3])/2)<=policy['maximum_center_y_difference_in_line_heights']*height and
            policy['minimum_horizontal_gap_fraction']<=gap<=policy['maximum_horizontal_gap_fraction']and
            lb[2]-lb[0]<=policy['maximum_label_width_fraction']):
            index+=1;continue
        merged=_literal(label+' '+heading);union=[min(lb[0],rb[0]),min(lb[1],rb[1]),max(lb[2],rb[2]),max(lb[3],rb[3])];tol=policy['independent_box_containment_tolerance']
        matched=[i for i,line in enumerate(other)if _literal(line['text'])==merged and
            all(line['bbox'][i]-tol<=union[i]for i in(0,1))and all(line['bbox'][i]+tol>=union[i]for i in(2,3))and
            min(line['bbox'][3],union[3])-max(line['bbox'][1],union[1])>=policy['minimum_vertical_overlap_fraction']*height]
        if len(matched)!=1:raise ValueError('Heading row lacks unique exact independent merged-line agreement')
        order[index:index+2]=[index+1,index]
        changes.append({'raw_left_line_index':index+1,'raw_right_line_index':index,'independent_line_index':matched[0],
            'raw_label':label,'raw_heading':heading,'merged_independent_reading':other[matched[0]]['text'],'bbox_normalized':union})
        index+=2
    if not changes:raise ValueError('No source-bound reversed heading-label fragments')
    revised=deepcopy(page);revised['ocr']['lines']=[deepcopy(original[i])for i in order];revised['text']='\n'.join(line['text']for line in revised['ocr']['lines']);revised['text_sha256']=sha(revised['text']);revised['chars']=len(revised['text'])
    old,new=_normalized(page['text']),_normalized(revised['text']);qp=load_policy()
    if (Counter(WORD.findall(old))!=Counter(WORD.findall(new))or NUMBER.findall(old)!=NUMBER.findall(new)or
        _operators(page['text'],qp)!=_operators(revised['text'],qp)):
        raise ValueError('Heading order changed word/number/operator inventory or ordered numeric/operator evidence')
    if sorted(order)!=list(range(len(original))):raise ValueError('Heading order lost or duplicated an original line')
    marker={'kind':'same_baseline_heading_label_order','original_lines_sha256':sha(canonical(original)),
        'rules_sha256':sha(canonical(policy))}
    revised['ocr']['source_bound_heading_row_order']=marker
    proof={'contract_version':policy['contract_version'],'page':page['page'],'original_page_text_sha256':page['text_sha256'],
        'new_page_text_sha256':revised['text_sha256'],'original_lines_sha256':marker['original_lines_sha256'],
        'rules_sha256':marker['rules_sha256'],'original_line_permutation':order,'changes':changes,
        'literal_line_multiset_unchanged':True,'ordered_numbers_and_operators_unchanged':True,
        'source_quality_clearance':False,'human_review':False,'publication_eligible':False}
    return {'proposed_page':revised,'proof':proof}


def derive(page,independent,source_pdf,policy=None):
    """Re-render source and assess original/reordered pages with normal gates."""
    import pdfplumber,pypdfium2 as pdfium
    from .quality_gate import assess_page,ocr_source_raster_evidence
    policy=policy or rules();source_pdf=Path(source_pdf);pdfsha=sha(source_pdf.read_bytes());number=page['page']
    if (independent.get('source_pdf_sha256')!=pdfsha or independent.get('page')!=number or
        independent.get('text_sha256')!=sha(independent['text'])or
        independent.get('selected_page_text_sha256')!=page['text_sha256']):raise ValueError('Independent heading reader source binding differs')
    result=plan(page,independent,policy)
    with pdfplumber.open(source_pdf)as pdf,pdfium.PdfDocument(source_pdf)as raster_pdf:
        source=pdf.pages[number-1];raster_page=raster_pdf[number-1];image=source.to_image(resolution=independent['render_dpi']).original
        buf=io.BytesIO();image.save(buf,format='PNG')
        if sha(buf.getvalue())!=independent.get('source_image_sha256'):raise ValueError('Original heading source raster differs')
        def assess(candidate):
            geometry=ocr_source_raster_evidence(candidate,raster_page,pdfsha,table_count=0)
            return assess_page(candidate,source_pdf_sha256=pdfsha,package_pdf_sha256=pdfsha,
                independent_text=independent['text'],independent_metadata=independent,source_geometry=geometry,
                table_count=0,source_pdf_path=source_pdf)
        before=assess(page)
        if {k for k,v in before['components'].items()if v['state']!='pass'}!={'words_and_order'}:
            raise ValueError('Heading route requires all seven other original page gates to pass')
        word=before['components']['words_and_order']
        if word.get('word_recall')!=1 or word.get('word_precision')!=1:raise ValueError('Original heading disagreement is not word-inventory exact')
        after=assess(result['proposed_page'])
        if after['outcome']!='auto_accepted'or len(after['components'])!=8 or any(v['state']!='pass'for v in after['components'].values()):
            raise ValueError('Reordered heading page still fails normal whole-page gates')
        for change in result['proof']['changes']:
            b=change['bbox_normalized'];box=[b[0]*source.width,b[1]*source.height,b[2]*source.width,b[3]*source.height]
            crop=source.crop(tuple(box)).to_image(resolution=policy['source_crop_dpi']).original
            raw=io.BytesIO();crop.save(raw,format='PNG');change.update(source_bbox_points=box,
                source_crop_dpi=policy['source_crop_dpi'],source_crop_sha256=sha(raw.getvalue()))
        raster_page.close()
    result['proof'].update(source_pdf_sha256=pdfsha,independent_reading_sha256=sha(canonical(independent)),
        source_original_page_render_sha256=independent['source_image_sha256'],
        original_quality=before,reordered_quality=after,all8_normal_gates_rederived=True)
    result['quality']=after
    return result


def prepare(experiment,source_pdf,output):
    """Write a NEW candidate extraction; never change the original OCR output."""
    import shutil
    from .evidence_package import verify_package,create_package
    from .pdf_ingest import FACT
    from .structure_candidates import page_candidates
    experiment,source_pdf,output=map(Path,(experiment,source_pdf,output))
    if output.exists()or output.resolve().is_relative_to(experiment.resolve()):raise ValueError('New independent heading output required')
    verify_package(experiment/'extraction',source_pdf=source_pdf)
    load=lambda p:json.loads(p.read_text())
    save=lambda p,v:p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
    rows=lambda p:[json.loads(l)for l in p.read_text().splitlines()if l.strip()]
    write=lambda p,v:p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'for r in v))
    old_result=load(experiment/'result.json');number=old_result['page'];pages=rows(experiment/'extraction/pages.jsonl');page=next(p for p in pages if p['page']==number)
    if any(t['page']==number for t in load(experiment/'extraction/tables.json')):raise ValueError('Heading route cannot reorder recorded table cells')
    independent=load(experiment/'independent-reading.json');result=derive(page,independent,source_pdf);revised=result['proposed_page']
    output.mkdir(parents=True);shutil.copytree(experiment/'extraction',output/'extraction')
    for name in('OCR-selection.json','independent-original-page.png'):shutil.copyfile(experiment/name,output/name)
    save(output/'heading-original-page.json',page);save(output/'heading-original-independent.json',independent);save(output/'heading-row-proof.json',result['proof'])
    independent=deepcopy(independent);independent['selected_page_text_sha256']=revised['text_sha256'];save(output/'independent-reading.json',independent)
    write(output/'extraction/pages.jsonl',[revised if p['page']==number else p for p in pages])
    inputs=rows(output/'extraction/embedding-input.jsonl')
    for row in inputs:
        if row['metadata']['page']==number:row.update(text=revised['text']);row['metadata']['page_text_sha256']=revised['text_sha256']
    write(output/'extraction/embedding-input.jsonl',inputs)
    facts=[r for r in rows(output/'extraction/fact-candidates.jsonl')if r['page']!=number]
    for match in FACT.finditer(revised['text']):
        value=match.group().strip();kind=('currency'if re.match(r'₹|INR|Rs\.?',value,re.I)else'percentage'if'%'in value else'capacity'if re.search(r'\b[mk]w',value,re.I)else'duration'if re.search(r'\b(?:days?|months?|years?)\b',value,re.I)else'date')
        facts.append({'page':number,'page_id':revised['id'],'page_text_sha256':revised['text_sha256'],'char_start':match.start(),'char_length':match.end()-match.start(),
            'kind':kind,'value_text':value,'context':re.sub(r'\s+',' ',revised['text'][max(0,match.start()-90):match.end()+90]).strip(),'status':'unverified_candidate'})
    facts.sort(key=lambda r:(r['page'],r['char_start']));write(output/'extraction/fact-candidates.jsonl',facts)
    structures=[r for r in rows(output/'extraction/structure-candidates.jsonl')if not any(s.get('page')==number for s in r.get('evidence',[]))]
    structures.extend(page_candidates(revised));write(output/'extraction/structure-candidates.jsonl',structures)
    report=load(output/'extraction/report.json');report.update(text_chars=sum(p['chars']for p in[revised if p['page']==number else p for p in pages]),numeric_candidates=len(facts),structure_candidates=len(structures),requires_source_review=True)
    report.setdefault('issues',[]).append({'page':number,'kind':'source_bound_heading_row_order','original_page_text_sha256':page['text_sha256'],'new_page_text_sha256':revised['text_sha256'],'raw_provider_lines_preserved':True})
    save(output/'extraction/report.json',report);create_package(output/'extraction');verify_package(output/'extraction',source_pdf=source_pdf)
    from .quality_gate import assess_package
    quality=assess_package(output/'extraction',source_pdf,pages=[number],independent_by_page={number:independent['text']},
        independent_metadata_by_page={number:{**independent,'version':'macOS_AppleVision_system_API'}},derive_ocr_source_raster=True)
    if quality['results'][0]['outcome']!='auto_accepted':raise ValueError('Prepared heading extraction does not pass every normal gate')
    save(output/'quality.json',quality)
    final=deepcopy(old_result);final.update(status='processed',quality_outcome='auto_accepted',new_selected_chars=len(revised['text']),new_page_text_sha256=revised['text_sha256'],
        representation_repair_kind='same_baseline_heading_label_order',OCR_model_calls_this_repair=0,registered_app_source_quality_pass=False)
    save(output/'result.json',final)
    return {'status':'source_revalidated_candidate','page':number,'new_page_text_sha256':revised['text_sha256'],'all8_normal_gates_passed':True,
        'new_experiment':str(output.resolve()),'OCR_model_calls':0,'registered_decoder_required':True,'publication_eligible':False}
