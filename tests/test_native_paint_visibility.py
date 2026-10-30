from copy import deepcopy
import json
from pathlib import Path
import pytest
from local_vector_batch.native_paint_visibility import (rules,opaque_fills,classify_glyphs,
    coverage,build,verify,sha,capture,attach_table_candidates)


POLICY=rules()['rules']['later_opaque_rectangular_fill']


def glyph(**kw):
    return dict({'glyph_key':'1:0','seqno':1,'bbox':[10,10,20,20],
       'proof_bbox':[10,10,20,20],'ink_bounds_basis':'single_character_paint_bbox',
       'type':0,'opacity':1.0,'layer':'','direction':[1.0,0.0],'wmode':0,'text':'X'},**kw)


def paint(**kw):
    return dict({'type':'f','level':0,'seqno':2,'fill_opacity':1.0,
                 'fill':[1.0,1.0,1.0],'layer':'','rectangular_path':True,'rect':[9,9,21,21]},**kw)


def test_later_opaque_full_box_is_only_removal_and_preserves_raw():
    fills,_=opaque_fills([paint()],POLICY)
    g=glyph();r=classify_glyphs([g],fills,POLICY)[0]
    assert r['excluded_from_visible_candidate'] and r['visibility_state']=='fully_occluded'
    assert all(r[k]==v for k,v in g.items())


@pytest.mark.parametrize('change',['transparent','earlier','partial','gap','nonrectangle','optional_layer',
    'complex_clip','clipped','group_alpha','blend','knockout','later_text','unproved_ink','stroke_text'])
def test_unsafe_paints_or_glyphs_are_never_removed(change):
    p=paint();g=glyph();draws=[p]
    if change=='transparent':p['fill_opacity']=0.99
    elif change=='earlier':p['seqno']=0
    elif change=='partial':p['rect']=[9,9,15,21]
    elif change=='gap':
        draws=[paint(rect=[9,9,15,21]),paint(seqno=3,rect=[15.000001,9,21,21])]
    elif change=='nonrectangle':p['rectangular_path']=False
    elif change=='optional_layer':p['layer']='optional'
    elif change in ('complex_clip','clipped'):
        draws=[{'type':'clip','level':0,'rectangular_path':change!='complex_clip','rect':[9,9,15,21]},paint(level=1)]
    elif change in ('group_alpha','blend','knockout'):
        group={'type':'group','level':0,'opacity':1.0,'blendmode':'Normal','knockout':False}
        if change=='group_alpha':group['opacity']=0.5
        elif change=='blend':group['blendmode']='Multiply'
        else:group['knockout']=True
        draws=[group,paint(level=1)]
    elif change=='later_text':g['seqno']=3
    elif change=='unproved_ink':g['ink_bounds_basis']='advance_only'
    elif change=='stroke_text':g['type']=1
    fills,_=opaque_fills(draws,POLICY)
    assert not classify_glyphs([g],fills,POLICY)[0]['excluded_from_visible_candidate']


def test_exact_union_of_multiple_later_fills_covers_without_geometry_epsilon():
    draws=[paint(rect=[9,9,15,21]),paint(seqno=3,rect=[15,9,21,21])]
    fills,_=opaque_fills(draws,POLICY)
    r=classify_glyphs([glyph()],fills,POLICY)[0]
    assert r['excluded_from_visible_candidate'] and r['coverage']['cover_seqnos']==[2,3]


def test_nan_source_geometry_rejects_instead_of_empty_partition_pass():
    with pytest.raises(ValueError):coverage([10,10,float('nan'),20],[{'seqno':2,'effective_bbox':[9,9,21,21]}])


def test_whitespace_is_retained_without_calling_advance_box_visible_ink():
    g=glyph(text=' ');fills,_=opaque_fills([paint()],POLICY)
    r=classify_glyphs([g],fills,POLICY)[0]
    assert not r['excluded_from_visible_candidate'] and r['visibility_state']=='retained_raw_whitespace'


