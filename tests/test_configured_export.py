import json
from pathlib import Path
import pytest
from test_legal_l1 import source_fixture
from local_vector_batch.legal_l1 import extract_document
from local_vector_batch.cli import main
from local_vector_batch.field_rules import extract,query,evaluate
from local_vector_batch.mcp_server import Bridge


def config(tmp_path):
    p=tmp_path/'rules.json'
    p.write_text(json.dumps({'schema_version':'pdf2data.extraction-rules.v1','id':'proceedings','version':'1','families':['record_of_proceedings','generic'],
        'fields':[{'id':'reply_mention','type':'text','pattern':r'(?P<value>reply)'},{'id':'no_tax','type':'quantity','pattern':r'tax (?P<value>\d+)'}]}))
    return p


def test_cli_rules_extract_query_evaluate_with_positive_negative_and_stale_references(tmp_path,capsys):
    source=source_fixture(tmp_path);package=tmp_path/'l1';extract_document(source,package)
    rules=config(tmp_path);bundle=tmp_path/'fields'
    assert main(['fields','extract','--package',str(package),'--rules',str(rules),'--output',str(bundle),'--json'])==0
    assert json.loads(capsys.readouterr().out)['observations']==1
    data=json.loads((bundle/'fields.json').read_text());o=data['observations'][0]
    from local_vector_batch.hashing import file_sha256
    assert (bundle/'rules.json').read_bytes()==rules.read_bytes()
    assert data['rules_sha256']==file_sha256(bundle/'rules.json')
    ref={'source_pdf_sha256':data['source_pdf_sha256'],'rules_sha256':data['rules_sha256'],'reviewer':'synthetic PDF fixture',
         'reference_scope':'development_fixture','fields':[{'field_id':'reply_mention','expected':[dict(page=1,page_text_sha256=o['source_span']['page_text_sha256'],char_start=o['source_span']['char_start'],exact_quote='reply')]},{'field_id':'no_tax','expected':[]}]}
    refs=tmp_path/'refs.json';refs.write_text(json.dumps(ref))
    assert main(['fields','evaluate','--bundle',str(bundle),'--references',str(refs),'--output',str(tmp_path/'evaluation'),'--json'])==0
    report=json.loads(capsys.readouterr().out);assert report['precision']==report['recall']==1 and report['unlabelled_fields']==0
    ref['fields'][0]['expected'][0]['typed_candidate']='an invented normalization'
    refs.write_text(json.dumps(ref));typed=evaluate(bundle,refs,tmp_path/'typed-mismatch')
    assert not typed['passed']and typed['results'][0]['normalization_mismatches']
    del ref['fields'][0]['expected'][0]['typed_candidate']
    ref['fields'][0]['expected']=[];refs.write_text(json.dumps(ref))
    assert main(['fields','evaluate','--bundle',str(bundle),'--references',str(refs),'--output',str(tmp_path/'wrong-reference'),'--json'])==3
    assert json.loads(capsys.readouterr().out)['false_positive']==1
    ref['rules_sha256']='b'*64;refs.write_text(json.dumps(ref))
    with pytest.raises(ValueError,match='version differs'):evaluate(bundle,refs,tmp_path/'stale')
    assert query([bundle],field='reply_mention')['total']==1
    (bundle/'fields.json').write_text('{}')
    with pytest.raises(ValueError,match='changed'):query(bundle)


def test_rules_reach_workspace_exports_and_mcp_without_new_tool_code_per_field(tmp_path,monkeypatch):
    rules=config(tmp_path);monkeypatch.setenv('PDF2DATA_RULES',str(rules));source=source_fixture(tmp_path)
    bridge=Bridge(tmp_path/'workspace',[tmp_path])
    try:
        run=bridge.parse(source['path'],ocr='off');bridge.workspace.executor.shutdown(wait=True)
        result=bridge.read(run['run_id'],artifact='fields',page=1,max_chars=200000)
        assert result['data']['observations'][0]['field_id']=='reply_mention'
        assert result['data']['observations'][0]['value_literal']=='reply'
        assert not result['content_verified']
        path=bridge.workspace.directory(run['run_id'])/'readable/configured-fields/fields.json'
        path.write_text('{}')
        with pytest.raises(ValueError,match='changed'):bridge.read(run['run_id'],artifact='fields')
    finally:bridge.close()


def test_rule_worker_timeout_does_not_publish_partial_results(tmp_path):
    import fitz
    p=tmp_path/'repeated.pdf';pdf=fitz.open();page=pdf.new_page(width=600,height=800);page.insert_text((30,50),'a'*120+'!');pdf.save(p);pdf.close()
    from local_vector_batch.hashing import file_sha256
    package=tmp_path/'l1';extract_document({'id':'repeated','path':str(p),'sha256':file_sha256(p),'family':'generic'},package)
    rules=tmp_path/'slow.json';rules.write_text(json.dumps({'schema_version':'pdf2data.extraction-rules.v1','id':'slow','version':'1','fields':[{'id':'x','type':'text','pattern':r'(?P<value>(a+)+)$'}]}))
    with pytest.raises(ValueError,match='time budget'):extract(package,rules,tmp_path/'result',timeout=1)
    assert not(tmp_path/'result').exists()
