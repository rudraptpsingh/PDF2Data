"""Typed model-assisted image observations, separate from source-quality proof.

The original packet/reader witnesses remain immutable. An annotation can record
an OCR correction or a field association without certifying raster coverage,
whole-page quality, table semantics, human review or client/legal facts.
"""
from pathlib import Path
import argparse,copy,hashlib,json,math,re,tempfile

SCHEMA='pdf2data.model-image-region-review.v1'
ORIGIN='model_assisted_visual_current_thread'
def sha(data):return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def load(path):return json.loads(Path(path).read_text())
def exact_false(value):return value is False

def validate_json_observation(value):
    if isinstance(value,str):value.encode('utf-8')
    elif isinstance(value,list):
        for item in value:validate_json_observation(item)
    elif isinstance(value,dict):
        for key,item in value.items():
            if not isinstance(key,str):raise ValueError('Observation keys must be strings')
            key.encode('utf-8');validate_json_observation(item)
    elif isinstance(value,float)and not math.isfinite(value):raise ValueError('Nonfinite observation value')
    elif value is not None and not isinstance(value,(bool,int,float)):raise ValueError('Observation contains non-JSON value')


def validate_model_review(representation,completeness,review,bindings):
    validate_json_observation(review)
    if review.get('contract_version')!='pdf2data.image-region-model-visual-review.v1'or review.get('reviewer_kind')!='model'or review.get('review_origin')!=ORIGIN:raise ValueError('Explicit model review origin required; no human inference')
    if review.get('human_approvals')!=0 or review.get('source_quality_clearances')!=0 or any(not exact_false(review.get(key))for key in ('selected_text_modified','source_packages_modified','legal_correctness_certified','publication_eligible'))or review.get('all_old_readings_preserved')is not True:raise ValueError('Model observation cannot approve source quality, legal status or human review')
    for key in ('source_packet_manifest_sha256','representation_sha256','completeness_sha256'):
        if review.get(key)!=bindings.get(key):raise ValueError('Model review source packet binding differs')
    regions={r['id']:r for r in representation['regions']};whole=review.get('whole_images_reviewed',[])
    if len(whole)!=len(regions)or {r.get('region_id')for r in whole}!=set(regions):raise ValueError('Every complete source image must have one observation record')
    for entry in whole:
        source=regions[entry['region_id']]
        if entry.get('entire_image_visually_inspected')is not True or entry.get('review_origin')!=ORIGIN or entry.get('human_approval')is not False:raise ValueError('Whole-image observation origin differs')
        for a,b in [('source_pdf_sha256','source_pdf_sha256'),('page','page'),('page_text_sha256','page_text_sha256'),('parent_package_sha256','parent_package_sha256'),('crop_bbox_PDF_points','source_crop_bbox_PDF_points'),('crop_render_sha256','source_crop_render_sha256'),('render_dpi','render_dpi')]:
            if entry.get(a)!=source.get(b):raise ValueError('Whole-image PDF/page/crop identity changed')
    key=lambda r:(r.get('region_id'),r.get('reader'),r.get('line_index'))
    unresolved={key(r):r for r in completeness['unresolved_readings']};decisions=review.get('reading_decisions',[])
    if len(decisions)!=len(unresolved)or{key(r)for r in decisions}!=set(unresolved):raise ValueError('Missing, duplicate or extra reader disagreement decisions')
    resolved=[];remaining=[]
    for decision in decisions:
        if type(decision.get('line_index'))is not int or decision['line_index']<0:raise ValueError('Physical reader line index must be an integer')
        original=unresolved[key(decision)];region=regions[decision['region_id']]
        for field in ('region_id','reader','line_index','literal','source_bbox_PDF_points'):
            if decision.get(field)!=original.get(field):raise ValueError('Review does not bind the original literal/geometry')
        if decision.get('source_crop_render_sha256')!=region['source_crop_render_sha256']or decision.get('source_crop_bbox_PDF_points')!=region['source_crop_bbox_PDF_points']:raise ValueError('Decision original render identity changed')
        if decision.get('review_origin')!=ORIGIN or decision.get('human_approval')is not False or decision.get('promotes_page')is not False:raise ValueError('Decision cannot self-promote model origin')
        reason=decision.get('reason');literal=decision.get('reviewed_literal')
        if not isinstance(reason,str)or not reason.strip()or len(reason)>2000:raise ValueError('Bounded observation reason required')
        if decision.get('status')=='model_assisted_resolved':
            if not isinstance(literal,str)or not literal.strip()or len(literal)>5000:raise ValueError('Resolved observation needs a bounded literal')
            literal.encode('utf-8');resolved.append(copy.deepcopy(decision))
        elif decision.get('status')=='unresolved':
            if literal is not None:raise ValueError('Unclear source glyph cannot acquire a fabricated exact literal')
            remaining.append(copy.deepcopy(decision))
        else:raise ValueError('Unknown model-review decision state')
    counts={'raw_reader_disagreement_records':len(unresolved),'model_assisted_resolved_records':len(resolved),'still_unresolved_records':len(remaining)}
    if any(review.get('counts',{}).get(k)!=v for k,v in counts.items()):raise ValueError('Declared review counts differ from exact records')
    return {'schema_version':SCHEMA,'review_origin':ORIGIN,'counts':counts,'resolved_candidate_observations':resolved,'remaining_candidate_observations':remaining,'whole_image_observations':copy.deepcopy(whole),'machine_reader_disagreements_unchanged':len(unresolved),'source_quality_clearances':0,'human_approvals':0,'source_packages_modified':False,'selected_text_modified':False,'page_complete':False,'embedding_candidate_allowed':False,'publication_eligible':False,'claim_level':'model_visual_observations_not_independent_raster_quality_proof','source_quality_gates_preserved':copy.deepcopy(completeness['gates'])}