def make_pdf(path):
    import pymupdf
    d=pymupdf.open();p=d.new_page(width=200,height=200)
    p.insert_text((40,60),'X',fontsize=16)
    p.draw_rect(pymupdf.Rect(35,35,65,70),color=None,fill=(1,1,1),overlay=True)
    p.insert_text((40,60),'Y',fontsize=16)
    d.save(path);d.close()


def test_actual_source_rederive_rejects_resealed_exclusion_and_source_change(tmp_path):
    source=tmp_path/'source.pdf';make_pdf(source);out=tmp_path/'proof'
    before=source.read_bytes()
    receipt=build(source,1,[{'id':'cell','bbox':[30,30,80,80]}],out)
    assert receipt['fully_occluded_glyphs']==1
    assert verify(out,source)['verified'] is True
    proof=json.loads((out/'proof.json').read_text())
    assert [g['text'] for g in proof['classified_glyphs'] if g['excluded_from_visible_candidate']]==['X']
    assert [g['text'] for g in proof['classified_glyphs'] if not g['excluded_from_visible_candidate']]==['Y']
    proof['classified_glyphs'][1]['excluded_from_visible_candidate']=True
    (out/'proof.json').write_text(json.dumps(proof));r=json.loads((out/'receipt.json').read_text())
    r['proof_sha256']=sha((out/'proof.json').read_bytes());(out/'receipt.json').write_text(json.dumps(r))
    with pytest.raises(ValueError):verify(out,source)
    assert source.read_bytes()==before
    changed=tmp_path/'changed.pdf';make_pdf(changed)
    with pytest.raises(ValueError):verify(out,changed)


def test_rotated_source_remains_unsupported(tmp_path):
    import pymupdf
    source=tmp_path/'r.pdf';d=pymupdf.open();p=d.new_page();p.set_rotation(90);d.save(source);d.close()
    with pytest.raises(ValueError,match='Rotated'):capture(source,1)


def test_soft_mask_resources_fail_closed(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject
    source=tmp_path/'mask.pdf';w=PdfWriter();p=w.add_blank_page(200,200)
    mask=DictionaryObject({NameObject('/S'):NameObject('/Alpha')})
    state=DictionaryObject({NameObject('/SMask'):mask})
    states=DictionaryObject({NameObject('/GS1'):state})
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/ExtGState'):states})
    w.write(source)
    with pytest.raises(ValueError,match='Soft-mask'):capture(source,1)


def test_candidate_overlay_retains_raw_layer_and_decodes_actual_source_once(tmp_path,monkeypatch):
    import local_vector_batch.native_paint_visibility as module
    source=tmp_path/'source.pdf';make_pdf(source);folder=tmp_path/'proof'
    build(source,1,[{'id':'cell','bbox':[30,30,80,80]}],folder)
    origin=tmp_path/'interpreted';origin.mkdir()
    item={'source_pdf_sha256':sha(source.read_bytes()),'page':1,'literal_sha256':sha('Y'),
          'interpretation':{'source_cell_ids':['cell'],'literal_text':'Y'},
          'original_cells':[{'id':'cell','bbox':[30,30,80,80],'text':'XY','independent_text':'XY'}],
          'original_feedback':{'correction':'Y'},'source_quality_clearance':False}
    path=origin/'candidate-001.json';path.write_text(json.dumps(item))
    (origin/'receipt.json').write_text(json.dumps({'files':{path.name:sha(path.read_bytes())}}))
    calls=[];actual=module.verify
    def wrapped(*args):calls.append(args);return actual(*args)
    monkeypatch.setattr(module,'verify',wrapped)
    result=attach_table_candidates(folder,source,origin,tmp_path/'new-candidates')
    assert len(calls)==1 and result['fresh_source_decoder_invocations']==1
    assert result['new_whole_page_clears']==0 and result['publication_eligible']is False
    candidate=json.loads((tmp_path/'new-candidates/candidate-001.json').read_text())
    assert candidate['original_cells'][0]['text']=='XY'
    assert candidate['reconstructed_cell']['text']=='Y'
    assert candidate['source_quality_clearance']is False
    path.write_text(json.dumps({**item,'literal_sha256':sha('Z')}))
    with pytest.raises(ValueError,match='evidence changed'):
        attach_table_candidates(folder,source,origin,tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()
