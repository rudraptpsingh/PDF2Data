from pathlib import Path
import json,copy
import pytest
from local_vector_batch.benchmark import prepare,load,write,sha,validate_suite,page_body,extract_case,compare


def fixture_suite(tmp_path):
    from pypdf import PdfWriter
    root=tmp_path/'dataset';(root/'pdfs').mkdir(parents=True)
    for name in ('a.pdf','b.pdf'):
        pdf=PdfWriter();pdf.add_blank_page(width=100,height=100)
        with (root/'pdfs'/name).open('wb') as f:pdf.write(f)
    rules=[{'id':'a','pdf':'a.pdf','page':1,'type':'present','text':'Alpha'}, {'id':'b','pdf':'b.pdf','page':1,'type':'absent','text':'Ghost'}]
    (root/'text.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rules))
    return root


def test_custom_fixture_cannot_be_mislabelled_as_public_dataset(tmp_path):
    root=fixture_suite(tmp_path)
    with pytest.raises(ValueError,match='categories'):prepare(root,tmp_path/'invalid')
    receipt=prepare(root,tmp_path/'suite',custom=True)
    suite=load(Path(receipt['output']))
    assert suite['dataset']=='custom_local' and suite['dataset_revision'] is None and not suite['complete_public_dataset']
    validate_suite(suite)


def test_hash_freeze_rejects_source_and_annotation_mutation(tmp_path):
    root=fixture_suite(tmp_path);prepare(root,tmp_path/'suite',custom=True);suite=load(tmp_path/'suite/suite.json')
    tampered=copy.deepcopy(suite);tampered['rules'][0]['rule']['text']='Different'
    with pytest.raises(ValueError,match='suite hash'):validate_suite(tampered)
    (root/'pdfs/a.pdf').write_bytes(b'changed')
    with pytest.raises(ValueError,match='source changed'):validate_suite(suite)


def test_selection_is_by_seeded_filename_not_label_contents(tmp_path):
    root=fixture_suite(tmp_path);prepare(root,tmp_path/'one',1,custom=True)
    text=(root/'text.jsonl').read_text().replace('Alpha','Completely different annotation');(root/'text.jsonl').write_text(text)
    prepare(root,tmp_path/'two',1,custom=True)
    assert [c['pdf'] for c in load(tmp_path/'one/suite.json')['cases']]==[c['pdf'] for c in load(tmp_path/'two/suite.json')['cases']]


def test_adapter_contains_only_selected_page_and_its_tables():
    page={'page':1,'text':'Actual source body','title':'GHOST LABEL','facts':['GHOST LABEL']}
    tables=[{'page':1,'cells':[['GNA','Not effective']]},{'page':2,'cells':[['OTHER PAGE SECRET']]}]
    body=page_body(page,tables)
    assert 'Actual source body' in body and 'Not effective' in body
    assert 'GHOST' not in body and 'OTHER PAGE' not in body
    assert page_body(page,tables,'text')=='Actual source body'


def test_merged_cells_are_retained_not_flattened():
    table={'page':1,'cells':[['Header',None],['A','B']], 'cell_spans':[{'row':0,'column':0,'row_span':1,'column_span':2,'text':'Header','role_candidate':'header'}]}
    body=page_body({'page':1,'text':''},[table]);assert 'colspan="2"' in body and '<th' in body
    table['cells'][0][1]='hidden data'
    with pytest.raises(ValueError,match='hide'):page_body({'page':1,'text':''},[table])


def test_case_shape_error_is_counted_and_not_propagated(tmp_path,monkeypatch):
    import local_vector_batch.benchmark as module
    source=tmp_path/'a.pdf';source.write_bytes(b'%PDF-test')
    case={'id':'case','pdf':'a.pdf','path':str(source),'sha256':sha(source.read_bytes()),'pages':1}
    def fake(command,**kwargs):
        dest=tmp_path/'run/packages/case';write(dest/'report.json',{'source_pdf_sha256':case['sha256']})
        (dest/'pages.jsonl').write_text('{"bad":"shape"}\n');write(dest/'tables.json',[])
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(module.subprocess,'run',fake)
    result=extract_case(case,'native',tmp_path/'run',Path('/launcher'),Path('/python'))
    assert result['status']=='error' and not result.get('artifacts')


def test_path_escape_and_duplicate_rule_are_rejected(tmp_path):
    root=fixture_suite(tmp_path);rules=[{'id':'duplicate','pdf':'../outside.pdf','page':1,'type':'present','text':'A'}]
    (root/'text.jsonl').write_text(json.dumps(rules[0])+'\n')
    with pytest.raises(ValueError,match='escaped'):prepare(root,tmp_path/'bad',custom=True)


def test_score_comparison_refuses_unmatched_or_invalid_runs(tmp_path):
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir()
    write(one/'report.json',{'suite_sha256':'one','valid_evaluation':True});write(two/'report.json',{'suite_sha256':'two','valid_evaluation':True})
    with pytest.raises(ValueError,match='matching'):compare([one,two],tmp_path/'comparison')
    write(two/'report.json',{'suite_sha256':'one','valid_evaluation':False})
    with pytest.raises(ValueError,match='invalid'):compare([one,two],tmp_path/'comparison')


def test_relative_run_path_produces_hash_bound_predictions(tmp_path,monkeypatch):
    import local_vector_batch.benchmark as module
    monkeypatch.chdir(tmp_path);source=tmp_path/'a.pdf';source.write_bytes(b'%PDF-test')
    case={'id':'case','pdf':'nested/a.pdf','path':str(source),'sha256':sha(source.read_bytes()),'pages':1}
    def fake(command,**kwargs):
        dest=Path(command[command.index('--output')+1]);dest.mkdir(parents=True,exist_ok=True)
        page={'page':1,'text':'Real source','text_sha256':sha(b'Real source'),'method':'pdf_text','status':'ready'}
        (dest/'pages.jsonl').write_text(json.dumps(page)+'\n');write(dest/'tables.json',[]);write(dest/'report.json',{'source_pdf_sha256':case['sha256'],'ocr_compute':{'engine':'off'}})
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(module.subprocess,'run',fake)
    result=extract_case(case,'native',Path('relative-run'),Path('/launcher'),Path('/python'))
    assert result['status']=='completed'
    assert all((tmp_path/'relative-run'/a['path']).is_file() for a in result['artifacts'])
    assert len(result['artifacts'])==2


def test_structure_projection_keeps_failed_cases_and_binds_relative_prediction_paths(tmp_path,monkeypatch):
    from local_vector_batch.benchmark import structure_projection_run
    from local_vector_batch.region_document import assemble
    root=fixture_suite(tmp_path);prepare(root,tmp_path/'suite',custom=True);suite=load(tmp_path/'suite/suite.json')
    parent=tmp_path/'parent';structured=tmp_path/'structured';cases=[]
    for index,case in enumerate(suite['cases']):
        text='Unverified source body.'
        raw=dict(source_pdf_sha256=case['sha256'],pages=[dict(page=1,text=text,text_sha256=sha(text.encode()),
            blocks=[dict(region_id=case['id']+':p1:r0',kind='text',scope='body',bbox=[1,1,99,20],text=text)])],tables=[])
        dest=structured/case['id'];write(dest/'data.json',assemble(raw))
        write(dest/'manifest.json',dict(source_pdf_sha256=case['sha256'],files={'data.json':sha((dest/'data.json').read_bytes())}))
        cases.append(dict(id=case['id'],pdf=case['pdf'],source_sha256=case['sha256'],status='error' if index==0 else 'completed',artifacts=[]))
    write(parent/'run.json',dict(config=dict(suite_sha256=suite['suite_sha256']),cases=cases))
    monkeypatch.chdir(tmp_path)
    result=structure_projection_run(tmp_path/'suite/suite.json',parent,structured,Path('relative-structure-run'))
    record=load(Path(result['output']))
    assert result['errors']==1 and len(record['cases'])==2 and result['model_inference_rerun'] is False
    assert all((tmp_path/'relative-structure-run'/a['path']).is_file() for c in record['cases'] for a in c['artifacts'])
    assert all('manifest_sha256' in binding for binding in record['projection']['structured_manifests'])
