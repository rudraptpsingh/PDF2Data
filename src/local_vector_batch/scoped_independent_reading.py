"""Typed source-crop corrections to an independent reading; raw Vision survives.

Only exact source-reviewed case pins shipped in this runtime may be applied.
A hash proves binding, not legal correctness or an automatic complete-page pass.
"""
from copy import deepcopy
import hashlib,io,json
from pathlib import Path


def sha(v):return hashlib.sha256(v if isinstance(v,bytes)else v.encode()).hexdigest()
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def rows(p):return[json.loads(l)for l in Path(p).read_text().splitlines()if l.strip()]
def rules():return read(Path(__file__).with_name('scoped_independent_reading_rules.json'))
def profile(page):return{k:page['ocr'].get(k)for k in('engine','engine_package_version','recognition_model','recognition_model_sha256')}


def plan(selected,raw,decision,policy=None):
    from .scoped_ocr_word_repair import _word_spans,_box,_iou
    policy=policy or rules()
    keys={'contract_version','rule','review_origin','policy_sha256','source_pdf_sha256','page','raw_independent_sha256','selected_page_sha256','cases'}
    if set(decision)!=keys or decision['contract_version']!=policy['contract_version']or decision['rule']!=policy['rule']or decision['review_origin']!=policy['review_origin']or decision['policy_sha256']!=sha(canonical(policy))or policy['whole_page_gates_unchanged']is not True:raise ValueError('Closed exact source-reviewed correction decision required')
    if type(decision['page'])is not int or decision['page']<1 or selected['page']!=decision['page']or selected.get('method')!='rapidocr'or sha(canonical(selected))!=decision['selected_page_sha256']or sha(selected['text'])!=selected['text_sha256']:raise ValueError('Selected source/page/model representation differs')
    if raw.get('engine')!='vision'or raw.get('page')!=decision['page']or raw.get('source_pdf_sha256')!=decision['source_pdf_sha256']or sha(canonical(raw))!=decision['raw_independent_sha256']or sha(raw['text'])!=raw['text_sha256']or raw['text']!='\n'.join(line['text']for line in raw['lines']):raise ValueError('Original exact Vision reading differs')
    ids=decision['cases']
    if not isinstance(ids,list)or not ids or len(ids)!=len(set(ids))or any(not isinstance(i,str)for i in ids):raise ValueError('Distinct source-reviewed cases required')
    by_id={case['id']:case for case in policy['cases']}
    if any(i not in by_id for i in ids):raise ValueError('Unregistered source-reviewed case')
    revised=deepcopy(raw);changes=[]
    for id in ids:
        case=by_id[id]
        for key in('source_pdf_sha256','page','raw_independent_sha256','selected_page_sha256'):
            if decision[key]!=case[key]:raise ValueError('Source-reviewed identity pin differs')
        if profile(selected)!=case['selected_model_profile']:raise ValueError('Actual selected OCR model/profile differs')
        li,wi=case['independent_line_index'],case['independent_word_index'];pi,pw=case['primary_line_index'],case['primary_word_index']
        if any(type(i)is not int or i<0 for i in(li,wi,pi,pw)):raise ValueError('Exact source word indices required')
        line=raw['lines'][li];word=line['words'][wi];primary=selected['ocr']['lines'][pi]['words'][pw]
        start,end=_word_spans(line)[wi];offset=sum(len(x['text'])+1 for x in raw['lines'][:li])+start
        span=case['original_span'];new=case['reviewed_literal']
        if span!={'char_start':offset,'char_length':end-start,'exact_quote':word['text'],'offset_encoding':'codepoint'}or word['text']!=case['original_literal']or not new or any(c.isspace()for c in new)or raw['text'][offset:offset+len(word['text'])]!=word['text']:raise ValueError('Exact original independent codepoint span differs')
        if primary['text']!=case['primary_literal']or _iou(_box(primary['bbox']),_box(word['bbox']))<policy['minimum_word_box_iou']:raise ValueError('Independent correction is not the same actual primary source word')
        crop=case['crop'];box=word['bbox'];point=[box[0]*selected['pdf_width'],box[1]*selected['pdf_height'],box[2]*selected['pdf_width'],box[3]*selected['pdf_height']]
        if set(crop)!={'bbox','dpi','sha256'}or any(point[i]<crop['bbox'][i]-2 for i in(0,1))or any(point[i]>crop['bbox'][i]+2 for i in(2,3)):raise ValueError('Reviewed crop clips the target independent word')
        changes.append({'case_id':id,'line_index':li,'word_index':wi,'line_start':start,'line_end':end,'original_span':span,'reviewed_literal':new,'crop':crop,'primary_literal':primary['text'],'independent_word_bbox':word['bbox'],'primary_word_bbox':primary['bbox']})
    intervals=sorted((c['original_span']['char_start'],c['original_span']['char_start']+c['original_span']['char_length'])for c in changes)
    if any(b>c for(a,b),(c,d)in zip(intervals,intervals[1:])):raise ValueError('Source-reviewed spans overlap')
    for c in sorted(changes,key=lambda c:c['original_span']['char_start'],reverse=True):
        span=c['original_span'];offset=span['char_start'];end=offset+span['char_length'];li=c['line_index'];wi=c['word_index']
        revised['text']=revised['text'][:offset]+c['reviewed_literal']+revised['text'][end:]
        line=revised['lines'][li];line['text']=line['text'][:c['line_start']]+c['reviewed_literal']+line['text'][c['line_end']:];line['words'][wi]['text']=c['reviewed_literal']
    if revised['text']!='\n'.join(line['text']for line in revised['lines']):raise ValueError('Corrected codepoint/word projections disagree')
    revised['text_sha256']=sha(revised['text']);revised['engine']='source_review_corrected_independent';revised['raw_reader_engine']='vision'
    revised['selected_page_text_sha256']=selected['text_sha256']
    marker={'contract_version':policy['contract_version'],'raw_independent_sha256':sha(canonical(raw)),'decision_sha256':sha(canonical(decision)),'policy_sha256':sha(canonical(policy)),'review_origin':policy['review_origin'],'human_review':False}
    revised['source_bound_independent_corrections']=marker
    proof={'contract_version':policy['contract_version'],'source_pdf_sha256':decision['source_pdf_sha256'],'page':decision['page'],'selected_page_sha256':decision['selected_page_sha256'],'raw_independent_sha256':sha(canonical(raw)),'old_independent_text_sha256':raw['text_sha256'],'new_independent_text_sha256':revised['text_sha256'],'changes':changes,'marker':marker,'raw_reading_preserved':True,'human_review':False,'source_quality_clearance':False,'publication_eligible':False}
    return {'corrected_reading':revised,'proof':proof}


