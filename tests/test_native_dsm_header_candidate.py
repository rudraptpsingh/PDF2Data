from copy import deepcopy
import pytest
from local_vector_batch import native_dsm_header_candidate as m


def fixture(tmp_path, monkeypatch):
    source=tmp_path/'source.pdf'; source.write_bytes(b'source')
    monkeypatch.setattr(m,'SOURCE',m._hash(b'source'))
    text=m.HEADER+'\nrest of the page'
    monkeypatch.setattr(m,'PAGE_SHA',m._hash(text.encode()))
    evidence={'glyphs':[{'text':'ि','font':'BCDEEE+ArialUnicodeMS','bbox':list(m.BOX)}],
              'font_sha256':m.FONT_SHA,'cmap_sha256':m.CMAP_SHA,'crop_sha256':m.CROP_SHA,
              'source_cid':'057E','font_glyph_for_ja':0x057e,'font_glyph_for_vowel_i':0x059f,
              'source_text_object_sha256':'25c4e33c0201a60456d7b6583ce644ba63026cd19d0ebe18450c85caf34fbf31'}
    monkeypatch.setattr(m,'_observe',lambda _:deepcopy(evidence))
    return source,text,evidence


def test_exact_candidate_retains_parent_and_never_approves(tmp_path,monkeypatch):
    source,text,_=fixture(tmp_path,monkeypatch);r=m.propose(source,13,text)
    assert r['candidate_page_text']==text.replace('रािपत्र','राजपत्र')
    assert text[r['selected_start']:r['selected_end']]=='ि'
    assert not r['source_quality_approval_granted'] and not r['publication_eligible']
    assert r['independent_linguistic_review']=='not_run'


@pytest.mark.parametrize('kind',['source','page','text','glyph','font','box','font_hash','cmap','crop','cid','glyph_map','object','duplicate'])
def test_outside_scope_or_changed_evidence_fails_closed(tmp_path,monkeypatch,kind):
    source,text,evidence=fixture(tmp_path,monkeypatch);page=13
    if kind=='source':source.write_bytes(b'other')
    elif kind=='page':page=14
    elif kind=='text':text+='changed'
    elif kind=='glyph':evidence['glyphs'][0]['text']='ज'
    elif kind=='font':evidence['glyphs'][0]['font']='other'
    elif kind=='box':evidence['glyphs'][0]['bbox'][0]+=.1
    elif kind=='font_hash':evidence['font_sha256']='0'*64
    elif kind=='cmap':evidence['cmap_sha256']='0'*64
    elif kind=='crop':evidence['crop_sha256']='0'*64
    elif kind=='cid':evidence['source_cid']='059F'
    elif kind=='glyph_map':evidence['font_glyph_for_ja']=0x059f
    elif kind=='object':evidence['source_text_object_sha256']='0'*64
    else:evidence['glyphs']*=2
    with pytest.raises(ValueError):m.propose(source,page,text)
