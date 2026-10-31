from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from test_legal_l1 import source_fixture
from local_vector_batch.legal_l1 import extract_document
from local_vector_batch import control_loop as control


def fixture(tmp_path,parsed=True):
    source=source_fixture(tmp_path);package=tmp_path/'l1'
    if parsed:extract_document(source,package)
    row=dict(source_pdf=source['path'],source_pdf_sha256=source['sha256'])
    if parsed:row['package']=str(package)
    manifest=tmp_path/'inventory.jsonl';manifest.write_text(json.dumps(row)+'\n')
    state=tmp_path/'controller';control.initialize(manifest,state)
    return source,package/'extraction',state


def report(package,source,fail=True):
    return dict(source_pdf_sha256=source,results=[dict(page=p['page'],source_pdf_sha256=source,page_text_sha256=p['text_sha256'],
        outcome='repair_required'if fail and p['page']==1 else 'auto_accepted',
        components=dict(source_identity=dict(state='pass'),ordered_numbers=dict(state='fail'if fail and p['page']==1 else 'pass')),
        failed_components=['ordered_numbers']if fail and p['page']==1 else [],missing_evidence=[])
        for p in control.rows(package/'pages.jsonl')])


def test_unknown_errors_are_not_zero_and_review_labels_do_not_replace_machine_gates():
    raw=dict(results=[dict(page=1,source_pdf_sha256='a'*64,page_text_sha256='b'*64,outcome='repair_required',
        components=dict(numbers=dict(state='fail'),geometry=dict(state='unknown'),text=dict(state='pass')))])
    measurement=control.residuals(raw,[])
    assert measurement['loss_vector']==[1,0,1]
    assert measurement['pages'][0]['gate_residuals']['geometry']['error']is None
    assert measurement['pages'][0]['source_review_error']is None
    assert measurement['source_review_unlabelled_pages']==1


def test_cell_feedback_measures_the_literal_and_does_not_count_as_whole_page_review():
    box=[10,20,30,40]
    raw=dict(results=[dict(page=1,source_pdf_sha256='a'*64,page_text_sha256='b'*64,outcome='auto_accepted',components={'text':{'state':'pass'}})])
    ref=dict(scope='cell',source_pdf_sha256='a'*64,page=1,page_text_sha256='b'*64,source_bbox=box,reference_text='Not effective')
    tables=[dict(page=1,cell_spans=[dict(bbox=box,text='Effective')])]
    before=control.residuals(raw,[ref],tables)
    assert before['loss_vector']==[0,1,0]and before['source_reviewed_pages']==0
    assert before['source_reviewed_cells']==1
    tables[0]['cell_spans'][0]['text']='Not effective'
    after=control.residuals(raw,[ref],tables)
    assert after['pages'][0]['cell_reference_residuals'][0]['error']==0
    assert control.non_regressing(before,after)
    tables[0]['cell_spans'][0]['bbox']=[100,200,300,400]
    assert not control.non_regressing(after,control.residuals(raw,[ref],tables))


def test_order_only_disagreement_is_distinguished_from_lost_numbers():
    raw=dict(results=[dict(page=1,source_pdf_sha256='a'*64,page_text_sha256='b'*64,outcome='repair_required',components={
        'ordered_numbers':dict(state='fail',selected=['2026','51%'],independent=['51%','2026']),
        'words_and_order':dict(state='fail',word_recall=.95,word_precision=1,word_order=.8)})])
    page=control.residuals(raw,[])['pages'][0]
    assert page['gate_residuals']['ordered_numbers']['same_token_multiset']is True
    assert page['gate_residuals']['ordered_numbers']['token_differences']
    assert page['gate_residuals']['words_and_order']['measured_residuals']['word_recall_loss']==pytest.approx(.05)