def derive(packet,review):
    from .native_image_regions import verify
    packet=Path(packet);verify(packet)
    bindings={key:sha((packet/file).read_bytes())for key,file in [('source_packet_manifest_sha256','source-regions-manifest.json'),('representation_sha256','representation.json'),('completeness_sha256','completeness.json')]}
    return validate_model_review(load(packet/'representation.json'),load(packet/'completeness.json'),review,bindings)

def consume(packet,review_path,output):
    packet,review_path,output=map(lambda p:Path(p).resolve(),(packet,review_path,output))
    if output.exists()or output.is_relative_to(packet):raise ValueError('New annotation directory outside immutable source packet required')
    reviewed=derive(packet,load(review_path));output.mkdir(parents=True)
    (output/'model-review.json').write_bytes(review_path.read_bytes())
    (output/'reviewed-candidate-observations.json').write_text(json.dumps(reviewed,ensure_ascii=False,indent=2)+'\n')
    binding={'schema_version':SCHEMA,'source_packet_path':str(packet),'source_packet_manifest_sha256':sha((packet/'source-regions-manifest.json').read_bytes()),'review_sha256':sha(review_path.read_bytes()),'engine_sha256':sha(Path(__file__).read_bytes()),'observations_sha256':sha((output/'reviewed-candidate-observations.json').read_bytes()),'publication_eligible':False}
    (output/'review-manifest.json').write_text(json.dumps(binding,indent=2)+'\n')
    return verify_review(output)

def verify_review(output):
    output=Path(output);manifest=load(output/'review-manifest.json')
    if manifest.get('schema_version')!=SCHEMA or manifest.get('publication_eligible')is not False or manifest.get('engine_sha256')!=sha(Path(__file__).read_bytes()):raise ValueError('Review engine/origin manifest differs')
    if sha((output/'model-review.json').read_bytes())!=manifest.get('review_sha256')or sha((output/'reviewed-candidate-observations.json').read_bytes())!=manifest.get('observations_sha256'):raise ValueError('Model review artifacts changed')
    packet=Path(manifest['source_packet_path'])
    if sha((packet/'source-regions-manifest.json').read_bytes())!=manifest['source_packet_manifest_sha256']:raise ValueError('Original source packet changed')
    actual=derive(packet,load(output/'model-review.json'))
    if actual!=load(output/'reviewed-candidate-observations.json'):raise ValueError('Candidate annotation differs from original source-bound model review')
    return {'status':'model_observations_bound_source_quality_still_held','schema_version':SCHEMA,**actual['counts'],'page_complete':False,'embedding_candidate_allowed':False,'source_quality_clearances':0,'human_approvals':0,'publication_eligible':False}

