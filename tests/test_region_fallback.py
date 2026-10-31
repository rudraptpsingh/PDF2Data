import json
import sys
from types import SimpleNamespace,ModuleType
import pytest
from local_vector_batch.region_fallback import validate_lines,source_coverage


def line(text='Amount 0',confidence=.95):
    return dict(text=text,confidence=confidence,bbox=[.1,.1,.9,.2])


def test_fallback_rejects_whole_reading_instead_of_clipping_uncertain_lines():
    assert validate_lines([line(),line('unread condition',.4)])=='low_fallback_confidence'
    assert validate_lines([])=='empty_fallback'
    assert validate_lines([line('')])=='empty_fallback_line'
    assert validate_lines([line(confidence=float('nan'))])=='low_fallback_confidence'
    assert validate_lines([{**line(),'bbox':[0,0,2,1]}])=='invalid_fallback_geometry'
    assert validate_lines([line('0 0 0 0')]) is None


def test_confident_label_does_not_clear_omitted_source_number():
    from PIL import Image,ImageDraw
    image=Image.new('L',(200,200),255);draw=ImageDraw.Draw(image)
    draw.rectangle((20,20,80,35),fill=0);draw.rectangle((170,20,185,35),fill=0)
    coverage=source_coverage(image,[dict(text='Label',confidence=1,bbox=[.1,.1,.4,.175])])
    assert not coverage['within_limit'] and coverage['uncovered_dark_pixels']>=200
    assert not coverage['semantic_coverage_verified']
    assert source_coverage(image,[dict(bbox=[.1,.1,.93,.175])])['within_limit']


@pytest.mark.parametrize('kind,accepted,status',[('text',True,'candidate'),('text',False,'reading_failed'),('table',True,'reading_failed')])
def test_reader_preserves_failed_paddle_and_does_not_turn_table_into_text(tmp_path,monkeypatch,kind,accepted,status):
    from local_vector_batch import structured as s,region_fallback as fallback,region_document
    from pypdf import PdfWriter
    import pypdfium2 as pdfium
    cache=tmp_path/'cache';snapshot=cache/('models--'+s.MODEL.replace('/','--'))/'snapshots'/s.REVISION
    snapshot.mkdir(parents=True);(snapshot/'config.json').write_text('{}')
    monkeypatch.setenv('HF_HUB_CACHE',str(cache))
    mx=ModuleType('mlx.core');mx.metal=SimpleNamespace(is_available=lambda:True)
    mx.gpu='gpu';mx.cpu='cpu';mx.set_default_device=lambda x:None;mx.default_device=lambda:'gpu';mx.get_peak_memory=lambda:100
    mlx=ModuleType('mlx');mlx.core=mx
    vm=ModuleType('mlx_vlm');vm.load=lambda path:(None,SimpleNamespace(apply_chat_template=lambda *a,**kw:'OCR:'))
    vm.generate=lambda *a,**kw:SimpleNamespace(text='repeated '*256,generation_tokens=256)
    monkeypatch.setitem(sys.modules,'mlx',mlx);monkeypatch.setitem(sys.modules,'mlx.core',mx);monkeypatch.setitem(sys.modules,'mlx_vlm',vm)
    monkeypatch.setattr(s,'version',lambda name:'test')
    monkeypatch.setattr(fallback,'vision_identity',lambda:{'engine':'macos_vision','test':True})
    calls=[]
    def attempt(path):
        calls.append(path)
        return dict(accepted=accepted,lines=[line(confidence=.95 if accepted else .1)],text='Amount 0' if accepted else None,failure_reason=None if accepted else 'low_fallback_confidence')
    monkeypatch.setattr(fallback,'vision_attempt',attempt)
    monkeypatch.setattr(region_document,'export_regions',lambda *a,**kw:{'mock':True})
    pdf=tmp_path/'source.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    with pdf.open('wb') as stream:writer.write(stream)
    with pdfium.PdfDocument(str(pdf)) as doc:
        bitmap=doc[0].render(scale=1);image=bitmap.to_pil().copy();bitmap.close()
    source=tmp_path/'page.png';image.save(source)
    crop=tmp_path/'crop.png';image.crop([8,8,92,92]).save(crop)
    region=dict(id='a:p1:r0',kind=kind,bbox=[10,10,90,90],crop_bbox_pixels=[8,8,92,92],crop_path=str(crop),crop_sha256=s.sha(crop.read_bytes()))
    page=dict(page=1,source_image=str(source),source_image_sha256=s.sha(source.read_bytes()),render={'render_dpi':72},page_size_points=[100,100],regions=[region],coverage={},layout_error=None)
    layout=tmp_path/'layout.json';s.write(layout,dict(schema_version='pdf2data.region-layout.v1',documents=[dict(id='a',source_pdf_path=str(pdf),source_pdf_sha256=s.sha(pdf.read_bytes()),pages=[page])]))
    out=tmp_path/'read';s.read_regions(layout,out,max_tokens=256,text_fallback='vision')
    result=json.loads((out/'a/readings.json').read_text())[0]
    assert result['status']==status and not result['content_verified']
    if kind=='table':
        assert calls==[] and result['raw_response']=='repeated '*256
    else:
        assert len(calls)==1 and result['paddle_attempt']['failure_reason']=='token_cap'
        assert result['fallback_attempt']['accepted']==accepted
        assert result['paddle_attempt']['text']==' '.join(['repeated']*256)
        if accepted:
            assert result['text']=='Amount 0' and result['tokens'] is None
            assert result['reader_engine']=='macos_vision'
            def no_generation(*args,**kwargs):raise AssertionError('same-contract retry should reuse the reading')
            vm.generate=no_generation
            reused=tmp_path/'reused'
            s.read_regions(layout,reused,max_tokens=256,text_fallback='vision',reuse_readings=out)
            cached=json.loads((reused/'a/readings.json').read_text())[0]
            assert cached['status']=='candidate' and cached['generation_reused']
            assert cached['paddle_attempt']==result['paddle_attempt'] and len(calls)==1
            monkeypatch.setattr(fallback,'vision_identity',lambda:{'engine':'macos_vision','test':'different'})
            changed=tmp_path/'changed'
            s.read_regions(layout,changed,max_tokens=256,text_fallback='vision',reuse_readings=out)
            stale=json.loads((changed/'a/readings.json').read_text())[0]
            assert stale['status']=='reading_failed' and 'same-contract retry' in stale['error']
        else:
            assert result['raw_response']=='repeated '*256