@pytest.mark.parametrize('mutation',['known_to_unknown','new_failure','lost_page','lost_review','no_change'])
def test_global_improvement_cannot_hide_local_regression_or_discarded_review(mutation):
    before=dict(pages=[dict(page=1,page_text_sha256='a'*64,source_review_error=0,
        gate_residuals=dict(numbers=dict(observed='fail'),geometry=dict(observed='pass')))])
    after=deepcopy(before);after['pages'][0]['gate_residuals']['numbers']['observed']='pass'
    if mutation=='known_to_unknown':after['pages'][0]['gate_residuals']['numbers']['observed']='unknown'
    if mutation=='new_failure':after['pages'][0]['gate_residuals']['geometry']['observed']='fail'
    if mutation=='lost_page':after['pages']=[]
    if mutation=='lost_review':after['pages'][0]['source_review_error']=None
    if mutation=='no_change':after=deepcopy(before)
    assert not control.non_regressing(before,after)
    after=deepcopy(before);after['pages'][0]['gate_residuals']['numbers']['observed']='pass'
    assert control.non_regressing(before,after)


def test_init_deduplicates_byte_identical_sources_and_status_is_readable_during_work(tmp_path):
    source,package,state=fixture(tmp_path)
    manifest=tmp_path/'again.jsonl';manifest.write_text(json.dumps(dict(source_pdf=source['path'],package=str(package.parent),sha256=source['sha256']))+'\n')
    assert control.initialize(manifest,state)['documents']==1
    with control.ledger(state)as(db,_):
        db.execute("UPDATE documents SET state='processing'");db.commit()
        assert control.status(state)['states']==dict(processing=1)


def test_staged_repair_is_remeasured_and_original_remains_immutable(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path);original=(package/'pages.jsonl').read_bytes();calls=[]
    def command(args,folder,label):
        calls.append(label)
        if label=='quality':
            selected=Path(args[args.index('--package')+1]);target=Path(args[args.index('--output')+1])
            control.write(target,report(selected,source['sha256'],fail=selected==package));return {}
        candidate=folder/'candidate';shutil.copytree(package,candidate)
        return dict(results=[dict(status='ready_for_publication',candidate_package=str(candidate))])
    monkeypatch.setattr(control,'command',command)
    result=control.run(state,max_rounds=2)
    assert calls==['quality','repair','quality']
    assert result['states']==dict(extraction_checks_passed=1)
    assert result['source_review_complete_documents']==0
    assert (package/'pages.jsonl').read_bytes()==original
    assert control.run(state,max_rounds=2)['documents_processed_this_invocation']==0
    assert calls==['quality','repair','quality']


def test_no_available_repair_does_not_spin_or_hide_unlabelled_pages(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path);calls=[]
    def command(args,folder,label):
        calls.append(label)
        if label=='quality':control.write(Path(args[args.index('--output')+1]),report(package,source['sha256']));return {}
        return dict(results=[dict(status='awaiting_evidence')])
    monkeypatch.setattr(control,'command',command)
    result=control.run(state,max_rounds=5)
    assert result['states']==dict(needs_source_review=1)and len(calls)==2
    assert result['source_review_unlabelled_pages']==2
    todo=control.tasks(state,limit=1)
    assert todo['returned']==1 and todo['tasks'][0]['page']==1
    assert control.tasks(state,limit=1,offset=1)['returned']==0
    artifact=Path(todo['tasks'][0]['measurement_artifact']);artifact.write_text('{}')
    with pytest.raises(ValueError,match='artifact changed'):control.tasks(state)


def test_partial_quality_receipt_cannot_claim_a_complete_document(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path)
    def command(args,folder,label):
        partial=report(package,source['sha256'],fail=False);partial['results']=partial['results'][:1]
        control.write(Path(args[args.index('--output')+1]),partial);return {}
    monkeypatch.setattr(control,'command',command)
    assert control.run(state)['states']==dict(source_blocked=1)


