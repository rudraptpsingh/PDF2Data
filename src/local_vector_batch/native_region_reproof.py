"""Compact registered native-region quality over an immutable L1 parent.

This is a strategy of targeted_reproof, not a new extraction/package format.
Stored snapshots are builder candidates only. The verifier always captures the
current original source once, rederives regions, and compares every quality gate.
"""
import copy
import json
from pathlib import Path
import sys
import time
import traceback

STRATEGY='native_regions'
OPERATION='targeted_native_region_reproof'


def _api():
    from local_vector_batch import targeted_reproof
    return targeted_reproof


def _bound(parent,origin,runtime,modules,expected=None):
    api=_api();origin=Path(origin).resolve()
    origin_receipt=api.read(origin/'runtime-receipt.json')
    if any(api.file_sha(origin/name)!=value for name,value in origin_receipt['files'].items()):raise ValueError('Frozen source origin changed')
    bound=api.bind_parent(parent,origin,modules,expected)
    expected_config=api.inspect_pdf2data_profile(origin)['source_configuration_expected']
    if any(bound['source']['configuration'].get(k)!=v for k,v in expected_config.items()):raise ValueError('Source extraction origin differs')
    bound['source_origin_profile']=bound['profile'];bound['profile']=api.inspect_pdf2data_profile(runtime)
    return bound


def _configuration(bound,runtime,modules,origin):
    api=_api()
    return {'operation':OPERATION,'parent_manifest_sha256':bound['manifest_sha256'],
            'proof_runtime_tree_sha256':modules['epoch']['tree_sha256'],'proof_implementation_sha256':api.file_sha(__file__),
            'proof_profile':bound['profile'],'source_runtime':str(Path(origin).resolve()),
            'source_extraction_profile':bound['source_origin_profile']}


def _evaluate(bound,origin,n,*,candidate_snapshot=None):
    from local_vector_batch.native_table_feedback import capture_source,validate_feedback
    from local_vector_batch.native_table_regions import derive_regions,_quality_from_regions
    api=_api();page=bound['pages'][n];started=time.perf_counter()
    snapshot=copy.deepcopy(candidate_snapshot)if candidate_snapshot is not None else capture_source(bound['path'],origin,n)
    observation='untrusted_builder_candidate'if candidate_snapshot is not None else'fresh_original_capture'
    if any(snapshot.get(k)!=v for k,v in {'source_pdf_sha256':bound['source_pdf_sha256'],'page':n,'page_text_sha256':page['text_sha256']}.items()):raise ValueError('Region snapshot source/page/text binding changed')
    policy=__import__('local_vector_batch.quality_gate',fromlist=['load_policy']).load_policy()
    policy_hash=api.digest(json.dumps(policy,sort_keys=True,ensure_ascii=False))
    if snapshot.get('fresh_quality',{}).get('policy_sha256')!=policy_hash:
        if candidate_snapshot is None:raise ValueError('Fresh source policy identity differs')
        snapshot=capture_source(bound['path'],origin,n)
        observation='fresh_original_capture'
    from .native_region_diagnostics import HeldRegionEvidence
    cells=None;stage='validate_cells'
    try:
        cells=validate_feedback(snapshot,[],{})
        if cells['status']!='validated':raise ValueError('Region source cells remain held')
        stage='derive_regions';proof=derive_regions(bound['path'],snapshot,cells);quality=_quality_from_regions(snapshot,proof)
    except MemoryError:
        raise
    except Exception as error:
        raise HeldRegionEvidence(error,stage,snapshot,cells,observation)from error
    return {'page':n,'page_text_sha256':page['text_sha256'],'source_pdf_sha256':bound['source_pdf_sha256'],
            'snapshot':snapshot,'cells':cells,'region_proof':proof,'quality':quality,
            'selected_text_modified':False,'legal_review':'not_run','answer_publication_eligible':False,
            'elapsed_seconds':round(time.perf_counter()-started,6)}


def _stable(proof):
    return {k:v for k,v in proof.items()if k!='elapsed_seconds'}


