import copy,json,logging,threading,warnings
from pathlib import Path
import pytest
from local_vector_batch.native_source_warnings import SourceWarnings,stage,verify,MAX_EVENTS,MAX_MESSAGE_CODEPOINTS


def observer(tmp_path,page=1):return SourceWarnings(tmp_path,'a'*64,page,'b'*64,'c'*64)


def test_python_and_parser_log_warnings_are_bound_and_still_emitted(tmp_path,capsys,caplog):
    previous=warnings.showwarning;filters=copy.deepcopy(warnings.filters)
    # Use a real original showwarning function: pytest intercepts warning output.
    original=[]
    warnings.showwarning=lambda *a,**k:original.append(str(a[0]))
    forwarding=warnings.showwarning
    try:
        with observer(tmp_path)as o:
            stage('source_capture');warnings.warn('font cmap missing',UserWarning)
            stage('validate_cells');logging.getLogger('pypdf._cmap').warning('font warning 469')
        d=verify(o.link,tmp_path,'a'*64,1,'b'*64,'c'*64)
        assert [e['processing_stage']for e in d['events']]==['source_capture','validate_cells']
        assert original==['font cmap missing']and'font warning 469'in caplog.text
        assert warnings.showwarning is forwarding and warnings.filters==filters
        assert d['source_quality_clearance']is False and d['candidate_only']is True
    finally:warnings.showwarning=previous


def test_other_thread_logger_and_sibling_page_are_not_misattributed(tmp_path):
    with observer(tmp_path,1)as a:
        t=threading.Thread(target=lambda:logging.getLogger('pypdf').warning('other thread'))
        t.start();t.join()
        logging.getLogger('unrelated.application').warning('unrelated logger')
        logging.getLogger('pypdf').warning('page one')
    with observer(tmp_path,2)as b:logging.getLogger('pdfminer').warning('page two')
    da=verify(a.link,tmp_path,'a'*64,1,'b'*64,'c'*64);db=verify(b.link,tmp_path,'a'*64,2,'b'*64,'c'*64)
    assert [e['message']for e in da['events']]==['page one']
    assert [e['message']for e in db['events']]==['page two']


def test_exception_restores_handlers_and_persists_bounded_candidate(tmp_path):
    previous=warnings.showwarning;handlers=list(logging.getLogger().handlers);filters=copy.deepcopy(warnings.filters)
    with pytest.raises(RuntimeError,match='actual reader exception'):
        with observer(tmp_path)as o:
            stage('derive_regions')
            for _ in range(MAX_EVENTS+3):logging.getLogger('pypdf').warning('x'*(MAX_MESSAGE_CODEPOINTS+3))
            raise RuntimeError('actual reader exception')
    assert warnings.showwarning is previous and warnings.filters==filters and logging.getLogger().handlers==handlers
    d=verify(o.link,tmp_path,'a'*64,1,'b'*64,'c'*64)
    assert len(d['events'])==MAX_EVENTS and d['dropped_events']==3
    assert d['events'][0]['message_truncated']is True
    assert d['events'][0]['message_codepoints']==MAX_MESSAGE_CODEPOINTS+3
    assert d['source_quality_clearance']is False


@pytest.mark.parametrize('attack',['hash','source','profile','page','authority','escape'])
def test_warning_links_fail_closed(tmp_path,attack):
    with observer(tmp_path)as o:logging.getLogger('pypdf').warning('bound')
    link=copy.deepcopy(o.link);file=tmp_path/link['path'];d=json.loads(file.read_text())
    if attack=='hash':file.write_text('{}')
    elif attack=='escape':link['path']='../other.json'
    else:
        key={'source':'source_pdf_sha256','profile':'proof_profile_sha256','page':'page','authority':'source_quality_clearance'}[attack]
        d[key]={'source':'d'*64,'profile':'d'*64,'page':True,'authority':True}[attack]
        file.write_text(json.dumps(d));import hashlib;link['sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(ValueError):verify(link,tmp_path,'a'*64,1,'b'*64,'c'*64)


def test_no_emitted_warnings_create_no_receipt(tmp_path):
    with observer(tmp_path)as o:pass
    assert o.link is None and not(tmp_path/'warning-provenance').exists()


def test_concurrent_owned_scopes_restore_global_hook_and_keep_page_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    previous=warnings.showwarning;handlers=list(logging.getLogger().handlers)
    def run(page):
        with observer(tmp_path,page)as o:logging.getLogger('pypdf').warning('page %d',page)
        return verify(o.link,tmp_path,'a'*64,page,'b'*64,'c'*64)
    with ThreadPoolExecutor(max_workers=2)as pool:documents=list(pool.map(run,range(1,5)))
    assert warnings.showwarning is previous and logging.getLogger().handlers==handlers
    assert all([e['message']for e in d['events']]==['page '+str(d['page'])]for d in documents)