def test_feedback_is_source_and_version_bound_and_cannot_grant_approval(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path);page=next(control.rows(package/'pages.jsonl'))
    reference=dict(source_pdf_sha256=source['sha256'],page=1,page_text_sha256=page['text_sha256'],
        acceptable=False,split='calibration',visual_evidence_sha256=source['sha256'],reviewer='test reviewer',
        visual_evidence_kind='original_pdf',scope='whole_page')
    path=tmp_path/'feedback.jsonl';path.write_text(json.dumps(reference)+'\n')
    assert control.add_feedback(state,path)['publication_eligible']is False
    def command(args,folder,label):
        assert label=='quality'
        control.write(Path(args[args.index('--output')+1]),report(package,source['sha256'],fail=False));return {}
    monkeypatch.setattr(control,'command',command)
    result=control.run(state)
    assert result['states']==dict(needs_source_review=1)
    assert result['source_reviewed_pages']==1 and not result['legal_review_approved']
    revised={**reference,'acceptable':True,'supersedes_feedback_sha256':control.digest(reference),'review_reason':'Rechecked the original page'}
    path.write_text(json.dumps(revised)+'\n');control.add_feedback(state,path)
    with control.ledger(state)as(db,_):assert db.execute('SELECT count(*)FROM feedback_history').fetchone()[0]==2
    reference['page_text_sha256']='d'*64;path.write_text(json.dumps(reference)+'\n')
    with pytest.raises(ValueError,match='stale'):control.add_feedback(state,path)


def test_unbound_archive_sources_are_visible_but_not_silently_queued(tmp_path):
    source=source_fixture(tmp_path);manifest=tmp_path/'unbound.jsonl';state=tmp_path/'controller'
    manifest.write_text(json.dumps(dict(source_pdf=source['path'],source_receipt_verified=False))+'\n')
    control.initialize(manifest,state)
    assert control.run(state)['states']==dict(source_binding_required=1)
    assert control.status(state)['attempts']==0


def test_source_mutation_is_detected_even_when_previous_attempt_is_cached(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path)
    def command(args,folder,label):
        control.write(Path(args[args.index('--output')+1]),report(package,source['sha256'],fail=False));return {}
    monkeypatch.setattr(control,'command',command)
    assert control.run(state)['states']==dict(extraction_checks_passed=1)
    Path(source['path']).write_bytes(b'%PDF-changed')
    assert control.run(state)['states']==dict(source_blocked=1)


def test_real_cli_parses_measures_persists_and_resumes_an_original_pdf(tmp_path):
    source,package,state=fixture(tmp_path,parsed=False)
    result=control.run(state,max_rounds=1,max_documents=1,ocr='off')
    assert result['documents']==result['quality_measured_documents']==1
    assert result['source_review_complete_documents']==0
    assert list(state.glob('attempts/*/parse-command.json'))
    assert list(state.glob('attempts/*/quality-command.json'))
    assert control.run(state,max_rounds=1,max_documents=1,ocr='off')['documents_processed_this_invocation']==0


def test_one_processing_failure_does_not_block_the_remaining_inventory(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path)
    extra=tmp_path/'extra';extra.mkdir();other=source_fixture(extra)
    path=Path(other['path']);path.write_bytes(path.read_bytes()+b'\n% different original bytes\n')
    other['sha256']=sha256(path.read_bytes()).hexdigest();other['id']='other'
    other_package=extra/'l1';extract_document(other,other_package)
    manifest=extra/'inventory.jsonl';manifest.write_text(json.dumps(dict(source_pdf=other['path'],sha256=other['sha256'],package=str(other_package)))+'\n')
    control.initialize(manifest,state)
    def command(args,folder,label):
        selected=Path(args[args.index('--package')+1])
        if selected==package:raise RuntimeError('test reader failure')
        control.write(Path(args[args.index('--output')+1]),report(selected,other['sha256'],fail=False));return {}
    monkeypatch.setattr(control,'command',command)
    result=control.run(state,max_documents=2,max_rounds=1)
    assert result['documents_processed_this_invocation']==2
    assert result['states']==dict(processing_failed=1,extraction_checks_passed=1)


def test_status_does_not_decode_full_document_diagnostics(tmp_path,monkeypatch):
    source,package,state=fixture(tmp_path)
    with control.ledger(state)as(db,_):
        control._save_metrics(db,source['sha256'],dict(state='needs_source_review',measurement=dict(pages=[],source_reviewed_pages=0)),None,None)
        db.execute('UPDATE documents SET last_result=?',('not-loaded-full-diagnostics',));db.commit()
    monkeypatch.setattr(control.json,'loads',lambda *_: (_ for _ in()).throw(AssertionError('corpus JSON loaded')))
    assert control.status(state)['quality_measured_documents']==1