def derive(selected,raw,decision,source_pdf):
    import pdfplumber,rapidocr
    result=plan(selected,raw,decision);source_pdf=Path(source_pdf);before=source_pdf.stat()
    if sha(source_pdf.read_bytes())!=decision['source_pdf_sha256']:raise ValueError('Original source PDF bytes differ')
    model=Path(rapidocr.__file__).parent/'models'/(selected['ocr']['recognition_model']+'.onnx')
    if sha(model.read_bytes())!=selected['ocr']['recognition_model_sha256']:raise ValueError('Actual installed primary model bytes differ')
    with pdfplumber.open(source_pdf)as pdf:
        source=pdf.pages[decision['page']-1]
        if source.width!=selected['pdf_width']or source.height!=selected['pdf_height']:raise ValueError('Source page geometry differs')
        image=source.to_image(resolution=raw['render_dpi']).original;buf=io.BytesIO();image.save(buf,format='PNG')
        if sha(buf.getvalue())!=raw['source_image_sha256']:raise ValueError('Original full-page independent raster differs')
        for change in result['proof']['changes']:
            crop=change['crop'];image=source.crop(tuple(crop['bbox'])).to_image(resolution=crop['dpi']).original;buf=io.BytesIO();image.save(buf,format='PNG')
            if sha(buf.getvalue())!=crop['sha256']:raise ValueError('Actual reviewed original source crop differs')
    if source_pdf.stat()!=before:raise ValueError('Original PDF changed while deriving independent corrections')
    result['proof'].update(fresh_source_crops_verified=True,fresh_fullpage_raster_sha256=raw['source_image_sha256'],actual_primary_model_bytes_verified=True,all8_normal_gates_required=True)
    return result


def consume(provenance,selected,source_pdf,configuration):
    """Called by the targeted OCR decoder before its unchanged wholepage gates."""
    provenance=Path(provenance);names=('independent-correction-decision.json','independent-corrected-reading.json','independent-correction-proof.json')
    present=[(provenance/name).is_file()for name in names]
    if configuration is None:
        if any(present):raise ValueError('Unregistered independent correction evidence')
        return read(provenance/'independent-reading.json')
    expected={'contract_version':rules()['contract_version'],'decision':'independent-correction-decision.json','corrected_reading':'independent-corrected-reading.json','proof':'independent-correction-proof.json'}
    if configuration!=expected or not all(present):raise ValueError('Complete registered independent correction configuration required')
    raw=read(provenance/'independent-reading.json');decision=read(provenance/names[0])
    if sha((provenance/'independent-original-page.png').read_bytes())!=raw['source_image_sha256']:raise ValueError('Saved independent original raster differs')
    derived=derive(selected,raw,decision,source_pdf)
    if read(provenance/names[1])!={'contract_version':rules()['contract_version'],'raw_reader':'vision','corrected_reading':derived['corrected_reading']}or read(provenance/names[2])!=derived['proof']:raise ValueError('Saved independent correction/proof differs from fresh source derivation')
    return derived['corrected_reading']


def configuration():
    return {'contract_version':rules()['contract_version'],'decision':'independent-correction-decision.json','corrected_reading':'independent-corrected-reading.json','proof':'independent-correction-proof.json'}
