import hashlib,re
import pytest
from local_vector_batch.unicode_words import WORD
from local_vector_batch.quality_gate import _normalized,NUMBER,_operators,load_policy,assess_page


@pytest.mark.parametrize('a,b',[('कि','के'),('न','ना'),('है','हूँ'),('में','मैं'),('बिंदु','बिन्दु')])
def test_material_matra_and_virama_differences_are_not_collapsed(a,b):
    assert WORD.findall(_normalized(a))!=WORD.findall(_normalized(b))


def test_punctuation_and_ascii_token_membership_are_unchanged():
    s='4.4 Scope; Rate_1 = 51%, not 52.0; A/B and C-D.'
    assert WORD.findall(s)==re.findall(r'\w+',s)
    assert WORD.findall('कि। के, ना!')==['कि','के','ना']
    assert NUMBER.findall(_normalized('५१%, 52.0 and 31/03/25'))==['51%','52.0','31/03/25']
    assert _operators('not and or 51%',load_policy())==['not','and','or','%']


def test_native_match_objects_keep_exact_original_codepoint_spans():
    text='कि। के, ना!';matches=list(WORD.finditer(text))
    assert [m.group()for m in matches]==['कि','के','ना']
    assert [(m.start(),m.end())for m in matches]==[(0,2),(4,6),(8,10)]
    assert all(text[m.start():m.end()]==m.group(0)for m in matches)


def test_leading_or_isolated_combining_mark_is_retained_not_silently_dropped():
    assert WORD.findall('\u093f क')==['\u093f','क']


def test_canonical_unicode_equivalence_uses_existing_nfkc_not_lossy_stripping():
    assert WORD.findall(_normalized('cafe\u0301'))==WORD.findall(_normalized('café'))
    assert WORD.findall('a\u0305')==['a\u0305']


def test_single_matra_error_in_long_page_fails_even_below_fraction_threshold():
    selected=('यह सही है '*600)+'कि';independent=('यह सही है '*600)+'के'
    page={'page':1,'text':selected,'chars':len(selected),'text_sha256':hashlib.sha256(selected.encode()).hexdigest(),'method':'rapidocr'}
    q=assess_page(page,source_pdf_sha256='a'*64,package_pdf_sha256='a'*64,independent_text=independent)
    gate=q['components']['words_and_order']
    assert gate['state']=='fail' and gate['marked_word_tokens_exact']is False
    assert gate['word_recall']>.995 and gate['word_precision']>.995
    assert gate['reason']=='independent_combining_mark_word_disagreement'


def test_policy_cannot_turn_off_the_minimum_mark_preservation_gate(tmp_path):
    import json
    p=load_policy();p['require_exact_marked_word_tokens']=False
    path=tmp_path/'unsafe-policy.json';path.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='invalid extraction quality policy'):load_policy(path)
