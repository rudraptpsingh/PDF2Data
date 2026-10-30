import json,time
from pathlib import Path
import pytest
from local_vector_batch.workspace import Workspace
from test_legal_l1 import source_fixture

def test_real_cli_pipeline_persists_artifacts_and_flags(tmp_path):
    source=source_fixture(tmp_path);workspace=Workspace(tmp_path/'workspace')
    try:
        job=workspace.submit(Path(source['path']),{'title':'Example CERC record','family':'record_of_proceedings','ocr':'off','url':source['url']})
        workspace.executor.shutdown(wait=True)
        final=workspace.get(job['id'])
        assert final['status'] in ('processed','processed_with_flags')
        assert [s['status'] for s in final['steps']]==['completed']*4
        assert final['metrics']['pages']==2 and final['metrics']['withheld_pages']==0
        assert final['outputs']['document.md'].endswith('/files/document.md')
        for step in final['steps'][1:]:
            assert step['command'][0]=='pdf2data'
            assert step['exit_code']==0
        assert (workspace.directory(job['id'])/'readable/document.html').is_file()
        assert final['publication_eligible'] is False
        restored=Workspace(tmp_path/'workspace')
        try:assert restored.get(job['id'])['outputs']==final['outputs']
        finally:restored.executor.shutdown(wait=True)
    finally:workspace.executor.shutdown(wait=True)

def test_restart_exposes_interruption_and_retry_preserves_original(tmp_path,monkeypatch):
    source=source_fixture(tmp_path);workspace=Workspace(tmp_path/'workspace')
    monkeypatch.setattr(workspace.executor,'submit',lambda *args:None)
    job=workspace.submit(Path(source['path']),{'title':'Queued record','ocr':'off'})
    workspace.executor.shutdown(wait=True)
    restored=Workspace(tmp_path/'workspace');monkeypatch.setattr(restored.executor,'submit',lambda *args:None)
    try:
        assert restored.get(job['id'])['status']=='interrupted'
        new=restored.retry(job['id'])
        assert new['retry_of']==job['id'] and new['id']!=job['id']
        assert restored.get(job['id'])['status']=='interrupted'
    finally:restored.executor.shutdown(wait=True)

def test_invalid_source_and_metadata_never_start_processing(tmp_path):
    workspace=Workspace(tmp_path/'workspace');bad=tmp_path/'fake.pdf';bad.write_text('not a pdf')
    try:
        with pytest.raises(ValueError,match='not a PDF'):workspace.submit(bad,{'title':'Bad'})
        with pytest.raises(ValueError,match='title'):workspace.submit(bad,{'title':''})
        assert workspace.jobs()==[]
        with pytest.raises(ValueError,match='identity'):workspace.get('../other')
    finally:workspace.executor.shutdown(wait=True)

def test_cli_failure_is_visible_without_readable_output(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=source_fixture(tmp_path);workspace=Workspace(tmp_path/'workspace')
    monkeypatch.setattr('local_vector_batch.workspace.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=2,stdout='',stderr='OCR engine unavailable'))
    try:
        job=workspace.submit(Path(source['path']),{'title':'Failure example','ocr':'off'})
        workspace.executor.shutdown(wait=True);final=workspace.get(job['id'])
        assert final['status']=='failed' and final['outputs'] is None
        assert final['steps'][1]['status']=='failed'
        assert final['steps'][2]['status']=='pending'
        assert 'unavailable' in final['error']
    finally:workspace.executor.shutdown(wait=True)
