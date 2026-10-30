import json,hashlib
from pathlib import Path
import pytest
from local_vector_batch.structured import geometry_order,contained_region_partition,parse_tables,sources


def region(id,box,kind='text'):
    return {'id':id,'bbox':box,'kind':kind,'crop_sha256':'source-crop'}


def test_column_order_keeps_heading_then_complete_left_column_then_right():
    rows=[region('r2',[60,40,100,50]),region('l2',[0,40,40,50]),region('r1',[60,20,100,30]),region('heading',[0,0,100,10]),region('l1',[0,20,40,30])]
    assert [r['id'] for r in geometry_order(rows)]==['heading','l1','l2','r1','r2']


def test_table_ownership_retains_contained_blocks_and_partial_overlap():
    table=region('table',[10,10,90,90],'table');inside=region('inside',[20,20,30,30]);partial=region('partial',[5,20,30,30]);caption=region('caption',[10,95,80,100])
    active,suppressed=contained_region_partition([inside,table,partial,caption])
    assert [r['id'] for r in active]==['table','partial','caption']
    assert suppressed[0]['represented_by']=='table' and suppressed[0]['id']=='inside'


def test_otsl_preserves_rectangular_merges_empty_cells_and_literal_negation():
    text='<fcel>GNA<lcel><fcel>COD<nl><ucel><xcel><fcel>30.07.2026<nl><fcel>Not effective<ecel><fcel>0<nl>'
    t=parse_tables(text,5,region('r',[0,0,100,100],'table'))[0]
    assert t['cells']==[['GNA',None,'COD'],[None,None,'30.07.2026'],['Not effective','','0']]
    assert t['cell_spans'][0]['row_span']==2 and t['cell_spans'][0]['column_span']==2
    assert t['page']==5 and t['status']=='needs_row_column_review'


@pytest.mark.parametrize('value',['<lcel><fcel>x<nl>','<fcel>a<nl><fcel>b<fcel>c<nl>','<fcel>a<lcel>hidden<nl>','<fcel>a<lcel><nl><ucel><fcel>bad<nl>'])
def test_bad_otsl_is_rejected_without_guessing_or_hiding_values(value):
    with pytest.raises(ValueError):parse_tables(value,1,region('r',[0,0,1,1]))


def test_html_is_parsed_into_safe_cells_and_explicit_spans():
    t=parse_tables('<table><tr><th colspan="2">Rate</th></tr><tr><td>100%</td><td>Not effective</td></tr></table>',1,region('r',[0,0,100,100]))[0]
    assert t['cells']==[['Rate',None],['100%','Not effective']]
    assert t['cell_spans'][0]['role_candidate']=='header'


@pytest.mark.parametrize('text',['<table><tr><td>x','<table><tr><td rowspan="5">x</td></tr></table>','<table><tr><td>x<table>bad</table></td></tr></table>'])
def test_truncated_nested_or_outside_html_spans_are_not_silently_repaired(text):
    with pytest.raises(ValueError):parse_tables(text,1,region('r',[0,0,1,1]))