UI_KEYS=('source_pdf_sha256','page','selected_text_sha256','independent_text_sha256','prompt_sha256','focus_sha256','proposal_sha256')
def deduplicate_ui_feedback(rows,questions):
    known={tuple(q[k]for k in UI_KEYS):q for q in questions};effective={};retries=0
    for row in rows:
        key=tuple(row.get(k)for k in UI_KEYS)
        if row.get('kind')!='ocr_disagreement_feedback'or row.get('promotes_page')is not False or key not in known:raise ValueError('Unknown, stale or promoted UI feedback')
        if row.get('decision')not in ('selected','independent','neither','unclear'):raise ValueError('Invalid UI choice')
        stable={k:row.get(k)for k in ('decision','note','disagreement_kind','proposal_choice','proposal_basis','promotes_page')}
        if key in effective:
            if stable!=effective[key]['stable']:raise ValueError('Conflicting UI retries require a new explicit version')
            effective[key]['events'].append(copy.deepcopy(row));retries+=1
        else:effective[key]={'stable':stable,'events':[copy.deepcopy(row)],'question':known[key]}
    return effective,retries

def corrected_mixed_candidate(representation,corrections,applied):
    correction={(r['region_id'],r['reader'],r['line_index']):r for r in corrections};regions=[]
    for region in representation['regions']:
        units=[];all_refs=[]
        for reader in region['readers']:
            for i,line in enumerate(reader['lines']):
                key=(region['id'],reader['reader'],i);change=correction.get(key);literal=change['reviewed_literal']if change else line['text']
                ref={'region_id':region['id'],'reader':reader['reader'],'line_index':i,'original_literal':line['text'],'reviewed_literal':literal,'source_bbox_PDF_points':line['source_bbox_PDF_points'],'origin':change['review_origin']if change else'two_reader_or_original_candidate','application_id':change.get('application_id')if change else None}
                all_refs.append(ref);box=line['source_bbox_PDF_points'];matches=[]
                for n,unit in enumerate(units):
                    other=unit['source_bbox_PDF_points'];inter=max(0,min(box[2],other[2])-max(box[0],other[0]))*max(0,min(box[3],other[3])-max(box[1],other[1]));union=(box[2]-box[0])*(box[3]-box[1])+(other[2]-other[0])*(other[3]-other[1])-inter
                    if literal.strip()==unit['literal'].strip()and union>0 and inter/union>=.5:matches.append(n)
                if len(matches)==1:units[matches[0]]['original_reader_refs'].append(ref)
                else:units.append({'literal':literal,'source_bbox_PDF_points':box,'original_reader_refs':[ref]})
        if sum(len(u['original_reader_refs'])for u in units)!=sum(len(r['lines'])for r in region['readers']):raise ValueError('Corrected candidate omits source reader records')
        regions.append({'region_id':region['id'],'source_crop_render_sha256':region['source_crop_render_sha256'],'source_crop_bbox_PDF_points':region['source_crop_bbox_PDF_points'],'native_selected_text':region['native_selected_text'],'native_page_text_sha256':region['page_text_sha256'],'source_bound_line_candidates':units,'original_reader_record_count':len(all_refs),'reading_order_approved':False})
    return {'schema_version':'pdf2data.corrected-mixed-image-candidate.v1','regions':regions,'applied_focus_decisions':applied,'selected_native_text_modified':False,'original_reader_records_modified':False,'page_complete':False,'embedding_candidate_allowed':False,'publication_eligible':False}

