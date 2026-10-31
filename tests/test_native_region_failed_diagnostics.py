"""Synthetic retention/binding tests; no source/legal approval is inferred."""
import copy,hashlib,json
from pathlib import Path
import pytest


def setup(tmp_path,monkeypatch,surrogate=True):
    from local_vector_batch import native_region_reproof as engine,targeted_reproof as api
    from local_vector_batch import native_table_feedback as cells,native_table_regions as regions
    parent=tmp_path/'parent';parent.mkdir();(parent/'original.pdf').write_bytes(b'synthetic source bytes; capture is explicitly stubbed')
    (parent/'l1-package.json').write_text('{}');(parent/'structure.json').write_text('{"units":[]}')
    text='Complete source diagnostic '+'नहीं '*2500+('\udc65'if surrogate else'complete')
    page={'page':1,'text':text,'text_sha256':hashlib.sha256(text.encode('utf-8','surrogatepass')).hexdigest()}
    pdfsha=api.file_sha(parent/'original.pdf');q={'page':1,'page_text_sha256':page['text_sha256'],'source_pdf_sha256':pdfsha,'outcome':'repair_required','page_complete':False,
        'failed_components':['layout'],'missing_evidence':[],'components':{k:{'state':'fail'if k=='layout'else'pass'}for k in api.COMPONENTS}}
    bound={'path':parent,'manifest_sha256':api.file_sha(parent/'l1-package.json'),'source_pdf_sha256':pdfsha,'profile':{'profile_sha256':'c'*64},
        'source_origin_profile':{'profile_sha256':'e'*64},'artifact_hashes':{},'source':{'configuration':{}},'pages':{1:page},'layout':{1:{'page':1,'raw_text':text}},'quality':{'results':[q]},'verified':{'integrity':'passed'}}
    modules={'epoch':{'tree_sha256':'d'*64}};origin=tmp_path/'origin';runtime=tmp_path/'runtime';origin.mkdir();runtime.mkdir()
    policy_hash=api.digest(json.dumps(__import__('local_vector_batch.quality_gate',fromlist=['load_policy']).load_policy(),sort_keys=True,ensure_ascii=False))
    snapshot={'source_pdf_sha256':pdfsha,'page':1,'page_text_sha256':page['text_sha256'],'tables':[{'complete_text':text}],'raw_tables':[{'complete_text':text}],
        'fresh_quality':{'policy_sha256':policy_hash,'results':[copy.deepcopy(q)]}}
    report={'source_pdf_sha256':pdfsha,'page':1,'page_text_sha256':page['text_sha256'],'status':'held','tables':[{'cells':[{'selected':text,'independent':text+'different'}]}],'page_quality_cleared':False}
    monkeypatch.setattr(api,'runtime_modules',lambda r:modules);monkeypatch.setattr(api,'verify_parent_unchanged',lambda b:None);monkeypatch.setattr(engine,'_bound',lambda *args:bound)
    monkeypatch.setattr(cells,'capture_source',lambda *args:copy.deepcopy(snapshot));monkeypatch.setattr(cells,'validate_feedback',lambda *args:copy.deepcopy(report))
    return engine,api,bound,modules,origin,runtime,snapshot,report,cells,regions


def test_complete_failed_snapshot_and_report_are_sealed_lossless_and_stay_held(tmp_path,monkeypatch):
    engine,api,bound,modules,origin,runtime,snapshot,report,*_=setup(tmp_path,monkeypatch)
    sidecar=tmp_path/'sidecar';receipt=engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar)
    proof=api.read(sidecar/'page-proofs.json')[0]
    assert proof['status']=='held'and proof['quality']==bound['quality']['results'][0]
    assert receipt['newly_auto_accepted_pages']==[]and set(receipt['artifacts_sha256'])=={'quality.json','page-proofs.json','parent-bindings.json'}
    from local_vector_batch.native_region_diagnostics import verify_link
    data=verify_link(proof['diagnostics'],sidecar,bound,origin,runtime,modules,1)
    assert data['snapshot']==snapshot and data['cells']==report
    assert len(data['cells']['tables'][0]['cells'][0]['selected'])>10000
    assert '\udc65'in data['snapshot']['tables'][0]['complete_text']
    assert data['binding']['source_pdf_path']==str((bound['path']/'original.pdf').resolve())
    result=engine.verify_region_reproof(sidecar,runtime,fresh=True)
    assert result['still_held_pages']==[1]and result['newly_auto_accepted_pages']==[]
    assert result['actual_fresh_registered_decoder_calls']==0
    assert result['target_pages'][0]['embedding_candidate_allowed']is False


