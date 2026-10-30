import hashlib,json
from pathlib import Path
import pytest
from local_vector_batch.l1_qwen import SCHEMA,MODEL,REVISION,PROMPT,compare,save,validate,run
from local_vector_batch.cli import parser

def fixture(tmp_path):
    package=tmp_path/'source';(package/'extraction').mkdir(parents=True)
    (package/'original.pdf').write_bytes(b'original captured source')
    text='GNA Not effective COD 30.07.2026'
    sha=lambda b:hashlib.sha256(b).hexdigest()
    page={'page':5,'text':text,'text_sha256':sha(text.encode())}
    (package/'extraction/pages.jsonl').write_text(json.dumps(page)+'\n')
    image=tmp_path/'source.png';image.write_bytes(b'captured raster')
    case={'id':'table-page','source_package':str(package),'page':5,'page_text_sha256':page['text_sha256'],'source_pdf_sha256':sha((package/'original.pdf').read_bytes()),'image':str(image),'image_sha256':sha(image.read_bytes())}
    cases=tmp_path/'cases.json';save(cases,{'schema_version':SCHEMA,'cases':[case]})
    reading={k:case[k] for k in ('id','page','page_text_sha256','source_pdf_sha256','image_sha256')}
    reading.update(text=text,possible_token_cap=False)
    readings=tmp_path/'readings.json';save(readings,{'schema_version':SCHEMA,'cases_sha256':sha(cases.read_bytes()),'model':MODEL,'revision':REVISION,'prompt':PROMPT,'results':[reading]})
    return cases,readings,case

def test_cli_exposes_source_bound_workflow():
    p=parser()
    assert p.parse_args(['l1-qwen-run','--cases','c','--output','o','--resume']).resume
    assert p.parse_args(['l1-qwen-prepare','--packages','p','--output','o']).command=='l1-qwen-prepare'

def test_exact_reading_never_promotes_source(tmp_path):
    c,r,_=fixture(tmp_path);out=tmp_path/'report.json';report=validate(c,r,out)
    assert report['counts']=={'literal_agreement_requires_layout_proof':1}
    saved=json.loads(out.read_text());assert saved['publication_eligible'] is False
    assert saved['results'][0]['source_quality_changed'] is False

@pytest.mark.parametrize('field',['image_sha256','source_pdf_sha256','page_text_sha256','page'])
def test_stale_reading_rejected(tmp_path,field):
    c,r,_=fixture(tmp_path);data=json.loads(r.read_text());data['results'][0][field]='wrong';save(r,data)
    with pytest.raises(ValueError,match='identity'):validate(c,r,tmp_path/'report.json')

def test_extra_date_and_lost_negation_are_not_agreement():
    assert not compare('Not effective 30.07.2026','effective 30.07.2026')['literal_nonspace_equal']
    assert not compare('30.07.2026','30.07.2026 31.12.2027')['number_order_equal']

def test_token_cap_and_missing_cases_stay_incomplete(tmp_path):
    c,r,_=fixture(tmp_path);data=json.loads(r.read_text());data['results'][0]['possible_token_cap']=True;save(r,data)
    assert validate(c,r,tmp_path/'report.json')['counts']=={'incomplete_reading':1}
    data['results']=[];save(r,data)
    assert validate(c,r,tmp_path/'report.json')['counts']=={'not_run':1}

def test_changed_raster_is_rejected(tmp_path):
    c,r,case=fixture(tmp_path);Path(case['image']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='image hash'):validate(c,r,tmp_path/'report.json')

def test_validation_cannot_overwrite_package_or_input(tmp_path):
    c,r,case=fixture(tmp_path)
    for out in (c,r,Path(case['source_package'])/'quality.json'):
        with pytest.raises(ValueError,match='overwrite'):validate(c,r,out)

def test_offline_required_before_model_import(tmp_path,monkeypatch):
    monkeypatch.delenv('HF_HUB_OFFLINE',raising=False)
    with pytest.raises(ValueError,match='downloads are disabled'):run(tmp_path/'missing',tmp_path/'out')

