"""Invisible backgrounds cannot waive text, paint, columns or raw-table guards."""
import hashlib,json
from pathlib import Path
import pytest


def source_pdf(path,*,stroke=False,color=1.0,columns=1,occluded=False,one_background=False,footer=None):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=240,height=320)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    backgrounds=[];text=[]
    for r in range(2):
        for c in range(columns):
            x=30+c*180/columns;y=220-r*60;w=180/columns
            backgrounds.append(f'{color} g 0 G .6 w {x} {y} {w} 60 re '+('B'if stroke else'f'))
            text.append(f'0 g BT /F1 10 Tf {x+10} {y+30} Td ('+('ALPHA'if c==0 else'BETA')+') Tj ET')
    if one_background:backgrounds=[f'{color} g 30 160 180 120 re f']
    if footer:
        y=150 if footer=='body'else 20
        height=5 if footer=='thick'else 3
        backgrounds.append(f'0.4 g 30 {y} 180 {height} re f')
        if footer=='overlap':text.append('0 g BT /F1 10 Tf 40 20 Td (Footer) Tj ET')
    content='\n'.join((text+backgrounds)if occluded else(backgrounds+text));stream=DecodedStreamObject();stream.set_data(content.encode());page[NameObject('/Contents')]=writer._add_object(stream);writer.write(path)


