"""Immutable source-quality versions and exact-focus logical review resolution."""
from collections import defaultdict
import copy
import json
from pathlib import Path
import shutil
import tempfile

from .legal_l1 import verify_document,rows,write_rows,readiness_ledger
from .native_token_spacing import reconstitute_page,digest
from .proof_identity import inspect_pdf2data_profile,proof_configuration,canonical


def read(path):return json.loads(Path(path).read_text())
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def create_spacing_reproof(parent,source_runtime,pages,output):
    from .quality_gate import assess_package
    parent,source_runtime,output=map(lambda p:Path(p).resolve(),(parent,source_runtime,output))
    if output.exists():raise ValueError('Choose a new immutable repair output')
    verified=verify_document(parent);source=parent/'original.pdf';parent_sha=digest((parent/'l1-package.json').read_bytes())
    profile=inspect_pdf2data_profile(source_runtime);config=read(parent/'source-receipt.json')['configuration']
    if any(config.get(k)!=v for k,v in profile['source_configuration_expected'].items()):raise ValueError('Original extraction runtime/configuration differs')
    all_pages={p['page']:p for p in rows(parent/'extraction/pages.jsonl')};proofs=[];new_quality={}
    for n in sorted(set(pages)):
        if n not in all_pages:raise ValueError('Requested source page is absent')
        proof=reconstitute_page(source,n,all_pages[n]['text'])
        report=assess_package(parent/'extraction',source,pages=[n],independent_by_page={n:proof['independent_corrected_text']},derive_independent_native=False,derive_ocr_source_raster=True)
        if not report['results']:raise ValueError('Source assessment produced no page')
        proof['whole_page_quality']=report['results'][0]
        repeated=reconstitute_page(source,n,all_pages[n]['text'])
        if any(repeated[k]!=proof[k]for k in repeated):raise ValueError('Original source token proof did not reproduce')
        if proof['repairs']and report['results'][0]['outcome']=='auto_accepted':new_quality[n]=report['results'][0]
        proof['signature']=digest(canonical(proof));proofs.append(proof)
    output.mkdir(parents=True);save(output/'page-proofs.json',proofs)
    configuration=proof_configuration('native_token_spacing',parent,Path(__file__))
    configuration['source_runtime']=str(source_runtime)
    result={'contract_version':'pdf2data.native-token-reproof.v1','parent_l1_package_path':str(parent),
            'parent_manifest_sha256':parent_sha,'quality_repair_configuration':configuration,
            'proofs_sha256':digest((output/'page-proofs.json').read_bytes()),'source_pdf_sha256':verified['source_pdf_sha256'],
            'resolved_token_count':sum(len(p['repairs'])for p in proofs),'whole_pages_cleared':sorted(new_quality),
            'source_text_modified':False,'legal_review':'not_run','publication_eligible':False,'model_calls':0,'remote_operations':0}
    if new_quality:
        destination=output/'packages'/parent.name;destination.parent.mkdir()
        with tempfile.TemporaryDirectory(prefix='.source-quality-',dir=output)as tmp:
            stage=Path(tmp)/parent.name;shutil.copytree(parent,stage)
            raw=rows(stage/'layout.jsonl')
            for line in raw:
                if line['page']in new_quality:
                    line['native_token_spacing_proof']=next(p for p in proofs if p['page']==line['page'])
                    line['quality_repair_configuration']=configuration
            write_rows(stage/'layout.jsonl',raw)
            q=read(stage/'quality.json');q['results']=[new_quality.get(p['page'],p)for p in q['results']]
            q['counts']={state:sum(p['outcome']==state for p in q['results'])for state in ('auto_accepted','repair_required','source_blocked')}
            save(stage/'quality.json',q);save(stage/'readiness.json',readiness_ledger(read(stage/'structure.json'),q))
            write_rows(stage/'repair-queue.jsonl',[job for job in rows(stage/'repair-queue.jsonl')if job['page']not in new_quality])
            source_receipt=read(stage/'source-receipt.json');source_receipt['quality_repair_configuration']=configuration;save(stage/'source-receipt.json',source_receipt)
            manifest=read(stage/'l1-package.json');manifest['request_sha256']=digest(canonical({'parent':parent_sha,'operation':'native_token_spacing','proofs':result['proofs_sha256']}))
            manifest['artifacts']={name:digest((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest)
            verify_document(stage)
            if (stage/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes():raise ValueError('Selected text was changed')
            stage.rename(destination)
        result['new_l1_package_path']=str(destination);result['new_l1_package_sha256']=digest((destination/'l1-package.json').read_bytes())
    result['signature']=digest(canonical(result));save(output/'validation.json',result)
    return result


def verify_spacing_reproof(output,source_runtime,fresh=True):
    output=Path(output);result=read(output/'validation.json');parent=Path(result['parent_l1_package_path'])
    verify_document(parent)
    if digest((parent/'l1-package.json').read_bytes())!=result['parent_manifest_sha256']:raise ValueError('Original source package changed')
    if digest((output/'page-proofs.json').read_bytes())!=result['proofs_sha256']:raise ValueError('Token repair proof bytes changed')
    current=proof_configuration('native_token_spacing',parent,Path(__file__))
    if current['proof_profile']!=result['quality_repair_configuration']['proof_profile']:raise ValueError('Current proof tool differs from pinned proof configuration')
    proofs=read(output/'page-proofs.json')
    if fresh:
        with tempfile.TemporaryDirectory()as tmp:
            replay=create_spacing_reproof(parent,source_runtime,[p['page']for p in proofs],Path(tmp)/'replay')
            if read(Path(tmp)/'replay/page-proofs.json')!=proofs:raise ValueError('Source token repair evidence does not revalidate')
    return {'status':'verified','source_pdf_sha256':result['source_pdf_sha256'],'whole_pages_cleared':result['whole_pages_cleared'],
            'proofs':proofs,'publication_eligible':False,'source_text_modified':False}


def decode_spacing_proof(proof,parent):
    """Reject a forged/stale JSON receipt by reconstructing its source evidence."""
    from .quality_gate import assess_package
    parent=Path(parent);verify_document(parent)
    n=proof['page'];page=next(p for p in rows(parent/'extraction/pages.jsonl')if p['page']==n)
    expected=reconstitute_page(parent/'original.pdf',n,page['text'])
    report=assess_package(parent/'extraction',parent/'original.pdf',pages=[n],independent_by_page={n:expected['independent_corrected_text']},derive_independent_native=False,derive_ocr_source_raster=True)
    expected['whole_page_quality']=report['results'][0];expected['signature']=digest(canonical(expected))
    if expected!=proof:raise ValueError('Registered token proof differs from original source revalidation')
    return expected


def filter_revalidated_focus(manifest,proofs,output):
    """Remove only an exact difference contained within one validated token."""
    import re
    manifest,output=Path(manifest),Path(output)
    if output.exists():raise ValueError('Choose a new immutable review resolution directory')
    kept=[];resolved=[];verified={};held_proofs=[]
    for index,row in enumerate(rows(manifest)):
        try:
            matches=[p for p in proofs if(p['source_pdf_sha256'],p['page'],p['page_text_sha256'],p['independent_raw_sha256'])==
                     (row.get('source_pdf_sha256'),row.get('page'),row.get('selected_text_sha256'),row.get('independent_text_sha256'))]
            focus=row.get('focus')or{};match=None
            if len(matches)==1 and focus.get('kind')in {'number','date','legal_operator','text_change'}:
                proof=matches[0]
                cache_key=(row['l1_package'],proof['signature'])
                if cache_key not in verified:verified[cache_key]=decode_spacing_proof(proof,row['l1_package'])
                selected=Path(row['selected_text']).read_text();independent=read(row['independent_json'])['text']
                if digest(selected)!=row['selected_text_sha256']or digest(independent)!=row['independent_text_sha256']:raise ValueError('Review reading artifacts changed')
                a,b=focus.get('selected_span',[-1,-1]);c,d=focus.get('independent_span',[-1,-1])
                if 0<=a<b<=len(selected)and 0<=c<d<=len(independent)and '\n'not in independent[c:d]and '\r'not in independent[c:d]:
                    for repair in proof['repairs']:
                        scope=repair.get('selected_span');other=repair['independent_span']
                        if scope and scope[0]<=a<b<=scope[1]and other[0]<=c<d<=other[1]and re.sub(r'[ \t]+','',independent[c:d])==selected[a:b]:
                            match=repair;break
        except (ValueError,KeyError,TypeError,OSError,IndexError)as error:
            kept.append(row)
            held_proofs.append({'original_index':index,'original_row_sha256':digest(canonical(row)),
                                'status':'held','reason':type(error).__name__+': '+str(error),
                                'page_quality_cleared':False,'publication_eligible':False})
            continue
        if match:
            resolved.append({'original_index':index,'original_row_sha256':digest(canonical(row)),
                             'source_pdf_sha256':row['source_pdf_sha256'],'page':row['page'],
                             'selected_text_sha256':row['selected_text_sha256'],'focus':focus,
                             'resolution':'source_revalidated_logical_formatting','review_origin':'automatic_source_geometry_rule',
                             'proof_signature':matches[0]['signature'],'repair':match,
                             'page_quality_cleared':matches[0]['whole_page_quality']['outcome']=='auto_accepted',
                             'human_review_claimed':False,'publication_eligible':False})
        else:kept.append(row)
    output.mkdir(parents=True);write_rows(output/'review-manifest.jsonl',kept);write_rows(output/'resolved-audit.jsonl',resolved);write_rows(output/'held-proof-audit.jsonl',held_proofs)
    result={'contract_version':'pdf2data.source-revalidated-review-filter.v1','input_manifest_sha256':digest(manifest.read_bytes()),
            'input_count':len(kept)+len(resolved),'resolved_count':len(resolved),'remaining_count':len(kept),
            'manifest':str(output/'review-manifest.jsonl'),'audit':str(output/'resolved-audit.jsonl'),
            'held_proof_errors':len(held_proofs),'human_review_claimed':False,'publication_eligible':False}
    save(output/'summary.json',result);return result


def repair_review_queue(manifest,source_runtime,output):
    manifest,output=Path(manifest),Path(output)
    if output.exists():raise ValueError('Choose a new immutable logical repair output')
    output.mkdir(parents=True);groups=defaultdict(set)
    for row in rows(manifest):groups[row['l1_package']].add(row['page'])
    proofs=[];versions=[]
    for index,(parent,pages)in enumerate(groups.items()):
        dest=output/f'source-{index+1:04}'
        try:
            result=create_spacing_reproof(parent,source_runtime,pages,dest)
            verify_spacing_reproof(dest,source_runtime,fresh=True)
            proofs.extend(read(dest/'page-proofs.json'))
            terminal={'status':'source_revalidated','requested_pages':sorted(pages),'source_files_modified':False,'publication_eligible':False}
        except (ValueError,KeyError,TypeError,OSError,IndexError,RuntimeError)as error:
            parent_manifest=Path(parent)/'l1-package.json'
            result={'status':'held_source_group_error','parent_l1_package_path':str(parent),
                    'parent_manifest_sha256':digest(parent_manifest.read_bytes())if parent_manifest.is_file()else None,
                    'requested_pages':sorted(pages),'whole_pages_cleared':[],
                    'reason':type(error).__name__+': '+str(error),'source_capture_attested':False,
                    'source_text_modified':False,'publication_eligible':False}
            terminal=result
        dest.mkdir(parents=True,exist_ok=True);save(dest/'group-terminal.json',terminal);versions.append(result)
        save(output/'progress.json',{'requested_source_groups':len(groups),'completed_source_groups':len(versions),
             'held_source_groups':sum(v.get('status')=='held_source_group_error'for v in versions),
             'source_quality_pages_cleared':sum(len(v['whole_pages_cleared'])for v in versions),
             'model_calls':0,'remote_operations':0,'publication_eligible':False})
    save(output/'page-proofs.json',proofs)
    filtered=filter_revalidated_focus(manifest,proofs,output/'review-queue')
    result={'contract_version':'pdf2data.logical-repair-review.v1','sources':versions,'review_filter':filtered,
            'source_quality_pages_cleared':sum(len(v['whole_pages_cleared'])for v in versions),
            'held_source_groups':sum(v.get('status')=='held_source_group_error'for v in versions),
            'proof_profile':inspect_pdf2data_profile(Path(__file__).resolve().parents[2]),
            'source_text_modified':False,'publication_eligible':False,'model_calls':0,'remote_operations':0}
    save(output/'validation.json',result);return result