def test_duplicate_result_rejected(tmp_path):
    c,r,_=fixture(tmp_path);data=json.loads(r.read_text());data['results']*=2;save(r,data)
    with pytest.raises(ValueError,match='duplicate'):validate(c,r,tmp_path/'report.json')

def test_nonspace_equality_is_not_word_boundary_proof():
    result=compare('not able','notable')
    assert result['literal_nonspace_equal'] is True
    assert not all(result.values())
    assert not all(compare('1 2','12').values())

def actual_source_fixture(tmp_path):
    from test_legal_l1 import source_fixture
    from local_vector_batch.legal_l1 import extract_document
    from io import BytesIO
    import pypdfium2 as pdfium
    from local_vector_batch.l1_qwen import digest
    source=source_fixture(tmp_path);root=tmp_path/'packages';folder=root/'example'
    extract_document(source,folder)
    page=json.loads((folder/'extraction/pages.jsonl').read_text().splitlines()[0])
    image=tmp_path/'image.png'
    with pdfium.PdfDocument(folder/'original.pdf') as pdf:
        p=pdf[0]
        try:p.render(scale=2.5).to_pil().convert('RGB').save(image)
        finally:p.close()
    case={'id':'actual-page','source_package':str(folder),'page':1,'page_text_sha256':page['text_sha256'],'source_pdf_sha256':digest(folder/'original.pdf'),'image':str(image),'image_sha256':digest(image)}
    cases=tmp_path/'cases.json';save(cases,{'schema_version':SCHEMA,'cases':[case]})
    r={k:case[k] for k in ('id','page','page_text_sha256','source_pdf_sha256','image_sha256')}
    # A simulated exact reading tests pipeline mechanics, not model performance.
    r.update(text=page['text'],possible_token_cap=False)
    readings=tmp_path/'readings.json';save(readings,{'schema_version':SCHEMA,'model':MODEL,'revision':REVISION,'prompt':PROMPT,'cases_sha256':digest(cases),'results':[r]})
    return root,cases,readings,case

def test_reproof_retains_original_and_valid_immutable_copy(tmp_path):
    from local_vector_batch.l1_qwen import reproof,digest
    from local_vector_batch.legal_l1 import verify_document
    root,cases,readings,case=actual_source_fixture(tmp_path)
    before={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
    result=reproof(cases,readings,root,tmp_path/'reproved')
    assert result['source_text_modified'] is False
    assert result['publication_eligible'] is False
    verify_document(tmp_path/'reproved/example')
    assert before=={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
    assert (tmp_path/'reproved/example/vision-evidence.json').exists()

def test_reproof_rejects_forged_input_raster(tmp_path):
    from local_vector_batch.l1_qwen import reproof,digest
    root,cases,readings,case=actual_source_fixture(tmp_path)
    Path(case['image']).write_bytes(b'not the original raster')
    data=json.loads(cases.read_text());data['cases'][0]['image_sha256']=digest(case['image']);save(cases,data)
    r=json.loads(readings.read_text());r['cases_sha256']=digest(cases);r['results'][0]['image_sha256']=digest(case['image']);save(readings,r)
    with pytest.raises(ValueError,match='original page raster'):reproof(cases,readings,root,tmp_path/'reproved')
    assert not (tmp_path/'reproved').exists()

def test_table_parser_keeps_two_distinct_illustrations_and_blanks():
    from local_vector_batch.l1_qwen import html_tables
    tables,valid=html_tables('<table><tr><th>GNA</th><th>COD</th></tr><tr><td>31.12.2027</td><td></td></tr></table><table><tr><td>Not effective</td><td>30.07.2026</td></tr></table>')
    assert valid and len(tables)==2
    assert tables[0]['rows'][1]==['31.12.2027','']
    assert tables[1]['rows'][0]==['Not effective','30.07.2026']

def test_merged_or_truncated_html_cannot_be_assumed_rectangular():
    from local_vector_batch.l1_qwen import html_tables
    tables,_=html_tables('<table><tr><td colspan="2">A</td></tr></table>')
    assert tables[0]['unsupported_spans']
    tables,valid=html_tables('<table><tr><td>missing end')
    assert not valid and not tables
