"""Apply one exact source-crop-supported OCR word without hiding other holds.

Original provider/independent/model readings are immutable provenance. Only an
explicit whole-word codepoint interval may change. The closed rules cover
Devanagari marks and duplicated mixed-script single digits. Model completion
is not page approval.
"""
from copy import deepcopy
import hashlib,io,json,math,re,shutil,unicodedata
from pathlib import Path


def sha(x):return hashlib.sha256(x if isinstance(x,bytes)else x.encode()).hexdigest()
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def rows(p):return[json.loads(x)for x in Path(p).read_text().splitlines()if x.strip()]
def lines(p,x):Path(p).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'for r in x))
def rules():return read(Path(__file__).with_name('scoped_ocr_word_rules.json'))
def base(x):return ''.join(c for c in x if not unicodedata.category(c).startswith('M'))
def rule_for(old,new):
    if old!=new and base(old)==base(new)and any('\u0900'<=c<='\u097f'for c in old+new):return 'independent_crop_supported_devanagari_mark_only'
    # A single printed digit sometimes receives both a Latin and Devanagari
    # recognition. This is not a general repeated-number or script rewrite.
    if len(old)==2 and len(new)==1 and old[0]!=old[1]and all(c.isdecimal()for c in old+new)and new in old and unicodedata.decimal(old[0])==unicodedata.decimal(old[1])==unicodedata.decimal(new)and any('0'<=c<='9'for c in old)and any('\u0966'<=c<='\u096f'for c in old):return 'independent_crop_supported_mixed_script_duplicate_digit'
    return None
def _box(b):
    if not isinstance(b,list)or len(b)!=4 or any(type(x)not in(int,float)or not math.isfinite(x)or not 0<=x<=1 for x in b)or b[0]>=b[2]or b[1]>=b[3]:raise ValueError('Exact normalized OCR word box required')
    return b
def _iou(a,b):
    area=lambda c:(c[2]-c[0])*(c[3]-c[1]);overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return overlap/(area(a)+area(b)-overlap)
def _word_spans(line):
    cursor=0;spans=[]
    for word in line['words']:
        literal=word['text'];start=line['text'].find(literal,cursor)
        if not literal or start<cursor or line['text'][cursor:start].strip():raise ValueError('OCR words do not exactly partition their line')
        spans.append((start,start+len(literal)));cursor=start+len(literal)
    if line['text'][cursor:].strip():raise ValueError('Unmapped OCR line suffix')
    return spans


