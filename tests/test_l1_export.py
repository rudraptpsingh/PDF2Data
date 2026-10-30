import json
import pytest
from local_vector_batch.l1_export import export_processed
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
    with pytest.raises(ValueError,match='new file'):export_processed(root,output)

def test_export_refuses_corrupt_source_package(tmp_path):
    source=source_fixture(tmp_path);root=tmp_path/'packages';extract_document(source,root/'example')
    (root/'example/layout.jsonl').write_text('{}\n')
    with pytest.raises(ValueError):export_processed(root,tmp_path/'all-data.json')
    assert not (tmp_path/'all-data.json').exists()