def create_region_reproof(parent,runtime,source_runtime,pages,output,*,expected_manifest_sha256=None,candidate_snapshots=None):
    api=_api();runtime=Path(runtime).resolve();output=Path(output).resolve();started=time.perf_counter()
    if output.exists():raise ValueError('New immutable region sidecar output required')
    modules=api.runtime_modules(runtime);bound=_bound(parent,source_runtime,runtime,modules,expected_manifest_sha256)
    pages=sorted(set(pages));old={q['page']:q for q in bound['quality']['results']}
    if not pages or any(type(n)is not int or n not in bound['pages']or old[n]['outcome']=='auto_accepted'for n in pages):raise ValueError('Existing held physical target pages required')
    # Failed witnesses are durable candidates immediately, not exception/frame
    # buffers multiplied across a partition. No manifest is sealed until the
    # final unchanged-parent check; interrupted outputs cannot confer quality.
    output.mkdir(parents=True)
    candidates=candidate_snapshots or {};proofs=[];candidate_inputs={}
    for n in pages:
        try:
            candidate=candidates.get(n,candidates.get(str(n)))
            if candidate is not None:
                path=Path(candidate).resolve();candidate_inputs[str(n)]={'path':str(path),'sha256':api.file_sha(path),'authority':'builder_candidate_only_final_fresh_source_decoder_required'};candidate=api.read(path)
                from .native_region_diagnostics import CONTRACT,load_binding
                if candidate.get('contract_version')==CONTRACT:candidate=load_binding(path,bound,source_runtime,runtime,modules,n)['snapshot']
            evaluated=_evaluate(bound,source_runtime,n,candidate_snapshot=candidate);evaluated['status']='source_proved';proofs.append(evaluated)
        except ValueError as error:
            from .native_region_diagnostics import HeldRegionEvidence
            proofs.append({'page':n,'page_text_sha256':bound['pages'][n]['text_sha256'],'source_pdf_sha256':bound['source_pdf_sha256'],
                           'status':'held','reason':str(error).encode('utf-8','backslashreplace').decode('utf-8'),'quality':old[n],'selected_text_modified':False,
                           'legal_review':'not_run','answer_publication_eligible':False})
            if isinstance(error,HeldRegionEvidence)and not isinstance(error.original_error,ValueError):
                original=error.original_error;message=str(original)
                proofs[-1].update(failure_kind='technical_reader_exception',technical_error={
                    'stage':error.stage,'class':type(original).__module__+'.'+type(original).__qualname__,
                    'message_codepoints':[ord(c)for c in message],'message_ascii_json':json.dumps(message,ensure_ascii=True),
                    'traceback_ascii_json':json.dumps(traceback.format_exc(),ensure_ascii=True),
                    'diagnostic_not_selected_source_text':True,'source_quality_clearance':False})
            if isinstance(error,HeldRegionEvidence):
                from .native_region_diagnostics import persist
                try:
                    proofs[-1]['diagnostics']=persist(output,bound,source_runtime,runtime,modules,n,error)
                finally:
                    # The original exception can own capture/raster stack frames.
                    # Only lossless JSON witnesses and small links survive here.
                    error.original_error.__traceback__=None
                    error.original_error.__context__=None
                    error.original_error.__cause__=None
                    error.__traceback__=None
                    error.__context__=None
                    error.__cause__=None
        except MemoryError:
            # Capacity failure aborts this partition for worker cleanup. Do not
            # keep evaluating sibling pages in a potentially exhausted process.
            raise
        except Exception as error:
            # A reader failure is NOT quality evidence. Preserve its exact
            # codepoints/escaped traceback without losing successful siblings.
            message=str(error);safe_message=message.encode('utf-8','backslashreplace').decode('utf-8')
            proofs.append({'page':n,'page_text_sha256':bound['pages'][n]['text_sha256'],'source_pdf_sha256':bound['source_pdf_sha256'],
                           'status':'held','failure_kind':'technical_reader_exception','reason':type(error).__name__+': '+safe_message,
                           'technical_error':{'stage':'candidate_read_or_native_source_evaluation','class':type(error).__module__+'.'+type(error).__qualname__,
                               'message_codepoints':[ord(c)for c in message],'message_ascii_json':json.dumps(message,ensure_ascii=True),
                               'traceback_ascii_json':json.dumps(traceback.format_exc(),ensure_ascii=True),
                               'diagnostic_not_selected_source_text':True,'source_quality_clearance':False},
                           'quality':old[n],'selected_text_modified':False,'legal_review':'not_run','answer_publication_eligible':False})
    api.verify_parent_unchanged(bound)
    selected={p['page']:p['quality']for p in proofs};merged={**bound['quality'],'results':[selected.get(q['page'],q)for q in bound['quality']['results']]}
    merged['counts']={state:sum(q['outcome']==state for q in merged['results'])for state in('auto_accepted','repair_required','source_blocked')}
    api.save(output/'quality.json',merged);api.save(output/'page-proofs.json',proofs)
    api.save(output/'parent-bindings.json',{'parent_l1_package_path':str(bound['path']),'artifacts_sha256':bound['artifact_hashes'],'source_extraction_configuration':bound['source']['configuration']})
    config=_configuration(bound,runtime,modules,source_runtime)
    receipt={'contract_version':api.CONTRACT,'strategy':STRATEGY,'operation':OPERATION,'parent_l1_package_path':str(bound['path']),
             'parent_manifest_sha256':bound['manifest_sha256'],'source_pdf_sha256':bound['source_pdf_sha256'],
             'source_extraction_configuration':bound['source']['configuration'],'source_runtime':str(Path(source_runtime).resolve()),
             'proof_runtime_path':str(runtime),'proof_runtime_tree_sha256':modules['epoch']['tree_sha256'],'proof_profile':bound['profile'],
             'proof_implementation_sha256':api.file_sha(__file__),'quality_repair_configuration':config,'target_pages':pages,
             'passing_pages_unchanged':True,'source_extraction_rerun':False,'selected_text_modified':False,
             'newly_auto_accepted_pages':[p['page']for p in proofs if p['status']=='source_proved'],
             'still_held_pages':[p['page']for p in proofs if p['status']=='held'],
             'technical_hold_pages':[p['page']for p in proofs if p.get('failure_kind')=='technical_reader_exception'],
             'failed_diagnostic_pages':[p['page']for p in proofs if 'diagnostics'in p],
             'artifacts_sha256':{name:api.file_sha(output/name)for name in('quality.json','page-proofs.json','parent-bindings.json')},
             'candidate_input_receipts':candidate_inputs,'status':'blocked_pending_registered_source_decoder',
             'elapsed_seconds':round(time.perf_counter()-started,6),'legal_review':'not_run','answer_publication_eligible':False,
             'new_model_calls':0,'remote_operations':0}
    api.save(output/'reproof-version.json',receipt);return receipt