def plan(page,independent,decision,model_readings,policy=None):
    policy=policy or rules()
    if policy.get('whole_page_gates_unchanged')is not True or policy.get('publication_eligible')is not False:raise ValueError('Rules cannot weaken whole-page quality/publication gates')
    required={'contract_version','rule','review_origin','source_pdf_sha256','page','original_page_text_sha256','independent_canonical_sha256',
        'primary_line_index','primary_word_index','independent_line_index','independent_word_index','original_literal','proposed_literal','crop','model_readings_sha256','model_case_id'}
    if set(decision)!=required or decision['contract_version']!=policy['contract_version']or decision['rule']not in policy.get('allowed_rules',[policy['allowed_rule']])or decision['review_origin']!=policy['allowed_review_origin']:raise ValueError('Closed automatic scoped word decision required')
    if type(decision['page'])is not int or decision['page']<1 or sha(canonical(model_readings))!=decision['model_readings_sha256']:raise ValueError('Exact physical page and full model reading identity required')
    runtime=model_readings.get('runtime')or{}
    if {'model':runtime.get('model'),'revision':runtime.get('revision')}not in policy['permitted_model_profiles']:raise ValueError('Actual pinned permitted model profile required')
    raw=(page.get('ocr')or{}).get('lines')or[]
    if page.get('method')!='rapidocr'or page['text']!='\n'.join(l['text']for l in raw)or sha(page['text'])!=page['text_sha256']:raise ValueError('Original exact provider lines/page required')
    if page['page']!=decision['page']or page['text_sha256']!=decision['original_page_text_sha256']or sha(canonical(independent))!=decision['independent_canonical_sha256']:raise ValueError('Original OCR/independent identity differs')
    if independent.get('engine')!='vision'or independent.get('source_pdf_sha256')!=decision['source_pdf_sha256']or independent.get('page')!=page['page']or independent.get('selected_page_text_sha256')!=page['text_sha256']or sha(independent['text'])!=independent['text_sha256']:raise ValueError('Actual independent source reading required')
    indices=[decision[k]for k in('primary_line_index','primary_word_index','independent_line_index','independent_word_index')]
    if any(type(x)is not int or x<0 for x in indices):raise ValueError('Exact nonnegative OCR indices required')
    li,wi,vi,vw=indices;line=raw[li];word=line['words'][wi];other=independent['lines'][vi]['words'][vw]
    old,new=word['text'],decision['proposed_literal']
    if old!=decision['original_literal']or old==new or not new or max(len(old),len(new))>policy['maximum_word_codepoints']or any(c.isspace()for c in old+new):raise ValueError('Bounded changed whole word required')
    if rule_for(old,new)!=decision['rule']:raise ValueError('Difference does not meet the selected closed source-word rule')
    alternatives=[w for row in independent['lines']for w in row.get('words',[])if w.get('bbox')and w.get('text')==new and _iou(_box(word['bbox']),_box(w['bbox']))>=policy['minimum_word_box_iou']]
    if new!=other['text']or len(alternatives)!=1 or _iou(_box(word['bbox']),_box(other['bbox']))<policy['minimum_word_box_iou']:raise ValueError('Unique exact independent word/source geometry required')
    matched=[r for r in model_readings['results']if r['id']==decision['model_case_id']]
    if len(matched)!=1:raise ValueError('Exact model source-case receipt required')
    model=matched[0];crop=decision['crop']
    if set(crop)!={'bbox','dpi','sha256'}or model.get('status')!='processed_candidate'or model.get('review_origin')!='local_vision_model'or model.get('source_pdf_sha256')!=decision['source_pdf_sha256']or model.get('page')!=page['page']or model.get('bbox')!=crop['bbox']or model.get('dpi')!=crop['dpi']or model.get('image_sha256')!=crop['sha256']or model.get('text','').count(new)!=1:raise ValueError('Model target does not match exact independent crop literal')
    for b in(word['bbox'],other['bbox']):
        points=[b[0]*page['pdf_width'],b[1]*page['pdf_height'],b[2]*page['pdf_width'],b[3]*page['pdf_height']];tol=policy['crop_containment_tolerance_points']
        if any(points[i]<crop['bbox'][i]-tol for i in(0,1))or any(points[i]>crop['bbox'][i]+tol for i in(2,3)):raise ValueError('Target word is clipped/outside reviewed crop')
    start,end=_word_spans(line)[wi];page_start=sum(len(l['text'])+1 for l in raw[:li])+start;page_end=page_start+len(old)
    if page['text'][page_start:page_end]!=old:raise ValueError('Exact page codepoint insertion span differs')
    revised=deepcopy(page);revised['ocr']['lines'][li]['text']=line['text'][:start]+new+line['text'][end:];revised['ocr']['lines'][li]['words'][wi]['text']=new
    revised['text']=page['text'][:page_start]+new+page['text'][page_end:];revised['text_sha256']=sha(revised['text']);revised['chars']=len(revised['text']);revised['ocr']['text']=revised['text']
    from .text_script import classify_text_script
    revised.update(classify_text_script(revised['text']))
    marker={'kind':'scoped_word_source_repair','rule':decision['rule'],'decision_sha256':sha(canonical(decision)),'raw_provider_page_sha256':sha(canonical(page)),'rules_sha256':sha(canonical(policy))}
    revised['ocr']['source_bound_scoped_word']=marker
    proof={'contract_version':policy['contract_version'],'page':page['page'],'source_pdf_sha256':decision['source_pdf_sha256'],'old_page_text_sha256':page['text_sha256'],'new_page_text_sha256':revised['text_sha256'],
        'original_span':{'char_start':page_start,'char_length':len(old),'exact_quote':old,'offset_encoding':'codepoint'},'new_span':{'char_start':page_start,'char_length':len(new),'exact_quote':new,'offset_encoding':'codepoint'},
        'raw_line_index':li,'raw_word_index':wi,'independent_line_index':vi,'independent_word_index':vw,'primary_word_bbox':word['bbox'],'independent_word_bbox':other['bbox'],
        'prefix_unchanged_sha256':sha(page['text'][:page_start]),'suffix_unchanged_sha256':sha(page['text'][page_end:]),'base_letters_digits_operators_unchanged':base(old)==base(new),'numeric_value_unchanged':decision['rule']=='independent_crop_supported_mixed_script_duplicate_digit','marker':marker,
        'automatic_action':'applied_scoped_word_candidate','human_review':False,'whole_page_quality_clearance':False,'publication_eligible':False}
    return {'proposed_page':revised,'proof':proof}


