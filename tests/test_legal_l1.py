from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from local_vector_batch.legal_l1 import (SCHEMA, FAMILIES, digest, extract_document,
    family_inventory, read_manifest, rows, run, structure_document, validate_structure, verify_document)


def pages(*texts):
    return [{'page':i,'text':t,'text_sha256':sha256(t.encode()).hexdigest()} for i,t in enumerate(texts,1)]


def source_fixture(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    p=tmp_path/'source.pdf';writer=PdfWriter()
    lines=[['CENTRAL ELECTRICITY REGULATORY COMMISSION',
            'Petitioner : Example Energy Private Limited', 'Record of Proceedings',
            '1. The Respondent may file its reply within two weeks.'],
           ['2. A rejoinder, if any, may be filed within two weeks thereafter.']]
    for i,body in enumerate(lines,1):
        page=writer.add_blank_page(width=612,height=842)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content='\n'.join(f'BT /F1 10 Tf 50 {800-n*20} Td ({line}) Tj ET' for n,line in enumerate(body))
        content+=f'\nBT /F1 10 Tf 50 40 Td (RoP in Petition No. 1/MP/2026) Tj ET\nBT /F1 10 Tf 50 20 Td (Page {i} of 2) Tj ET'
        stream=DecodedStreamObject();stream.set_data(content.encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(p)
    return {'id':'example','path':str(p),'sha256':digest(p.read_bytes()),'family':'record_of_proceedings','expected_pages':2,'url':'https://example.org/order.pdf'}


def test_cross_page_body_excludes_margins_without_losing_text():
    p=pages('Repeated order title for source\n1. First.\n2. Respondents must\nRoP in Petition No. 1\nPage 1 of 2',
            'Repeated order title for source\nfile replies, two weeks; rejoinder if any within two weeks thereafter.\n3. Next.\nPage 2 of 2')
    s=structure_document(p,'a'*64,'record_of_proceedings')
    validate_structure(s,p,'a'*64)
    unit=next(u for u in s['units'] if u['label']=='2')
    assert len(unit['spans'])==2
    text=''.join(a['exact_quote'] for a in unit['spans'])
    assert 'thereafter' in text and 'RoP in' not in text and 'Page 1' not in text
    assert sum(r['span']['char_length'] for r in s['regions'])==sum(len(x['text']) for x in p)


def test_duplicate_printed_labels_and_short_units_survive():
    p=pages('1. Yes.\n31. First conclusion.\n31. Separate conclusion.')
    s=structure_document(p,'a'*64,'judgment',width=10)
    assert len({u['id'] for u in s['units']})==3
    assert [u['label'] for u in s['units']].count('31')==2
    assert any(c['text']=='1. Yes.\n' for c in s['children'])
    validate_structure(s,p,'a'*64)


@pytest.mark.parametrize('family',sorted(FAMILIES))
def test_all_profiles_retain_unicode_and_unresolved_status(family):
    p=pages('1. प्रथम ₹ ९१,७५०; within 30 days — subject to appeal.\n2. No final finding.')
    s=structure_document(p,'a'*64,family)
    assert ''.join(c['text'] for c in s['children'])==p[0]['text']
    assert all(u['boundary_state']=='candidate' for u in s['units'])
    assert s['legal_status']=='unresolved'
    validate_structure(s,p,'a'*64)


@pytest.mark.parametrize('mutation',['page_hash','child_parent','clip','promote','lost_region','duplicate_unit'])
def test_structure_corruption_rejected(mutation):
    p=pages('1. Body with proviso.\n2. Next.')
    s=structure_document(p,'a'*64,'draft')
    if mutation=='page_hash':p[0]['text_sha256']='0'*64
    elif mutation=='child_parent':s['children'][0]['parent_provision_version_id']='wrong'
    elif mutation=='clip':s['units'][0]['spans'][0]['char_length']-=1
    elif mutation=='promote':s['units'][0]['boundary_state']='complete'
    elif mutation=='lost_region':s['regions']=[]
    else:s['units'].append(s['units'][0])
    with pytest.raises(ValueError):validate_structure(s,p,'a'*64)


def test_caption_roles_not_automatic_actor_facts():
    p=pages('Petitioner : Serentica Renewables India-4 Pvt Ltd\nRespondents : CTUIL\nDate of Hearing : 30.7.2026\nCoram : Chair and Members\nParties Present : Counsel\nRecord of Proceedings\n1. The Petitioner submitted a claim.\nRoP in Petition No. 26/MP/2026\nPage 1 of 1')
    s=structure_document(p,'a'*64,'record_of_proceedings')
    roles={r['role_candidate']:r['span']['exact_quote'] for r in s['family_regions']}
    assert 'CTUIL' not in roles['petitioner']
    assert 'Date of Hearing' not in roles['respondents']
    assert 'RoP in' not in roles['proceedings']
    assert all(r['status']=='review_required' for r in s['family_regions'])


def test_manifest_and_missing_dependencies(tmp_path):
    source=source_fixture(tmp_path)
    m={'schema_version':SCHEMA,'sources':[source],'families':[{'id':'sharing','required_source_ids':['example','supplement']}]}
    f=tmp_path/'manifest.json';f.write_text(json.dumps(m))
    loaded=read_manifest(f)
    assert family_inventory(loaded)[0]['missing_source_ids']==['supplement']
    m['sources'].append(source);f.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='duplicate'):read_manifest(f)


@pytest.mark.parametrize('field,value',[('id','../escape'),('family','generic_summary'),('sha256','bad'),('url','https://user:secret@example.org/x'),('expected_pages',True)])
def test_bad_manifest_rejected(tmp_path,field,value):
    source=source_fixture(tmp_path);source[field]=value
    f=tmp_path/'m.json';f.write_text(json.dumps({'schema_version':SCHEMA,'sources':[source]}))
    with pytest.raises(ValueError):read_manifest(f)


def test_real_pdf_package_replay_and_corruption(tmp_path):
    source=source_fixture(tmp_path);out=tmp_path/'out'
    result=extract_document(source,out)
    assert result['pages']==2 and not result['publication_eligible']
    raw=rows(out/'layout.jsonl');assert raw[0]['glyphs'] and raw[0]['native_text_raw'] and raw[0]['secondary_text_raw']
    assert extract_document(source,out,resume=True)['reused']
    with pytest.raises(ValueError,match='exists'):extract_document(source,out)
    with pytest.raises(ValueError,match='changed'):extract_document(source,out,language='hin',resume=True)
    (out/'layout.jsonl').write_text('[]')
    with pytest.raises(ValueError,match='hash mismatch'):verify_document(out)


def test_failed_acquisition_and_page_count_publish_nothing(tmp_path):
    source=source_fixture(tmp_path);out=tmp_path/'out'
    source['sha256']='a'*64
    with pytest.raises(ValueError,match='SHA256 mismatch'):extract_document(source,out)
    assert not out.exists()
    source['sha256']=digest(Path(source['path']).read_bytes());source['expected_pages']=99
    with pytest.raises(ValueError,match='page count'):extract_document(source,out)
    assert not out.exists()


def test_cli_full_run_and_missing_family_exit(tmp_path):
    source=source_fixture(tmp_path)
    m=tmp_path/'m.json';m.write_text(json.dumps({'schema_version':SCHEMA,'sources':[source], 'families':[{'id':'test','required_source_ids':['example','missing']}]}))
    cmd=[sys.executable,'-m','local_vector_batch','legal-l1','--manifest',str(m),'--output',str(tmp_path/'out'),'--json']
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==3,result.stderr
    report=json.loads(result.stdout);assert report['passed']==1 and not report['all_required_members_supplied']
    assert report['documents'][0]['legal_review']=='not_run'


def test_source_layout_mode_fixed_and_both_raw_representations_retained(tmp_path):
    source=source_fixture(tmp_path);out=tmp_path/'out'
    extract_document(source,out)
    raw=rows(out/'layout.jsonl')[0]
    assert raw['secondary_layout_text_raw'] is not None
    assert raw['secondary_text_raw'] is not None
    assert raw['quality_independent_basis']=='pypdf_layout_mode_fixed_before_comparison'
    q=json.loads((out/'quality.json').read_text())
    assert q['independent_extraction_mode']=='pypdf_layout_fixed_for_all_native_pages'
    assert {r['page'] for r in rows(out/'repair-queue.jsonl')}=={r['page'] for r in q['results'] if r['outcome']!='auto_accepted'}


def test_unreadable_or_blank_pdf_does_not_gain_content_readiness(tmp_path):
    from pypdf import PdfWriter
    p=tmp_path/'blank.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(p)
    source={'id':'blank','path':str(p),'sha256':digest(p.read_bytes()),'family':'notice'}
    result=extract_document(source,tmp_path/'out')
    assert result['integrity']=='passed' and not result['content_ready']
    assert not result['publication_eligible']


def test_interrupted_extractor_does_not_publish_partial_package(tmp_path,monkeypatch):
    import local_vector_batch.legal_l1 as l1
    source=source_fixture(tmp_path);target=tmp_path/'out'
    def interrupt(*args,**kwargs):raise RuntimeError('simulated extraction interruption')
    monkeypatch.setattr(l1,'extract_pdf',interrupt)
    with pytest.raises(RuntimeError,match='interruption'):extract_document(source,target)
    assert not target.exists()
    assert not list(tmp_path.glob('.out-*'))


def test_candidate_ocr_method_cannot_borrow_native_alignment_as_proof(tmp_path,monkeypatch):
    import local_vector_batch.legal_l1 as l1
    source=source_fixture(tmp_path)
    # Exercise the raw capture adapter on actual PDF glyphs with an OCR-selected record.
    p=pages('OCR text with a very different reading order.','Second OCR page.')
    for record in p:record['method']='vision'
    raw=l1.capture_layout(Path(source['path']),p)
    assert all(r['geometry_status']=='native_geometry_not_ocr_geometry' for r in raw)
    assert any(op['kind']!='equal' for r in raw for op in r['alignment'])


def test_sequential_bare_page_numbers_are_retained_as_margins_not_body():
    p=pages('1. Contract entered before the date\n1','and furnished before the filing cutoff.\n2')
    s=structure_document(p,'a'*64,'draft')
    text=''.join(c['text'] for c in s['children'])
    assert text=='1. Contract entered before the date\nand furnished before the filing cutoff.\n'
    assert [r['span']['exact_quote'] for r in s['regions'] if r['kind']=='footer_candidate']==['1','2']
    validate_structure(s,p,'a'*64)


def test_one_page_numeric_body_is_not_assumed_a_footer():
    p=pages('1. The quantity is\n1')
    s=structure_document(p,'a'*64,'notice')
    assert not any(r['kind']=='footer_candidate' for r in s['regions'])
    validate_structure(s,p,'a'*64)


def test_same_pdf_corrected_extraction_changes_unit_and_child_version_ids():
    old=structure_document(pages('1. Pay 91,750.'),'a'*64,'interim_order')
    new=structure_document(pages('1. Pay 91,750,000.'),'a'*64,'interim_order')
    assert old['units'][0]['id']!=new['units'][0]['id']
    assert old['children'][0]['id']!=new['children'][0]['id']


def test_no_silent_schema_or_candidate_promotion():
    p=pages('1. A draft only.')
    s=structure_document(p,'a'*64,'draft');s['legal_status']='in_force'
    with pytest.raises(ValueError,match='promoted'):validate_structure(s,p,'a'*64)


def test_unit_readiness_needs_every_page_and_never_implies_legal_review():
    from local_vector_batch.legal_l1 import readiness_ledger
    p=pages('1. Subject to the condition','on the following page.')
    structure=structure_document(p,'a'*64,'interim_order')
    ledger=readiness_ledger(structure,{'results':[{'page':1,'outcome':'auto_accepted'},{'page':2,'outcome':'repair_required'}]})
    assert ledger['units'][0]['source_quality']=='held'
    assert not ledger['units'][0]['eligible_for_legal_assertion']
    assert not ledger['publication_eligible']