def parent(tmp_path,**options):
    from local_vector_batch.legal_l1 import extract_document
    path=tmp_path/'source.pdf';source_pdf(path,**options);folder=tmp_path/'parent'
    extract_document({'id':'synthetic-background-only','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                      'family':'draft','url':'https://example.org/synthetic.pdf','expected_pages':1},folder)
    return folder


def test_white_backgrounds_reclassify_actual_prose_and_preserve_every_raw_value(tmp_path):
    from local_vector_batch.native_whitefill_prose import derive,build_version
    from local_vector_batch.legal_l1 import verify_document
    original=parent(tmp_path);tables=(original/'extraction/tables.json').read_bytes();assert json.loads(tables)
    proof,quality=derive(original,1)
    assert all(proof['predicates'].values())and quality['outcome']=='auto_accepted'
    assert all(c['state']=='pass'for c in quality['components'].values())
    result=build_version(original,Path(__file__).resolve().parents[1],1,tmp_path/'proof')
    assert result['status']=='passed'and result['fresh_decoder']['fresh_registered_source_decoder']
    dest=Path(result['new_l1_package_path']);verify_document(dest)
    assert(dest/'extraction/tables.json').read_bytes()==tables
    assert(dest/'extraction/pages.jsonl').read_bytes()==(original/'extraction/pages.jsonl').read_bytes()


@pytest.mark.parametrize('options,reason',[
    ({'stroke':True},'object predicates'),({'color':.999},'object predicates'),
    ({'columns':2},'source predicates'),({'occluded':True},'source predicates')])
def test_real_strokes_nearwhite_columns_and_overpainted_text_remain_held(tmp_path,options,reason):
    from local_vector_batch.native_whitefill_prose import derive
    original=parent(tmp_path,**options)
    with pytest.raises(ValueError,match=reason):derive(original,1)


def test_resealed_forged_background_classification_rejected_by_fresh_decoder(tmp_path):
    from local_vector_batch.native_whitefill_prose import build_version,verify_version
    from local_vector_batch.legal_l1 import rows,write_rows,verify_document
    original=parent(tmp_path);runtime=Path(__file__).resolve().parents[1]
    built=build_version(original,runtime,1,tmp_path/'proof',external_decoder=True);assert built['status'].startswith('blocked_')
    package=Path(built['new_l1_package_path']);layout=rows(package/'layout.jsonl');layout[0]['native_whitefill_prose_proof']['raw_tables_retained'][0]['cells'][0][0]='FORGED';write_rows(package/'layout.jsonl',layout)
    manifest=json.loads((package/'l1-package.json').read_text());manifest['artifacts']['layout.jsonl']=hashlib.sha256((package/'layout.jsonl').read_bytes()).hexdigest();(package/'l1-package.json').write_text(json.dumps(manifest))
    verify_document(package)
    with pytest.raises(ValueError,match='fresh original'):verify_version(package,runtime)


def test_same_configured_prose_rule_preserves_an_empty_raw_matrix_list(tmp_path):
    from local_vector_batch.native_whitefill_prose import derive,build_version
    original=parent(tmp_path,one_background=True);raw=(original/'extraction/tables.json').read_bytes();assert json.loads(raw)==[]
    proof,quality=derive(original,1)
    assert proof['raw_tables_retained']==[]and proof['rule_version']==4
    assert quality['outcome']=='auto_accepted'and all(v['state']=='pass'for v in quality['components'].values())
    with pytest.raises(ValueError,match='Already accepted'):build_version(original,Path(__file__).resolve().parents[1],1,tmp_path/'no-redundant-clearance')


def test_representation_requires_fresh_registered_exact_reproduction(monkeypatch):
    from local_vector_batch.native_whitefill_prose import selected_representation
    from local_vector_batch import native_consensus
    raw='GNA and T-GNA apply\nRE RE';selected='GNARE and T-GNARE apply';calls=[]
    def repair(*args):calls.append(args);return selected,[{'basis':'two_native_readers_plus_exact_source_character_conservation'}]
    monkeypatch.setattr(native_consensus,'repair_source_lines',repair)
    ok,proof=selected_representation(Path('source.pdf'),object(),2,raw,selected,selected,{'allow_registered_source_line_representation':True})
    assert ok and calls and proof['repairs']and proof['selected_text_modified']is False
    assert not selected_representation(Path('source.pdf'),object(),2,raw,selected,selected,{'allow_registered_source_line_representation':False})[0]
    monkeypatch.setattr(native_consensus,'repair_source_lines',lambda *args:(selected,[]))
    assert not selected_representation(Path('source.pdf'),object(),2,raw,selected,selected,{'allow_registered_source_line_representation':True})[0]


def test_digit_or_condition_loss_never_qualifies_as_representation(monkeypatch):
    from local_vector_batch.native_whitefill_prose import selected_representation
    from local_vector_batch import native_consensus
    monkeypatch.setattr(native_consensus,'repair_source_lines',lambda *args:pytest.fail('Changed content must reject before repair'))
    assert not selected_representation(Path('source.pdf'),object(),1,'Pay 100; not final.','Pay 10; final.','',{'allow_registered_source_line_representation':True})[0]


def test_footer_rule_requires_independent_full_page_proof_and_retains_raw_tables(tmp_path):
    from local_vector_batch.native_whitefill_prose import derive,build_version
    original=parent(tmp_path,footer='valid');raw=(original/'extraction/tables.json').read_bytes()
    proof,quality=derive(original,1)
    assert quality['outcome']=='auto_accepted'and proof['footer_rule_rectangles']
    assert all(proof['predicates'].values())and proof['raw_tables_retained']==json.loads(raw)
    built=build_version(original,Path(__file__).resolve().parents[1],1,tmp_path/'child')
    assert built['fresh_decoder']['fresh_registered_source_decoder']
    assert(Path(built['new_l1_package_path'])/'extraction/tables.json').read_bytes()==raw


@pytest.mark.parametrize('footer',['body','thick','overlap'])
def test_body_rules_thick_rules_and_glyph_overlap_remain_held(tmp_path,footer):
    from local_vector_batch.native_whitefill_prose import derive
    original=parent(tmp_path,footer=footer)
    with pytest.raises(ValueError,match='object predicates'):derive(original,1)


def test_public_reproof_output_cannot_change_parent_or_origin(tmp_path):
    from local_vector_batch.native_whitefill_prose import build_version
    original=parent(tmp_path);runtime=Path(__file__).resolve().parents[1]
    before=(original/'l1-package.json').read_bytes()
    for output in (original/'nested-output',runtime/'.qa/unsafe-output'):
        with pytest.raises(ValueError,match='outside parent and source runtime'):build_version(original,runtime,1,output)
        assert not output.exists()
    assert (original/'l1-package.json').read_bytes()==before