def derive(page,independent,decision,model_readings,source_pdf,policy=None):
    import pdfplumber
    policy=policy or rules();source_pdf=Path(source_pdf);state=source_pdf.stat();pdfsha=sha(source_pdf.read_bytes())
    if pdfsha!=decision['source_pdf_sha256']:raise ValueError('Original PDF bytes differ')
    result=plan(page,independent,decision,model_readings,policy)
    with pdfplumber.open(source_pdf)as pdf:
        source=pdf.pages[page['page']-1];crop=decision['crop'];image=source.crop(tuple(crop['bbox'])).to_image(resolution=crop['dpi']).original
        buf=io.BytesIO();image.save(buf,format='PNG')
        if sha(buf.getvalue())!=crop['sha256']:raise ValueError('Fresh original word crop differs')
        image=source.to_image(resolution=independent['render_dpi']).original;buf=io.BytesIO();image.save(buf,format='PNG')
        if sha(buf.getvalue())!=independent['source_image_sha256']:raise ValueError('Fresh full-page independent raster differs')
    if source_pdf.stat()!=state:raise ValueError('Original PDF changed during word derivation')
    result['proof'].update(source_crop_sha256=crop['sha256'],source_full_page_render_sha256=independent['source_image_sha256'],independent_canonical_sha256=sha(canonical(independent)),
        model_record_sha256=sha(canonical(model_readings)),all8_normal_gates_required=True)
    return result