def apply_ui_feedback(packet,review_path,manifest_path,feedback_path,output):
    from .review_queue import load_item
    from .native_image_regions import load as read_packet
    from .quality_gate import assess_package
    import pdfplumber
    from io import BytesIO
    packet,review_path,manifest_path,feedback_path,output=map(lambda p:Path(p).resolve(),(packet,review_path,manifest_path,feedback_path,output))
    if output.exists()or output.is_relative_to(packet):raise ValueError('New applied review directory outside original packet required')
    model=derive(packet,load(review_path));representation=load(packet/'representation.json');source_manifest=load(packet/'source-regions-manifest.json');raw_manifest=manifest_path.read_bytes();raw_feedback=feedback_path.read_bytes();rows=[json.loads(l)for l in raw_manifest.decode().splitlines()if l.strip()];feedback=[json.loads(l)for l in raw_feedback.decode().splitlines()if l.strip()]
    questions=[];crop_proofs={}
    for i,row in enumerate(rows):
        item=load_item(manifest_path,i)
        if row.get('source_package_manifest_sha256')!=source_manifest['parent_package_sha256']or row.get('page_text_sha256')!=representation['regions'][0]['page_text_sha256']:raise ValueError('UI question parent/native page binding differs')
        region=[r for r in representation['regions']if r['source_crop_render_sha256']==row.get('whole_image_crop_sha256')and r['source_pdf_sha256']==item['source_pdf_sha256']and r['page']==item['page']]
        if len(region)!=1:raise ValueError('UI focus is outside exact reviewed original image')
        crop_path=Path(row['original_focus_crop']);crop_hash=sha(crop_path.read_bytes());box=row['original_focus_bbox_PDF_points']
        if crop_hash!=row['original_focus_crop_sha256']:raise ValueError('UI original focus crop changed')
        with pdfplumber.open(item['source_pdf'])as pdf:
            original=pdf.pages[item['page']-1];matches=[]
            for dpi in [120,180,240,300,360]:
                image=original.crop(tuple(box)).to_image(resolution=dpi).original;stream=BytesIO();image.save(stream,format='PNG')
                if sha(stream.getvalue())==crop_hash:matches.append(dpi)
        if len(matches)!=1:raise ValueError('UI source focus render cannot be reproduced uniquely')
        key=tuple(item[k]for k in UI_KEYS);crop_proofs[key]={'source_crop_sha256':crop_hash,'source_focus_bbox_PDF_points':box,'render_dpi':matches[0],'region_id':region[0]['id']};questions.append(item)
    effective,retries=deduplicate_ui_feedback(feedback,questions);remaining=copy.deepcopy(model['remaining_candidate_observations']);corrections=copy.deepcopy(model['resolved_candidate_observations']);applied=[];pending=[]
    for i,item in enumerate(questions):
        key=tuple(item[k]for k in UI_KEYS);answer=effective.get(key)
        if not answer or answer['stable']['decision']in ('neither','unclear'):pending.append(rows[i]);continue
        chosen=answer['stable']['decision'];focus=item['focus'];literal=focus[chosen+'_excerpt'];span=focus.get(chosen+'_span');text=item['selected_text']if chosen=='selected'else item['independent_text']
        if not literal or span is None or text[span[0]:span[1]]!=literal:raise ValueError('UI choice must bind an exact focused literal, never whole-reader replacement')
        proof=crop_proofs[key];box=proof['source_focus_bbox_PDF_points'];group=[]
        for observation in remaining:
            b=observation['source_bbox_PDF_points'];cx,cy=(b[0]+b[2])/2,(b[1]+b[3])/2
            if observation['region_id']==proof['region_id']and box[0]<=cx<=box[2]and box[1]<=cy<=box[3]:group.append(observation)
        if not group or not any(r['literal']==focus['selected_excerpt']for r in group)or not any(r['literal']==focus['independent_excerpt']for r in group):raise ValueError('UI focus does not identify the exact remaining reader group')
        if any(r['literal']not in (focus['selected_excerpt'],focus['independent_excerpt'])for r in group):raise ValueError('UI focus would overwrite unrelated unresolved content')
        application_id=sha(canonical({'question_identity':key,'decision':chosen,'focused_literal':literal,'source_crop_sha256':proof['source_crop_sha256']}))
        entry={'application_id':application_id,'question_identity':{k:item[k]for k in UI_KEYS},'decision':chosen,'applied_literal':literal,**proof,'review_origin':'local_review_UI_user_decision','reviewer_identity_authenticated':False,'feedback_event_count':len(answer['events']),'original_feedback_events':answer['events'],'promotes_page':False}
        applied.append(entry)
        for record in group:
            correction=copy.deepcopy(record);correction.update(status='UI_focus_choice_applied',reviewed_literal=literal,review_origin='local_review_UI_user_decision',reviewer_identity_authenticated=False,application_id=application_id,human_approval=False);corrections.append(correction);remaining.remove(record)
    mixed=corrected_mixed_candidate(representation,corrections,applied)
    mixed['prior_model_visual_observations']=copy.deepcopy(model['whole_image_observations'])
    current=copy.deepcopy(model['whole_image_observations'])
    for region in current:
        region['prior_unresolved_visual_notes']=region.pop('unresolved',[])
        region['remaining_candidate_reader_records']=sum(r['region_id']==region['region_id']for r in remaining)
        region['applied_UI_focus_decisions']=[a for a in applied if a['region_id']==region['region_id']]
        for application in region['applied_UI_focus_decisions']:
            label=re.fullmatch(r'(\d+)[.)]\s*(.*)',application['applied_literal'])
            if not label:continue
            for field in region.get('field_associations',[]):
                if field.get('number')!=label[1]:continue
                if 'exact_glyph_status'in field:field['prior_exact_glyph_status']=field['exact_glyph_status']
                field.update(label=label[2],exact_glyph_status='UI_focus_choice_applied',label_review_origin='local_review_UI_user_decision',application_id=application['application_id'],reviewer_identity_authenticated=False)
    mixed['current_structured_observations']=current
    parent=Path(source_manifest['parent_package_path']);fresh=assess_package(parent/'extraction',parent/'original.pdf',pages=[source_manifest['page']],derive_ocr_source_raster=True)
    receipt={'schema_version':'pdf2data.applied-image-focus-review.v1','status':'applied_with_source_quality_holds','unique_UI_questions_applied':len(applied),'idempotent_retry_records':retries,'raw_feedback_records':len(feedback),'prior_model_resolved_records':len(model['resolved_candidate_observations']),'UI_resolved_reader_records':len(corrections)-len(model['resolved_candidate_observations']),'remaining_review_questions':len(pending),'remaining_candidate_reader_records':len(remaining),'corrected_candidate_available':True,'source_quality_clearances':0,'reviewer_identity_authenticated':False,'selected_native_text_modified':False,'original_reader_records_modified':False,'publication_eligible':False,'applied_decisions':applied,'source_packet_path':str(packet),'source_packet_manifest_sha256':sha((packet/'source-regions-manifest.json').read_bytes()),'model_review_sha256':sha(review_path.read_bytes()),'UI_manifest_sha256':sha(raw_manifest),'UI_feedback_snapshot_sha256':sha(raw_feedback),'engine_sha256':sha(Path(__file__).read_bytes())}
    output.mkdir(parents=True)
    (output/'UI-feedback-snapshot.jsonl').write_bytes(raw_feedback);(output/'UI-manifest-snapshot.jsonl').write_bytes(raw_manifest);(output/'model-review.json').write_bytes(review_path.read_bytes())
    for name,value in [('applied-review-receipt.json',receipt),('corrected-mixed-candidate.json',mixed),('fresh-native-quality.json',fresh),('remaining-review-reader-records.json',remaining)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (output/'review-manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'for r in pending))
    (output/'resolved-audit.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'for r in corrections))
    return receipt