@pytest.mark.parametrize('attack',['snapshot_hash','profile','source_pdf','parent_hash','absolute_link','parent_escape','symlink_escape','page_type','quality_authority'])
def test_diagnostic_reuse_rejects_source_profile_hash_escape_or_authority_changes(tmp_path,monkeypatch,attack):
    engine,api,bound,modules,origin,runtime,*_=setup(tmp_path,monkeypatch);sidecar=tmp_path/'sidecar'
    engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar);proof=api.read(sidecar/'page-proofs.json')[0];binding_path=sidecar/proof['diagnostics']['binding']['path'];binding=api.read(binding_path)
    from local_vector_batch.native_region_diagnostics import load_binding
    if attack=='snapshot_hash':(binding_path.parent/'source-snapshot.json').write_text('{}')
    elif attack=='profile':binding['proof_profile_sha256']='f'*64
    elif attack=='source_pdf':(bound['path']/'original.pdf').write_bytes(b'changed original')
    elif attack=='parent_hash':binding['parent_manifest_sha256']='f'*64
    elif attack=='absolute_link':binding['artifacts']['source-snapshot.json']['path']=str((binding_path.parent/'source-snapshot.json').resolve())
    elif attack=='parent_escape':binding['artifacts']['source-snapshot.json']['path']='../source-snapshot.json'
    elif attack=='symlink_escape':
        outside=tmp_path/'outside.json';outside.write_text('{}');(binding_path.parent/'escape.json').symlink_to(outside);binding['artifacts']['source-snapshot.json']={'path':'escape.json','sha256':api.file_sha(outside)}
    elif attack=='page_type':binding['page']=True
    else:binding['source_quality_clearance']=True
    binding_path.write_text(json.dumps(binding))
    with pytest.raises(ValueError):load_binding(binding_path,bound,origin,runtime,modules,1)


def test_resealed_diagnostic_cannot_be_relabelled_as_success(tmp_path,monkeypatch):
    engine,api,bound,modules,origin,runtime,*_=setup(tmp_path,monkeypatch);sidecar=tmp_path/'sidecar';engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar)
    proofs=api.read(sidecar/'page-proofs.json');proofs[0]['status']='source_proved';api.save(sidecar/'page-proofs.json',proofs)
    receipt=api.read(sidecar/'reproof-version.json');receipt['artifacts_sha256']['page-proofs.json']=api.file_sha(sidecar/'page-proofs.json');api.save(sidecar/'reproof-version.json',receipt)
    with pytest.raises(ValueError,match='diagnostic cannot become'):engine.verify_region_reproof(sidecar,runtime,fresh=False)


def test_technical_failure_after_capture_retains_snapshot_and_terminal_absence_of_report(tmp_path,monkeypatch):
    engine,api,bound,modules,origin,runtime,snapshot,_,cells,_=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(cells,'validate_feedback',lambda *args:(_ for _ in ()).throw(RuntimeError('reader \udc65')))
    sidecar=tmp_path/'sidecar';receipt=engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar);proof=api.read(sidecar/'page-proofs.json')[0]
    assert receipt['technical_hold_pages']==[1]and proof['technical_error']['message_codepoints'][-1]==0xdc65
    from local_vector_batch.native_region_diagnostics import verify_link
    data=verify_link(proof['diagnostics'],sidecar,bound,origin,runtime,modules,1)
    assert data['snapshot']==snapshot and data['cells']['status']=='not_returned'


