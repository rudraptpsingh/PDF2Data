import json
from pathlib import Path
import pytest
from test_document_metadata import data
from local_vector_batch.document_profiles import profile_documents


def package(root,name,text,sha):
    p=root/name;(p/'extraction').mkdir(parents=True)
    d=data([(text,100,False)],'draft');d['source_pdf_sha256']=sha
    (p/'source-receipt.json').write_text(json.dumps(dict(actual_sha256=sha,declared=dict(id=name,family='draft'))))
    (p/'extraction/pages.jsonl').write_text(json.dumps(d['pages'][0])+'\n')
    (p/'layout.jsonl').write_text(json.dumps(d['layout'][0])+'\n')
    (p/'structure.json').write_text(json.dumps(d['structure']))
    (p/'quality.json').write_text(json.dumps(dict(results=[dict(outcome='auto_accepted')])))
    return p


def test_profile_counts_languages_and_unique_documents_without_training_rules(tmp_path,monkeypatch):
    from local_vector_batch import legal_l1
    verified=[];monkeypatch.setattr(legal_l1,'verify_document',lambda p:verified.append(p))
    a=package(tmp_path,'a','No. L-1/2026 Dated: 26 October 2026','a'*64)
    b=package(tmp_path,'b','सं. एल-१/२०२६ दिनांक: २६ अक्टूबर २०२६','b'*64)
    manifest=tmp_path/'input.json';manifest.write_text(json.dumps(dict(schema_version='pdf2data.profile-input.v1',packages=[dict(package=str(a)),dict(package=str(a)),dict(package=str(b))])))
    out=tmp_path/'out';result=profile_documents(manifest,out);report=json.loads((out/'profiles.json').read_text())
    assert result['unique_documents']==2 and result['pages']==2 and len(verified)==3
    assert len(report['duplicate_sources_skipped'])==1
    dated=report['by_document_type'][0]['keywords']['dated']
    assert dated['en']['documents_with_keyword']==dated['hi']['documents_with_keyword']==1
    assert dated['en']['documents_in_type']==2
    assert 'no automatic rule training' in report['learning_policy']
    assert len(list((out/'metadata').glob('*.json')))==2
    with pytest.raises(ValueError,match='new directory'):profile_documents(manifest,out)


def test_failed_source_verification_creates_no_profile_outputs(tmp_path,monkeypatch):
    from local_vector_batch import legal_l1
    def reject(p):raise ValueError('changed source PDF')
    monkeypatch.setattr(legal_l1,'verify_document',reject)
    m=tmp_path/'in.json';m.write_text(json.dumps(dict(schema_version='pdf2data.profile-input.v1',packages=[dict(package=str(tmp_path/'source'))])))
    with pytest.raises(ValueError,match='changed source PDF'):profile_documents(m,tmp_path/'out')
    assert not (tmp_path/'out').exists()
