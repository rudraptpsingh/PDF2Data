"""Admission protocol controls; these fixtures do not measure reading accuracy."""
import json
import shutil

import pytest

from local_vector_batch.cached_l1 import admit
from local_vector_batch.evidence_package import create_package
from local_vector_batch.hashing import file_sha256
from local_vector_batch.legal_l1 import digest, rows, verify_document, write_json, write_rows


def legacy(tmp_path, text='Unreviewed cached literal', status='ready'):
    from pypdf import PdfWriter
    original = tmp_path/'original.pdf'
    writer = PdfWriter(); writer.add_blank_page(width=600, height=800); writer.write(original)
    sha = file_sha256(original); identity = 'pdf-'+sha[:24]
    package = tmp_path/'legacy'; package.mkdir()
    page = dict(id=identity+':p1', page=1, text=text, text_sha256=digest(text.encode()),
                method='pdf_text' if status == 'ready' else 'none', status=status,
                pdf_width=600, pdf_height=800)
    write_rows(package/'pages.jsonl', [page])
    write_rows(package/'embedding-input.jsonl', [dict(id=page['id'], text=text,
        metadata=dict(parent_document_id=identity, source_pdf_sha256=sha,
            page_text_sha256=page['text_sha256'], page=1, url='https://example.org/source.pdf'))] if text else [])
    write_json(package/'tables.json', [])
    for name in ('fact-candidates.jsonl', 'structure-candidates.jsonl'):
        write_rows(package/name, [])
    write_json(package/'report.json', dict(document_id=identity, title='Unreviewed cached source',
        source_url='https://example.org/source.pdf', source_pdf_sha256=sha, pages=1,
        page_status_counts={s:int(status == s) for s in ('ready','blank','review_required')},
        numeric_candidates=0, structure_candidates=0, tables=0))
    create_package(package)
    return package, original


def run(package, original, output, **overrides):
    options = dict(package=package, source_pdf=original, source_sha256=file_sha256(original),
        package_sha256=file_sha256(package/'evidence-package.json'), source_id='cached', family='notice', output=output)
    options.update(overrides)
    return admit(**options)


def reseal(output, name):
    path=output/'l1-package.json'; manifest=json.loads(path.read_text())
    manifest['artifacts'][name]=file_sha256(output/name); write_json(path,manifest)


