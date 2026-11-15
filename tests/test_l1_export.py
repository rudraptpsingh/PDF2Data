import json
import pytest
from local_vector_batch.l1_export import export_processed, export_table_contexts, verify_table_contexts
from local_vector_batch.legal_l1 import extract_document
from test_legal_l1 import source_fixture

def test_export_retains_every_page_raw_layout_and_unreviewed_status(tmp_path):
    source=source_fixture(tmp_path);root=tmp_path/'packages';extract_document(source,root/'example')
    output=tmp_path/'all-data.json';result=export_processed(root,output);packet=json.loads(output.read_text())
    assert result['pages']==2 and result['withheld_pages']==0
    assert packet['publication_eligible'] is False
    doc=packet['documents'][0]
    assert len(doc['pages'])==len(doc['layout'])==len(doc['quality']['results'])==2
    assert all('glyphs' in page for page in doc['layout'])
    assert len(doc['structure']['units'])>0
    assert doc['candidate_inventory']['artifacts']['fact_candidates']['count']==len(doc['fact_candidates'])
    assert doc['candidate_inventory']['artifacts']['structure_candidates']['count']==len(doc['structure_candidates'])
    assert [s['source_quality'] for s in doc['page_states']]==[q['outcome'] for q in doc['quality']['results']]
    with pytest.raises(ValueError,match='new file'):export_processed(root,output)

def test_export_refuses_corrupt_source_package(tmp_path):
    source=source_fixture(tmp_path);root=tmp_path/'packages';extract_document(source,root/'example')
    (root/'example/layout.jsonl').write_text('{}\n')
    with pytest.raises(ValueError):export_processed(root,tmp_path/'all-data.json')
    assert not (tmp_path/'all-data.json').exists()

def test_package_bound_context_export_preserves_raw_sources(tmp_path):
    source=source_fixture(tmp_path);root=tmp_path/'packages';package=root/'example'
    extract_document(source,package)
    before=json.loads((package/'extraction/tables.json').read_text())
    out=tmp_path/'all.json';export_processed(root,out,document_model=True)
    doc=json.loads(out.read_text())['documents'][0]
    assert doc['tables']==before and doc['legal_state']=='unreviewed'
    assert doc['publication_eligible'] is False and 'continuations' in doc['document_model']
    result=export_table_contexts(out,'example',tmp_path/'contexts')
    assert json.loads((tmp_path/'contexts/data.json').read_text())==doc
    assert result['package_sha256']==doc['package_sha256']
    assert verify_table_contexts(out,tmp_path/'contexts')['status']=='verified'
    with pytest.raises(ValueError,match='new output'):
        export_table_contexts(out,'example',tmp_path/'contexts')
    with pytest.raises(ValueError,match='exactly one'):
        export_table_contexts(out,'missing',tmp_path/'missing')
    old=tmp_path/'old.json';export_processed(root,old)
    with pytest.raises(ValueError,match='document model'):
        export_table_contexts(old,'example',tmp_path/'old-context')
    with pytest.raises(ValueError,match='identity'):
        verify_table_contexts(old,tmp_path/'contexts')
    sidecar=tmp_path/'contexts/table-contexts.jsonl';sidecar.write_bytes(sidecar.read_bytes()+b' ')
    with pytest.raises(ValueError,match='differs'):
        verify_table_contexts(out,tmp_path/'contexts')
