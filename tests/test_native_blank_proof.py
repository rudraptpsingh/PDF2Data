import json
from pathlib import Path
import pytest

from local_vector_batch.native_blank_proof import inspect_exact_white,blank_quality,create_blank_version,verify_blank_version,digest
from local_vector_batch.legal_l1 import extract_document,verify_document


def pdf_fixture(path,kind='white'):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,NumberObject,DecodedStreamObject,ArrayObject,FloatObject
    writer=PdfWriter();page=writer.add_blank_page(width=120,height=150);content=b''
    if kind=='nearwhite':content=b'.996 .996 .996 rg 0 0 120 150 re f'
    elif kind=='dot':content=b'0 0 0 rg 60 75 .5 .5 re f'
    elif kind=='text':
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=b'1 1 1 rg BT /F1 10 Tf 20 80 Td (Hidden text) Tj ET'
    elif kind in {'black_image','white_image'}:
        image=DecodedStreamObject();image.set_data((b'\x00\x00\x00'if kind=='black_image'else b'\xff\xff\xff')*4)
        image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(2),NameObject('/Height'):NumberObject(2),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Im1'):writer._add_object(image)})})
        content=b'q 120 0 0 150 0 0 cm /Im1 Do Q'
    elif kind=='crop':page[NameObject('/CropBox')]=ArrayObject([FloatObject(0),FloatObject(0),FloatObject(100),FloatObject(130)])
    stream=DecodedStreamObject();stream.set_data(content);page[NameObject('/Contents')]=writer._add_object(stream);writer.write(path)
    return path


def test_exact_white_and_white_image_have_no_content_or_embedding(tmp_path):
    for kind in ['white','white_image']:
        source=pdf_fixture(tmp_path/(kind+'.pdf'),kind);proof=inspect_exact_white(source,1,'',digest(source.read_bytes()))
        assert proof['status']=='source_proven_exact_white_blank'and proof['nonwhite_rgb_pixels']==0
        assert proof['native_character_objects']==proof['pdfium_character_objects']==0
        assert all(c['state']=='pass'for c in blank_quality(proof)['components'].values())
        assert proof['embedding_candidate_allowed']is False and proof['publication_eligible']is False


@pytest.mark.parametrize('kind',['nearwhite','dot','black_image','text','crop'])
def test_any_nonwhite_pixel_scanned_content_native_char_or_crop_is_held(tmp_path,kind):
    source=pdf_fixture(tmp_path/(kind+'.pdf'),kind);proof=inspect_exact_white(source,1,'')
    assert proof['status']=='held'
    with pytest.raises(ValueError):blank_quality(proof)
    if kind=='nearwhite':assert proof['nonwhite_rgb_pixels']>0


def test_selected_content_stale_pdf_or_wrong_page_never_qualifies(tmp_path):
    source=pdf_fixture(tmp_path/'white.pdf')
    assert inspect_exact_white(source,1,'shall not') ['status']=='held'
    with pytest.raises(ValueError):inspect_exact_white(source,1,'','0'*64)
    with pytest.raises(ValueError):inspect_exact_white(source,2,'')


def test_stale_boolean_checks_cannot_relax_exact_pixel_or_reader_invariants(tmp_path):
    import copy
    source=pdf_fixture(tmp_path/'white.pdf');proof=inspect_exact_white(source,1,'')
    for key,value in [('render_dpi',72),('rgb_white_value_required',250),('nonwhite_rgb_pixels',1),('native_character_objects',1),('draw_annotations',False)]:
        bad=copy.deepcopy(proof);bad[key]=value
        with pytest.raises(ValueError):blank_quality(bad)


def test_new_l1_version_preserves_extraction_and_fresh_decoder_rejects_tampering(tmp_path):
    import local_vector_batch.native_blank_proof as implementation
    source=pdf_fixture(tmp_path/'white.pdf');runtime=Path(implementation.__file__).resolve().parents[2]
    original=tmp_path/'original'
    declared={'id':'white-source','path':str(source),'sha256':digest(source.read_bytes()),'family':'regulation','expected_pages':1,'url':'https://example.invalid/white.pdf'}
    extract_document(declared,original,ocr='off');before=(original/'l1-package.json').read_bytes()
    result=create_blank_version(original,runtime,[1],tmp_path/'proof');new=Path(result['new_l1_package_path'])
    assert result['physical_blank_pages_verified']==[1]and result['embedding_candidates_created']==0
    assert verify_document(new)['quality_counts']['auto_accepted']==1
    assert (new/'extraction/pages.jsonl').read_bytes()==(original/'extraction/pages.jsonl').read_bytes()
    assert (original/'l1-package.json').read_bytes()==before
    assert verify_blank_version(new,runtime)['blank_physical_pages']==[1]
    from local_vector_batch.proof_identity import verify_l1_source_readiness
    assert verify_l1_source_readiness(new,runtime)['pages'][0]['embedding_candidate_allowed']is False
    raw=[json.loads(l)for l in(new/'layout.jsonl').read_text().splitlines()];raw[0]['exact_white_blank_proof']['render_dpi']=72
    (new/'layout.jsonl').write_text(''.join(json.dumps(r)+'\n'for r in raw));manifest=json.loads((new/'l1-package.json').read_text());manifest['artifacts']['layout.jsonl']=digest((new/'layout.jsonl').read_bytes());(new/'l1-package.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):verify_blank_version(new,runtime)