def test_bytes_geometry_missing_proof_and_no_extraction(tmp_path, monkeypatch):
    package, original=legacy(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('admission must not extract or capture new source readings')
    monkeypatch.setattr('local_vector_batch.legal_l1.extract_pdf', forbidden)
    monkeypatch.setattr('local_vector_batch.legal_l1.capture_layout', forbidden)
    (package/'old-failed-receipt.json').write_text('{"status":"failed"}\n')
    output=tmp_path/'admitted'; result=run(package,original,output)
    assert result['integrity']=='passed' and not result['content_ready']
    assert result['quality_counts']==dict(auto_accepted=0,repair_required=1,source_blocked=0)
    assert not result['cached_reading_modified'] and not result['fresh_extraction_run']
    for path in package.rglob('*'):
        if path.is_file(): assert path.read_bytes()==(output/'extraction'/path.relative_to(package)).read_bytes()
    layout=rows(output/'layout.jsonl')[0]
    assert (layout['width'],layout['height'])==(600,800) and layout['glyphs']==[]
    assert verify_document(output)==result
    assert json.loads((output/'cached-reading.json').read_text())['missing_facets']
    with pytest.raises(ValueError,match='new immutable'):run(package,original,output)


@pytest.mark.parametrize('text,status,outcome', [('', 'blank', 'source_blocked'),
                                             ('Unresolved OCR reading', 'review_required', 'repair_required')])
def test_source_only_and_review_states_are_retained(tmp_path,text,status,outcome):
    package,original=legacy(tmp_path,text,status); output=tmp_path/'admitted'
    result=run(package,original,output)
    assert result['quality_counts'][outcome]==1 and not result['content_ready']
    assert rows(output/'extraction/pages.jsonl')[0]['status']==status
    assert rows(output/'repair-queue.jsonl')[0]['status']=='awaiting_source_proof'


@pytest.mark.parametrize('pin', ['source_sha256','package_sha256'])
def test_wrong_pins_publish_nothing(tmp_path,pin):
    package,original=legacy(tmp_path); output=tmp_path/'admitted'
    with pytest.raises(ValueError,match='expected SHA256'):run(package,original,output,**{pin:'0'*64})
    assert not output.exists()


@pytest.mark.parametrize('name', ['quality.json','layout.jsonl','cached-reading.json','source-receipt.json'])
def test_resealed_derivation_changes_refused(tmp_path,name):
    package,original=legacy(tmp_path);output=tmp_path/'admitted';run(package,original,output)
    path=output/name
    if name.endswith('.jsonl'):
        value=rows(path);value[0]['width']=42;write_rows(path,value)
    else:
        value=json.loads(path.read_text());value['source_quality_approval_granted']=True;write_json(path,value)
    reseal(output,name)
    with pytest.raises(ValueError,match='derivation mismatch'):verify_document(output)


def test_mutated_or_linked_legacy_refused(tmp_path):
    package,original=legacy(tmp_path);output=tmp_path/'admitted'
    with (package/'pages.jsonl').open('a') as stream:stream.write('\n')
    with pytest.raises(ValueError,match='hash mismatch'):run(package,original,output)
    assert not output.exists()


def test_extra_retained_artifact_changes_refused(tmp_path):
    package,original=legacy(tmp_path);(package/'failed.json').write_text('{}')
    output=tmp_path/'admitted';run(package,original,output)
    (output/'extraction/failed.json').write_text('{"changed":true}')
    with pytest.raises(ValueError,match='pinned inventory'):verify_document(output)


def test_cached_table_geometry_and_missing_dimensions(tmp_path):
    package,original=legacy(tmp_path)
    page=rows(package/'pages.jsonl')[0]
    del page['pdf_width'];del page['pdf_height'];write_rows(package/'pages.jsonl',[page])
    write_json(package/'tables.json',[dict(id=page['id']+':table1',page=1,cells=[['Cached cell']],bbox=[1,2,30,40])])
    report=json.loads((package/'report.json').read_text());report['tables']=1;write_json(package/'report.json',report)
    create_package(package)
    output=tmp_path/'admitted';run(package,original,output)
    assert (package/'tables.json').read_bytes()==(output/'extraction/tables.json').read_bytes()
    layout=rows(output/'layout.jsonl')[0]
    assert layout['width'] is None and layout['height'] is None and layout['geometry_status']=='unavailable'


def test_changed_reading_has_separate_identity(tmp_path):
    package,original=legacy(tmp_path);first=tmp_path/'first';run(package,original,first)
    changed=tmp_path/'changed';shutil.copytree(package,changed)
    page=rows(changed/'pages.jsonl')[0];page['text']='A separately retained reading'
    page['text_sha256']=digest(page['text'].encode());write_rows(changed/'pages.jsonl',[page])
    inputs=rows(changed/'embedding-input.jsonl');inputs[0]['text']=page['text']
    inputs[0]['metadata']['page_text_sha256']=page['text_sha256'];write_rows(changed/'embedding-input.jsonl',inputs)
    create_package(changed)
    second=tmp_path/'second';run(changed,original,second)
    identities=[json.loads((p/'cached-reading.json').read_text())['selected_reading_sha256']for p in (first,second)]
    assert identities[0]!=identities[1]
    assert rows(first/'extraction/pages.jsonl')[0]['text']!=page['text']


def test_linked_optional_evidence_refused(tmp_path):
    package,original=legacy(tmp_path);(package/'linked.pdf').symlink_to(original)
    with pytest.raises(ValueError,match='regular files'):run(package,original,tmp_path/'admitted')


@pytest.mark.parametrize('mutation', ['change_retained','add_file'])
def test_verification_session_guards_entire_cached_inventory(tmp_path,mutation):
    from local_vector_batch.native_verification_session import VerificationSession
    package,original=legacy(tmp_path);(package/'old-failed.json').write_text('{}')
    output=tmp_path/'admitted';run(package,original,output)
    with VerificationSession() as session:
        verify_document(output);verify_document(output)
        assert session.stats['full_verifications']==1 and session.stats['cache_hits']==1
        name='old-failed.json'if mutation=='change_retained'else'new-unsealed.json'
        (output/'extraction'/name).write_text('{"changed":true}')
        with pytest.raises(ValueError,match='pinned inventory'):verify_document(output)