def prepare(experiment,source_pdf,decision_file,model_file,output):
    """A NEW extraction version: scoped data applied; page clearance iff8pass."""
    from .evidence_package import verify_package,create_package
    from .structure_candidates import page_candidates
    from .pdf_ingest import FACT
    from .quality_gate import assess_package
    experiment,source_pdf,output=map(Path,(experiment,source_pdf,output));decision=read(decision_file);model=read(model_file)
    if output.exists()or output.resolve().is_relative_to(experiment.resolve()):raise ValueError('New independent processed-word output required')
    verify_package(experiment/'extraction',source_pdf=source_pdf)
    original=rows(experiment/'extraction/pages.jsonl');number=decision['page'];page=next(p for p in original if p['page']==number);independent=read(experiment/'independent-reading.json')
    result=derive(page,independent,decision,model,source_pdf);revised=result['proposed_page']
    quality_before=assess_package(experiment/'extraction',source_pdf,pages=[number],independent_by_page={number:independent['text']},independent_metadata_by_page={number:{**independent,'version':'macOS_AppleVision_system_API'}},derive_ocr_source_raster=True)
    output.mkdir(parents=True);shutil.copytree(experiment/'extraction',output/'extraction')
    for name in('OCR-selection.json','independent-original-page.png'):shutil.copyfile(experiment/name,output/name)
    save(output/'word-original-page.json',page);save(output/'word-original-independent.json',independent);save(output/'word-decision.json',decision);save(output/'word-model-readings.json',model)
    independent=deepcopy(independent);independent['selected_page_text_sha256']=revised['text_sha256'];save(output/'independent-reading.json',independent)
    selected=[revised if p['page']==number else p for p in original];lines(output/'extraction/pages.jsonl',selected)
    embeddings=rows(output/'extraction/embedding-input.jsonl')
    for r in embeddings:
        if r['metadata']['page']==number:r['text']=revised['text'];r['metadata']['page_text_sha256']=revised['text_sha256']
    lines(output/'extraction/embedding-input.jsonl',embeddings)
    facts=[r for r in rows(output/'extraction/fact-candidates.jsonl')if r['page']!=number]
    for match in FACT.finditer(revised['text']):
        value=match.group().strip();kind='currency'if re.match(r'₹|INR|Rs\.?',value,re.I)else'percentage'if'%'in value else'capacity'if re.search(r'\b[mk]w',value,re.I)else'duration'if re.search(r'\b(?:days?|months?|years?)\b',value,re.I)else'date'
        facts.append({'page':number,'page_id':revised['id'],'page_text_sha256':revised['text_sha256'],'char_start':match.start(),'char_length':match.end()-match.start(),'kind':kind,'value_text':value,'context':re.sub(r'\s+',' ',revised['text'][max(0,match.start()-90):match.end()+90]).strip(),'status':'unverified_candidate'})
    facts.sort(key=lambda r:(r['page'],r['char_start']));lines(output/'extraction/fact-candidates.jsonl',facts)
    structures=[r for r in rows(output/'extraction/structure-candidates.jsonl')if not any(s.get('page')==number for s in r.get('evidence',[]))];structures.extend(page_candidates(revised));lines(output/'extraction/structure-candidates.jsonl',structures)
    report=read(output/'extraction/report.json');report.update(text_chars=sum(p['chars']for p in selected),numeric_candidates=len(facts),structure_candidates=len(structures),requires_source_review=True)
    report.setdefault('issues',[]).append({'page':number,'kind':'automatic_scoped_word_mark_repair','original_page_text_sha256':page['text_sha256'],'new_page_text_sha256':revised['text_sha256'],'raw_provider_evidence_preserved':True,'human_review':False})
    save(output/'extraction/report.json',report);create_package(output/'extraction');verify_package(output/'extraction',source_pdf=source_pdf)
    quality=assess_package(output/'extraction',source_pdf,pages=[number],independent_by_page={number:independent['text']},independent_metadata_by_page={number:{**independent,'version':'macOS_AppleVision_system_API'}},derive_ocr_source_raster=True)
    q=quality['results'][0];passed=q['outcome']=='auto_accepted'and len(q['components'])==8 and all(c['state']=='pass'for c in q['components'].values())
    result['proof'].update(original_quality=quality_before['results'][0],processed_quality=q,all8_normal_gates_rederived=True,whole_page_quality_clearance=False)
    save(output/'word-proof.json',result['proof']);save(output/'quality.json',quality)
    old=read(experiment/'result.json');old.update(status='processed',new_selected_chars=len(revised['text']),new_page_text_sha256=revised['text_sha256'],quality_outcome=q['outcome'],quality_components={k:v['state']for k,v in q['components'].items()},
        representation_repair_kind='scoped_word_mark_repair',automatic_action='scoped_word_applied',OCR_model_calls_this_repair=0,registered_app_source_quality_pass=False)
    save(output/'result.json',old)
    receipt={'contract_version':'pdf2data.scoped-ocr-word-version.v1','source_pdf_path':str(source_pdf.resolve()),'source_pdf_sha256':decision['source_pdf_sha256'],'parent_experiment':str(experiment.resolve()),
        'parent_artifacts_sha256':{name:sha((experiment/name).read_bytes())for name in('extraction/evidence-package.json','extraction/pages.jsonl','independent-reading.json')},
        'page':number,'old_page_text_sha256':page['text_sha256'],'new_page_text_sha256':revised['text_sha256'],'scoped_word_applied':True,'all8_normal_gates_passed':passed,'source_quality_clearance':False,
        'registered_decoder_required_before_counting':True,'automatic_action':'scoped_word_applied_page_pass_pending_registered_decoder'if passed else'scoped_word_applied_other_page_gates_unresolved',
        'artifacts_sha256':{str(p.relative_to(output)):sha(p.read_bytes())for p in sorted(output.rglob('*'))if p.is_file()},'OCR_model_calls':0,'VLM_model_calls':0,'human_votes':0,'source_packages_modified':False,'publication_eligible':False}
    save(output/'scoped-word-version.json',receipt);return receipt


