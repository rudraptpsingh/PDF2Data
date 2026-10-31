from copy import deepcopy
import json
import pytest
from local_vector_batch.native_visuals import native_raster_regions, associate_table_geometry


def test_native_text_is_not_proof_of_image_text_or_decorative_status():
    class Page:
        width,height=600,800
        images=[dict(x0=100,top=40,x1=500,bottom=250,name='Certificate'),dict(x0=2,top=2,x1=3,bottom=3,name='Tiny')]
    regions=native_raster_regions(Page())
    assert len(regions)==2 and all(r['requires_source_raster_review'] for r in regions)
    assert all(r['text_recovery_verified'] is False for r in regions)
    assert regions[0]['bbox']==[100,40,500,250]
    assert len(native_raster_regions(Page(),minimum_page_fraction=.01))==1


def test_unavailable_image_geometry_is_held_not_dropped():
    class Page:
        width,height=600,800
        images=[{},object()]
    regions=native_raster_regions(Page())
    assert len(regions)==2 and all(r['bbox'] is None for r in regions)
    assert all(r['status']=='raster_content_unverified' for r in regions)
    with pytest.raises(ValueError):native_raster_regions(Page(),minimum_page_fraction=-.1)


def test_nested_grid_candidates_keep_all_raw_null_cells_and_relationships():
    tables=[dict(id='full',page=1,bbox=[0,0,100,100],cells=[['A',None],['B','']]),
            dict(id='header',page=1,bbox=[0,0,100,10],cells=[['A',None],['','']]),
            dict(id='subheader',page=1,bbox=[0,0,50,10],cells=[['A'],['']]),
            dict(id='other-page',page=2,bbox=[0,0,100,100],cells=[['x'],['y']])]
    cells=deepcopy([t['cells'] for t in tables])
    result=associate_table_geometry(tables)
    assert result['raw_candidates']==4 and result['contained_candidates']==2
    assert result['maximal_geometry_candidates']==2 and result['semantic_table_count'] is None
    assert tables[1]['geometry_relationships']['nearest_container_candidate']=='full'
    assert tables[2]['geometry_relationships']['nearest_container_candidate']=='header'
    assert [t['cells'] for t in tables]==cells
    assert all(t['geometry_relationships']['semantic_table_count_proven'] is False for t in tables)


def test_equal_or_overlapping_geometry_is_never_automatically_discarded():
    tables=[dict(id='a',page=1,bbox=[0,0,100,100]),dict(id='b',page=1,bbox=[0,0,100,100]),
            dict(id='c',page=1,bbox=[90,90,200,200])]
    result=associate_table_geometry(tables)
    assert len(tables)==3 and result['equal_geometry_candidates']==2
    assert tables[0]['geometry_relationships']['equal_geometry_candidates']==['b']
    assert not tables[2]['geometry_relationships']['contained_by_candidates']
    with pytest.raises(ValueError):associate_table_geometry(tables,tolerance=5)


def test_mixed_long_native_text_and_raster_certificate_is_not_ready(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,NumberObject,DecodedStreamObject
    from PIL import Image,ImageDraw
    from local_vector_batch.pdf_ingest import extract_pdf
    from local_vector_batch.evidence_package import verify_package
    image=Image.new('RGB',(600,220),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((5,5,595,215),outline='black',width=3)
    draw.text((20,30),'SAMPLE CERTIFICATE 16 MWh',fill='black')
    source=tmp_path/'native-and-certificate.pdf';writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    raster=DecodedStreamObject();raster.set_data(image.tobytes())
    raster.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(600),
        NameObject('/Height'):NumberObject(220),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)}),
        NameObject('/XObject'):DictionaryObject({NameObject('/Certificate'):writer._add_object(raster.flate_encode())})})
    content=DecodedStreamObject();content.set_data(('BT /F1 11 Tf 40 780 Td '+
        ' '.join('(Clause 13 - annual charging energy requirement and evidence.) Tj 0 -16 Td' for _ in range(8))+
        ' ET q 480 0 0 176 40 350 cm /Certificate Do Q').encode())
    page[NameObject('/Contents')]=writer._add_object(content)
    with source.open('wb') as stream:writer.write(stream)
    output=tmp_path/'extraction';report=extract_pdf(source,output,ocr='off')
    page=json.loads((output/'pages.jsonl').read_text())
    assert page['chars']>80 and page['method']=='pdf_text'
    assert page['status']=='review_required'
    assert len(page['native_raster_regions'])==1
    assert 'SAMPLE CERTIFICATE' not in page['text']  # No invented OCR transcription.
    assert not report['complete_for_embedding'] and report['requires_source_review']
    assert any(i['kind']=='native_raster_content_unverified' for i in report['issues'])
    receipt=verify_package(output,source_pdf=source)
    assert receipt['original_pdf_checked'] and receipt['page_text_readiness']=='review_required'
