"""Source-preserving mixed-page inclusion never silently promotes OCR."""
from copy import deepcopy
import hashlib,json,shutil
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,NumberObject,DecodedStreamObject
from local_vector_batch.legal_l1 import extract_document
from local_vector_batch.native_image_regions import build,verify,capture_requests,bind_readings,assess_completeness
from local_vector_batch.l1_export import export_processed

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.fixture()
def actual(tmp_path):
    image=Image.new('RGB',(500,120),'white');draw=ImageDraw.Draw(image);draw.text((5,15),'Date 15/07/2026',fill='black');image.save(tmp_path/'image.png')
    pdf=tmp_path/'input.pdf';writer=PdfWriter();page=writer.add_blank_page(612,842)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    raster=DecodedStreamObject();raster.set_data(image.tobytes());raster.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(500),NameObject('/Height'):NumberObject(120),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)}),NameObject('/XObject'):DictionaryObject({NameObject('/Im0'):writer._add_object(raster)})})
    content=DecodedStreamObject();content.set_data(b'BT /F1 11 Tf 50 780 Td (Original clause stays complete and unchanged.) Tj ET\nq 250 0 0 60 50 500 cm /Im0 Do Q')
    page[NameObject('/Contents')]=writer._add_object(content);writer.write(pdf)
    parent=tmp_path/'packages'/'original';parent.parent.mkdir()
    extract_document({'id':'actual-source-fixture','family':'explanatory_memorandum','path':str(pdf),'sha256':digest(pdf),'url':'https://example.invalid/fixture.pdf'},parent)
    runtime=Path(__file__).resolve().parents[1]
    items=capture_requests(parent,1,tmp_path/'crops');raw=[]
    for item in items:
        raw.append({**{k:item[k]for k in ('id','source_pdf_sha256','page','page_text_sha256','original_crop_path','original_crop_sha256','crop_source_bbox_PDF_points','render_dpi')},'vision_cpu':{'lines':[{'text':'15/07/2026','bbox':[.01,.1,.4,.4]}]},'rapidocr_cpu':{'lines':[{'text':'16/07/2026','bbox':[.01,.1,.4,.4],'words':[]}]}})
    readings=tmp_path/'readings.json';save(readings,{'regions':raw})
    packet=tmp_path/'packet';result=build(parent,runtime,1,packet,readings)
    return tmp_path,parent,runtime,readings,packet,result,items

def test_actual_source_content_is_included_without_promoting_conflicting_dates(actual):
    _,parent,_,_,packet,result,_=actual
    native=(parent/'extraction/pages.jsonl').read_bytes();representation=read(packet/'representation.json')
    assert result['image_regions']==1 and result['unresolved_readings']==2
    assert result['page_complete']is False and result['embedding_candidate_allowed']is False
    assert 'Original clause stays complete' in representation['regions'][0]['native_selected_text']
    assert '15/07/2026' in json.dumps(representation)and '16/07/2026'in json.dumps(representation)
    assert (parent/'extraction/pages.jsonl').read_bytes()==native
    assert verify(packet)==result

@pytest.mark.parametrize('kind',['missing_image','duplicate_image','wrong_date_scope','wrong_crop'])
def test_missing_duplicate_or_drifted_regions_fail(actual,kind):
    _,_,_,readings,_,_,items=actual;r=read(readings)
    if kind=='missing_image':r['regions']=[]
    elif kind=='duplicate_image':r['regions']*=2
    elif kind=='wrong_date_scope':r['regions'][0]['page_text_sha256']='0'*64
    else:r['regions'][0]['original_crop_sha256']='0'*64
    with pytest.raises(ValueError):bind_readings(r,items)

@pytest.mark.parametrize('kind',['crop','represented_text','quality','claim','extra_artifact'])
def test_resealed_tampering_never_clears(actual,kind):
    _,_,_,_,packet,_,_=actual;m=read(packet/'source-regions-manifest.json')
    if kind=='crop':
        file=next((packet/'original-regions').glob('*.png'));file.write_bytes(b'wrong-source');m['artifacts'][str(file.relative_to(packet))]=digest(file)
    elif kind=='represented_text':
        file=packet/'representation.json';r=read(file);r['regions'][0]['native_selected_text']='altered';save(file,r);m['artifacts'][file.name]=digest(file)
    elif kind=='quality':
        file=packet/'completeness.json';r=read(file);r['page_complete']=True;r['embedding_candidate_allowed']=True;save(file,r);m['artifacts'][file.name]=digest(file)
    elif kind=='claim':m['publication_eligible']=True
    else:(packet/'hidden.txt').write_text('unbound')
    save(packet/'source-regions-manifest.json',m)
    with pytest.raises(ValueError):verify(packet)

def test_agreement_is_not_visual_or_table_completeness():
    r={'regions':[{'id':'i','readers':[{'reader':'vision_cpu','lines':[]},{'reader':'rapidocr_cpu','lines':[]}],'literal_geometry_agreements':[]}]}
    result=assess_completeness(r,{'results':[{'outcome':'auto_accepted'}]})
    assert result['gates']['raster_reader_literal_geometry']['state']=='pass'
    assert result['gates']['raster_source_visual_coverage']['state']=='unknown'
    assert result['page_complete']is False

def test_export_keeps_original_and_flagged_raster_readings(actual):
    tmp,parent,_,_,packet,_,_=actual
    export_processed(parent.parent,tmp/'all.json',packet)
    out=read(tmp/'all.json');doc=out['documents'][0]
    assert doc['pages']==[json.loads(line)for line in(parent/'extraction/pages.jsonl').read_text().splitlines()]
    assert len(doc['mixed_source_region_candidates'])==1
    assert doc['mixed_source_region_candidates'][0]['completeness']['page_complete']is False