def verify_candidate(output):
    """Re-derive exact mutation and normal8gates; a held result stays held."""
    from .evidence_package import verify_package
    from .quality_gate import assess_package
    output=Path(output);receipt=read(output/'scoped-word-version.json');source=Path(receipt['source_pdf_path'])
    for name,digest in receipt['artifacts_sha256'].items():
        if Path(name).is_absolute()or'..'in Path(name).parts or(output/name).resolve().is_relative_to(output.resolve())is False or sha((output/name).read_bytes())!=digest:raise ValueError('Processed word artifact graph differs')
    raw=read(output/'word-original-page.json');independent=read(output/'word-original-independent.json');decision=read(output/'word-decision.json');model=read(output/'word-model-readings.json')
    if sha((output/'independent-original-page.png').read_bytes())!=independent['source_image_sha256']:raise ValueError('Saved independent source raster differs from original rendering')
    parent=Path(receipt['parent_experiment'])
    for name,digest in receipt['parent_artifacts_sha256'].items():
        if sha((parent/name).read_bytes())!=digest:raise ValueError('Original provider package binding changed')
    verify_package(parent/'extraction',source_pdf=source)
    parent_pages=rows(parent/'extraction/pages.jsonl')
    if raw!=next(p for p in parent_pages if p['page']==raw['page'])or independent!=read(parent/'independent-reading.json'):raise ValueError('Saved raw OCR/independent evidence differs from original provider')
    selected=rows(output/'extraction/pages.jsonl')
    if {p['page']for p in selected}!={p['page']for p in parent_pages}or any(a!=b for a,b in zip(parent_pages,selected)if a['page']!=raw['page']):raise ValueError('Scoped word repair changed another physical page')
    derived=derive(raw,independent,decision,model,source);actual=next(p for p in rows(output/'extraction/pages.jsonl')if p['page']==raw['page'])
    if actual!=derived['proposed_page']:raise ValueError('Processed word differs from exact source-bound derivation')
    verify_package(output/'extraction',source_pdf=source);current=read(output/'independent-reading.json')
    if current!={**independent,'selected_page_text_sha256':actual['text_sha256']}:raise ValueError('Independent raw reading changed')
    quality=assess_package(output/'extraction',source,pages=[raw['page']],independent_by_page={raw['page']:current['text']},independent_metadata_by_page={raw['page']:{**current,'version':'macOS_AppleVision_system_API'}},derive_ocr_source_raster=True)
    if quality!=read(output/'quality.json'):raise ValueError('Fresh normal whole-page gates differ')
    return {'status':'verified_processed_word_candidate','page':raw['page'],'new_page_text_sha256':actual['text_sha256'],'all8_normal_gates_rederived':True,
        'all8_normal_gates_passed':quality['results'][0]['outcome']=='auto_accepted'and len(quality['results'][0]['components'])==8 and all(c['state']=='pass'for c in quality['results'][0]['components'].values()),'source_quality_clearance':False,'human_votes':0,'publication_eligible':False}