def test_existing_success_still_requires_full_fresh_source_proof(tmp_path,monkeypatch):
    engine,api,bound,modules,origin,runtime,snapshot,report,cells,regions=setup(tmp_path,monkeypatch,surrogate=False)
    report['status']='validated';monkeypatch.setattr(cells,'validate_feedback',lambda *args:copy.deepcopy(report))
    good=copy.deepcopy(bound['quality']['results'][0]);good.update(outcome='auto_accepted',page_complete=True,failed_components=[],missing_evidence=[])
    good['components']={k:{'state':'pass'}for k in api.COMPONENTS}
    monkeypatch.setattr(regions,'derive_regions',lambda *args:{'source_bound':'synthetic unit control'})
    monkeypatch.setattr(regions,'_quality_from_regions',lambda *args:copy.deepcopy(good))
    sidecar=tmp_path/'sidecar';engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar)
    proof=api.read(sidecar/'page-proofs.json')[0];assert proof['status']=='source_proved'and'diagnostics'not in proof
    result=engine.verify_region_reproof(sidecar,runtime,fresh=True)
    assert result['fresh_source_decoder_pages']==[1]and result['actual_fresh_registered_decoder_calls']==1
    assert result['target_pages'][0]['embedding_candidate_allowed']is True


@pytest.mark.parametrize('later_failure',['memory','interrupt','source_changed'])
def test_completed_failed_witness_is_durable_but_unsealed_after_later_abort(tmp_path,monkeypatch,later_failure):
    engine,api,bound,modules,origin,runtime,*_=setup(tmp_path,monkeypatch)
    bound['pages'][2]={**bound['pages'][1],'page':2}
    bound['quality']['results'].append({**bound['quality']['results'][0],'page':2})
    evaluate=engine._evaluate
    def evaluation(*args,**kwargs):
        if args[2]==2:
            if later_failure=='interrupt':raise KeyboardInterrupt('synthetic interruption')
            raise MemoryError('synthetic capacity failure')
        return evaluate(*args,**kwargs)
    monkeypatch.setattr(engine,'_evaluate',evaluation)
    if later_failure=='source_changed':
        monkeypatch.setattr(api,'verify_parent_unchanged',lambda b:(_ for _ in()).throw(ValueError('parent source changed')))
    sidecar=tmp_path/'sidecar'
    with pytest.raises({'memory':MemoryError,'interrupt':KeyboardInterrupt,'source_changed':ValueError}[later_failure]):
        engine.create_region_reproof(bound['path'],runtime,origin,[1]if later_failure=='source_changed'else[1,2],sidecar)
    folder=sidecar/'diagnostics/page-000001'
    assert (folder/'bindings.json').is_file()and(folder/'source-snapshot.json').is_file()and(folder/'cell-validation.json').is_file()
    assert api.read(folder/'bindings.json')['source_quality_clearance']is False
    assert not(sidecar/'reproof-version.json').exists()
    assert not(sidecar/'quality.json').exists()
    with pytest.raises(FileNotFoundError):engine.verify_region_reproof(sidecar,runtime,fresh=True)


def test_failed_capture_warning_is_sealed_and_does_not_approve(tmp_path,monkeypatch):
    import logging
    engine,api,bound,modules,origin,runtime,_,_,cells,_=setup(tmp_path,monkeypatch)
    def capture(*args):
        logging.getLogger('pypdf._cmap').warning('future font warning with source scope')
        raise RuntimeError('reader failure after warning')
    monkeypatch.setattr(cells,'capture_source',capture)
    sidecar=tmp_path/'sidecar';engine.create_region_reproof(bound['path'],runtime,origin,[1],sidecar)
    proof=api.read(sidecar/'page-proofs.json')[0]
    from local_vector_batch.native_source_warnings import verify
    warning=verify(proof['warning_provenance'],sidecar,bound['source_pdf_sha256'],1,bound['pages'][1]['text_sha256'],bound['profile']['profile_sha256'])
    assert warning['events'][0]['processing_stage']=='source_capture'
    assert proof['quality']==bound['quality']['results'][0]and proof['status']=='held'
    result=engine.verify_region_reproof(sidecar,runtime,fresh=False)
    assert result['newly_auto_accepted_pages']==[]and result['still_held_pages']==[1]