def test_source_manifest_rejects_mutated_pdf_and_path_like_ids(tmp_path):
    pdf=tmp_path/'s.pdf';pdf.write_bytes(b'%PDF-test')
    row={'id':'a','path':str(pdf),'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
    m=tmp_path/'sources.json';m.write_text(json.dumps({'schema_version':'pdf2data.source-batch.v1','sources':[row]}));assert sources(m)==[row]
    pdf.write_bytes(b'mutated')
    with pytest.raises(ValueError,match='changed'):sources(m)
    row['id']='../escape';m.write_text(json.dumps({'schema_version':'pdf2data.source-batch.v1','sources':[row]}))
    with pytest.raises(ValueError,match='ID'):sources(m)


def test_source_crop_is_reproved_from_pdf_not_just_editable_hashes(tmp_path):
    from io import BytesIO
    from PIL import Image
    from pypdf import PdfWriter
    import pypdfium2 as pdfium
    from local_vector_batch.structured import verify_page_source
    pdf=tmp_path/'blank.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    with pdf.open('wb') as stream:writer.write(stream)
    with pdfium.PdfDocument(str(pdf)) as doc:
        bitmap=doc[0].render(scale=1);image=bitmap.to_pil().copy();bitmap.close()
    stream=BytesIO();image.save(stream,format='PNG')
    crop=tmp_path/'crop.png';image.crop([8,8,32,32]).save(crop)
    page={'page':1,'source_image_sha256':hashlib.sha256(stream.getvalue()).hexdigest(),'render':{'render_dpi':72},'page_size_points':[100,100],
          'regions':[{'bbox':[10,10,30,30],'crop_bbox_pixels':[8,8,32,32],'crop_path':str(crop),'crop_sha256':hashlib.sha256(crop.read_bytes()).hexdigest()}]}
    verify_page_source({'source_pdf_path':str(pdf)},page)
    Image.new(image.mode,(24,24),'black').save(crop);page['regions'][0]['crop_sha256']=hashlib.sha256(crop.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='original source pixels'):verify_page_source({'source_pdf_path':str(pdf)},page)


def test_two_column_order_does_not_split_table_into_rows():
    rows=[region('table',[0,0,100,30],'table'),region('left',[0,40,40,70]),region('right',[60,40,100,70])]
    assert [r['id'] for r in geometry_order(rows)]==['table','left','right']


@pytest.mark.parametrize('failed',[0,1])
def test_structured_benchmark_strips_labels_and_counts_failed_readings(tmp_path,monkeypatch,failed):
    import local_vector_batch.benchmark as bench
    from types import SimpleNamespace
    output=tmp_path/'run';case={'id':'source','pdf':'unknown.pdf','path':'/original/source.pdf','sha256':'source-sha','pages':1}
    suite={'suite_sha256':'suite-sha','cases':[case],'rules':[{'text':'SECRET EXPECTED ANSWER'}]}
    monkeypatch.setattr(bench,'runtime',lambda python:{'mock_runtime':True})
    commands=[]
    def fake(command,**kwargs):
        commands.append(command)
        if command[1]=='pdf-layout-detect':
            data=bench.load(output/'sources.json')
            assert set(data['sources'][0])=={'id','path','sha256','pages'}
            assert 'SECRET' not in json.dumps(data)
            bench.write(output/'layout/layout.json',{'source_manifest_sha256':bench.sha((output/'sources.json').read_bytes()),'documents':[{'id':'source'}]})
        else:
            folder=output/'read/source';folder.mkdir(parents=True)
            page={'page':1,'text':'Actual source','text_sha256':bench.sha(b'Actual source'),'blocks':[{'scope':'body','markdown':'Actual source'},{'scope':'furniture','markdown':'Footer'}],'visual_review_regions':[]}
            (folder/'pages.jsonl').write_text(json.dumps(page)+'\n');bench.write(folder/'tables.json',[])
            bench.write(output/'read/read.json',{'documents':[{'id':'source','failed_regions':failed}],'reused_regions':0,'model':'model','revision':'revision','device_used':'gpu','model_files_sha256':{},'runtime':{}})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bench.subprocess,'run',fake)
    bench.structured_run(suite,output,Path('/reader-python'),Path('/layout-python'))
    result=bench.load(output/'run.json');record=result['cases'][0]
    assert result['errors']==failed and record['status']==('error' if failed else 'completed')
    assert [c[1] for c in commands]==['pdf-layout-detect','pdf-region-read']
    assert all('SECRET' not in (output/a['path']).read_text() and 'Footer' not in (output/a['path']).read_text() for a in record['artifacts'])


@pytest.mark.parametrize('text',['<table></table>','<table><tr></tr></table>','<table><tr><td> </td></tr></table>','<ecel><nl>'])
def test_empty_table_response_is_unresolved_not_success(text):
    with pytest.raises(ValueError,match='empty'):parse_tables(text,1,region('r',[0,0,1,1]))