def verify_applied_review(output):
    output=Path(output);receipt=load(output/'applied-review-receipt.json')
    if receipt.get('engine_sha256')!=sha(Path(__file__).read_bytes()):raise ValueError('UI application engine changed')
    with tempfile.TemporaryDirectory(prefix='revalidate-image-UI-choice-')as folder:
        replay=Path(folder)/'replay'
        actual=apply_ui_feedback(receipt['source_packet_path'],output/'model-review.json',output/'UI-manifest-snapshot.jsonl',output/'UI-feedback-snapshot.jsonl',replay)
        expected={p.name for p in replay.iterdir()}
        observed={p.name for p in output.iterdir()}-{'verification.json'}
        if expected!=observed:raise ValueError('Applied UI artifact coverage differs')
        for name in expected:
            if(replay/name).read_bytes()!=(output/name).read_bytes():raise ValueError('Applied source UI review differs: '+name)
    return {k:v for k,v in actual.items()if k!='applied_decisions'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--packet',type=Path,required=True);p.add_argument('--review',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--ui-manifest',type=Path);p.add_argument('--ui-feedback',type=Path);a=p.parse_args()
    if bool(a.ui_manifest)!=bool(a.ui_feedback):raise ValueError('Both exact UI manifest and feedback are required')
    result=apply_ui_feedback(a.packet,a.review,a.ui_manifest,a.ui_feedback,a.output)if a.ui_manifest else consume(a.packet,a.review,a.output)
    if a.ui_manifest:
        checked=verify_applied_review(a.output);(a.output/'verification.json').write_text(json.dumps(checked,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k!='applied_decisions'}))
if __name__=='__main__':main()