def verify_region_reproof(output,runtime,*,fresh=True):
    api=_api();output=Path(output).resolve();runtime=Path(runtime).resolve();started=time.perf_counter();receipt=api.read(output/'reproof-version.json')
    if receipt.get('contract_version')!=api.CONTRACT or receipt.get('strategy')!=STRATEGY or receipt.get('operation')!=OPERATION:raise ValueError('Unknown registered region sidecar strategy')
    if any(receipt.get(k)is not v for k,v in [('passing_pages_unchanged',True),('source_extraction_rerun',False),('selected_text_modified',False),('answer_publication_eligible',False)])or receipt.get('legal_review')!='not_run':raise ValueError('Closed source-quality-only scope required')
    if set(receipt.get('artifacts_sha256',{}))!={'quality.json','page-proofs.json','parent-bindings.json'}or any(api.file_sha(output/name)!=value for name,value in receipt['artifacts_sha256'].items()):raise ValueError('Region sidecar artifact changed')
    modules=api.runtime_modules(runtime)
    if receipt.get('proof_implementation_sha256')!=api.file_sha(__file__)or receipt.get('proof_runtime_path')!=str(runtime)or receipt.get('proof_runtime_tree_sha256')!=modules['epoch']['tree_sha256']:raise ValueError('Region proof code/runtime changed')
    bound=_bound(receipt['parent_l1_package_path'],receipt['source_runtime'],runtime,modules,receipt['parent_manifest_sha256'])
    if receipt.get('quality_repair_configuration')!=_configuration(bound,runtime,modules,receipt['source_runtime'])or receipt.get('proof_profile')!=bound['profile']or receipt.get('source_pdf_sha256')!=bound['source_pdf_sha256']or receipt.get('source_extraction_configuration')!=bound['source']['configuration']:raise ValueError('Region sidecar source/profile binding changed')
    bindings=api.read(output/'parent-bindings.json')
    if bindings!={'parent_l1_package_path':str(bound['path']),'artifacts_sha256':bound['artifact_hashes'],'source_extraction_configuration':bound['source']['configuration']}:raise ValueError('Region parent artifact graph changed')
    proofs=api.read(output/'page-proofs.json');targets=receipt['target_pages'];old={q['page']:q for q in bound['quality']['results']}
    if not targets or targets!=sorted(set(targets))or any(type(n)is not int or n not in old or old[n]['outcome']=='auto_accepted'for n in targets)or [p['page']for p in proofs]!=targets:raise ValueError('Region page scope changed')
    passed=[p for p in proofs if p.get('status')=='source_proved'];held=[p for p in proofs if p.get('status')=='held']
    if len(passed)+len(held)!=len(proofs)or any(p['quality']!=old[p['page']]for p in held):raise ValueError('Held page cannot acquire a quality clearance')
    from .native_region_diagnostics import verify_link
    for proof in proofs:
        if 'diagnostics'in proof:
            if proof.get('status')!='held':raise ValueError('Failed diagnostic cannot become a passing proof')
            verify_link(proof['diagnostics'],output,bound,receipt['source_runtime'],runtime,modules,proof['page'])
    if receipt.get('failed_diagnostic_pages',[])!=[p['page']for p in proofs if 'diagnostics'in p]:raise ValueError('Failed diagnostic page scope changed')
    technical=[p['page']for p in held if p.get('failure_kind')=='technical_reader_exception']
    if receipt.get('technical_hold_pages',[])!=technical:raise ValueError('Technical hold scope changed')
    if fresh:
        for proof in passed:
            actual=_evaluate(bound,receipt['source_runtime'],proof['page']);actual['status']='source_proved'
            if _stable(actual)!=_stable(proof):raise ValueError('Stored region sidecar differs from fresh original-source proof')
    selected={p['page']:p['quality']for p in proofs};expected={**bound['quality'],'results':[selected.get(q['page'],q)for q in bound['quality']['results']]}
    expected['counts']={state:sum(q['outcome']==state for q in expected['results'])for state in('auto_accepted','repair_required','source_blocked')}
    if api.read(output/'quality.json')!=expected or receipt.get('newly_auto_accepted_pages')!=[p['page']for p in passed]or receipt.get('still_held_pages')!=[p['page']for p in held]:raise ValueError('Region virtual quality changed outside proof scope')
    if any(p['quality'].get('outcome')!='auto_accepted'or p['quality'].get('page_complete')is not True or p['quality'].get('failed_components')or p['quality'].get('missing_evidence')or any(p['quality'].get('components',{}).get(c,{}).get('state')!='pass'for c in api.COMPONENTS)for p in passed):raise ValueError('Every region source-quality gate is required')
    api.verify_parent_unchanged(bound)
    from local_vector_batch.legal_l1 import readiness_ledger
    return {'contract_version':'powerlex.pdf2data-targeted-reproof-readiness.v1','strategy':STRATEGY,'integrity':'passed',
            'parent_l1_package_path':str(bound['path']),'reproof_path':str(output),'reproof_sha256':api.file_sha(output/'reproof-version.json'),
            'source_pdf_sha256':bound['source_pdf_sha256'],'parent_integrity_verification':bound['verified'],
            'virtual_readiness':readiness_ledger(api.read(bound['path']/'structure.json'),expected),
            'quality_repair_configuration':receipt['quality_repair_configuration'],'source_extraction_configuration':bound['source']['configuration'],
            'proof_profile':bound['profile'],'current_proof_profile_matches':True,'source_extraction_rerun':False,'selected_text_modified':False,
            'fresh_original_reproof':fresh,'actual_fresh_registered_decoder_calls':len(passed)if fresh else 0,'fresh_source_decoder_pages':[p['page']for p in passed]if fresh else[],
            'newly_auto_accepted_pages':[p['page']for p in passed],'still_held_pages':[p['page']for p in held],'technical_hold_pages':technical,
            'failed_diagnostic_pages':[p['page']for p in held if 'diagnostics'in p],'diagnostics_are_not_quality_clearance':True,
            'target_pages':[{'page':p['page'],'page_text_sha256':p['page_text_sha256'],'quality':p['quality'],'embedding_candidate_allowed':fresh and p['status']=='source_proved'and bool(bound['pages'][p['page']]['text'].strip())}for p in proofs],
            'untargeted_quality_retained_with_original_producer_identity':True,'legal_review':'not_run','answer_publication_eligible':False,
            'new_model_calls':0,'remote_operations':0,'verification_elapsed_seconds':round(time.perf_counter()-started,6)}
