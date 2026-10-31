from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from local_vector_batch.layout_overlay import build_overlays,normalized,viewer_controls,visualize_bundle


def fixture():
    return dict(source_pdf_sha256='a'*64,pages=[dict(page=1,blocks=[dict(region_id='a:r1',text='A heading')],failed_regions=['a:r2'])],
        tables=[],document_blocks={'blocks':[dict(id='b1',region_id='a:r1',kind='heading',spans=[dict(page=1,exact_quote='A heading')])]},quality={'results':[dict(page=1)]})


def layout():
    return dict(pages=[dict(page=1,page_size_points=[200,400],regions=[
        dict(id='a:r1',kind='text',bbox=[20,40,180,80],reading_order_index=0),
        dict(id='a:r2',kind='table',bbox=[20,100,180,300],reading_order_index=1),
        dict(id='a:r3',kind='picture',bbox=[20,310,180,350],reading_order_index=2)])])


def test_source_geometry_types_and_failed_regions_remain_distinct_from_approval():
    data=fixture();original=deepcopy(data);result=build_overlays(data,layout());regions=result['pages'][0]['regions']
    assert [r['type'] for r in regions]==['heading','table','figure']
    assert regions[0]['bbox_normalized']==[.1,.1,.9,.2]
    assert regions[1]['state']=='reading_failed' and regions[1]['text']==''
    assert regions[2]['state']=='visual_review'
    assert data==original and not result['publication_eligible'] and not result['recognition_rerun']
    assert all(r['confidence'] is None and not r['content_verified'] for r in regions)


@pytest.mark.parametrize('box',[[0,0,201,2],[-1,0,1,1],[0,0,float('nan'),1],[0,0,0,1]])
def test_invalid_coordinates_are_not_silently_clamped_or_drawn(box):
    with pytest.raises(ValueError):normalized(box,200,400)
    source=layout();source['pages'][0]['regions'][0]['bbox']=box
    page=build_overlays(fixture(),source)['pages'][0]
    assert 'a:r1' in page['unlocated_blocks'] and len(page['regions'])==2


def test_cross_page_paragraph_does_not_reuse_the_first_page_box():
    data=dict(source_pdf_sha256='a'*64,pages=[dict(page=1),dict(page=2)],layout=[dict(page=1,width=200,height=400),dict(page=2,width=200,height=400)],
              document_blocks={'blocks':[dict(id='b1',kind='paragraph',bbox=[20,40,180,80],spans=[dict(page=1,exact_quote='First'),dict(page=2,exact_quote='Second')])]})
    pages=build_overlays(data)['pages']
    assert all(p['regions']==[] and p['unlocated_blocks']==['b1'] for p in pages)


def test_layout_from_another_source_is_rejected():
    source=layout();source['source_pdf_sha256']='b'*64
    with pytest.raises(ValueError,match='different source'):build_overlays(fixture(),source)


def test_table_hover_preview_preserves_structure_and_escapes_source_text():
    data=fixture();data['document_blocks']['blocks'][0].update(kind='table',table_id='table-1')
    data['tables']=[dict(id='table-1',page=1,cells=[['Rate',None],['<script>','Not effective']],cell_spans=[
        dict(row=0,column=0,row_span=1,column_span=2,text='Rate',role_candidate='header'),
        dict(row=1,column=0,row_span=1,column_span=1,text='<script>'),
        dict(row=1,column=1,row_span=1,column_span=1,text='Not effective')])]
    preview=build_overlays(data,layout())['pages'][0]['regions'][0]['preview_html']
    assert '<table' in preview and 'colspan="2"' in preview and '<th' in preview
    assert '&lt;script&gt;' in preview and '<script>' not in preview and 'Not effective' in preview


def test_preview_requires_overlay_identity_in_manifest(tmp_path):
    bundle=tmp_path/'bundle';bundle.mkdir()
    (bundle/'manifest.json').write_text(json.dumps(dict(files={})))
    with pytest.raises(ValueError,match='not in manifest'):visualize_bundle(bundle,tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_script_payload_escapes_source_text_without_executing_it():
    data=fixture();data['pages'][0]['blocks'][0]['text']='</script><script>alert(1)</script>'
    rendered=viewer_controls(build_overlays(data,layout()))
    assert '</script><script>alert' not in rendered and '\\u003c/script' in rendered


def test_preview_rejects_tampered_source_image_before_creating_output(tmp_path):
    from PIL import Image
    bundle=tmp_path/'bundle';(bundle/'assets').mkdir(parents=True)
    pdf=bundle/'source.pdf';pdf.write_bytes(b'%PDF-test')
    data=fixture();data['source_pdf_sha256']=sha256(pdf.read_bytes()).hexdigest()
    overlay=bundle/'overlays.json';overlay.write_text(json.dumps(build_overlays(data,layout())))
    image=bundle/'assets/page-0001.png';Image.new('RGB',(200,400),'white').save(image)
    files={str(p.relative_to(bundle)):sha256(p.read_bytes()).hexdigest() for p in (pdf,overlay,image)}
    (bundle/'manifest.json').write_text(json.dumps(dict(source_pdf_sha256=data['source_pdf_sha256'],files=files)))
    result=visualize_bundle(bundle,tmp_path/'valid',pages=[1])
    assert result['pages']==1 and Path(result['images'][0]).is_file()
    with pytest.raises(ValueError,match='does not exist'):visualize_bundle(bundle,tmp_path/'wrong-page',pages=[2])
    image.write_bytes(b'changed')
    with pytest.raises(ValueError,match='artifact changed'):visualize_bundle(bundle,tmp_path/'tampered',pages=[1])
    assert not (tmp_path/'tampered').exists()
