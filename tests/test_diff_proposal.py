from hashlib import sha256
from local_vector_batch.diff_proposal import propose_diff, DiffPolicy


def example():
    crop=b'original crop'
    item={'source_pdf_sha256':'a'*64,'page':3,'selected_text_sha256':'b'*64,'independent_text_sha256':'c'*64,'focus_sha256':'d'*64}
    receipt={**item,'crop_sha256':sha256(crop).hexdigest(),'kind':'enumerator_candidate',
      'selected_diff':'the regulation (1) applies to all', 'independent_diff':'the regulation (i) applies to all',
      'rapid_text':'the regulation (i) applies to all', 'vlm_text':'the regulation (i) applies to all'}
    return item,receipt,crop

def test_complete_aligned_agreement_is_diagnostic_only():
    item,receipt,crop=example(); result=propose_diff(receipt,item,crop)
    assert result['choice']=='independent' and result['publication_allowed'] is False

def test_partial_omitted_or_disagreeing_reader_abstains():
    item,r,crop=example()
    for changes in ({'selected_diff':''}, {'vlm_text':'regulation (i) applies'}, {'rapid_text':'the regulation (1) applies to all'}, {'independent_diff':'the regulation (i) applies all'}, {'independent_diff':'the regulation ₹ applies to all'}):
        assert propose_diff({**r,**changes},item,crop)['choice'] is None

def test_mismatched_crop_and_type_abstain():
    item,r,crop=example()
    assert propose_diff(r,item,b'changed')['choice'] is None
    assert propose_diff({**r,'kind':'math_spacing'},item,crop)['choice'] is None
    assert propose_diff({**r,'focus_sha256':'e'*64},item,crop)['choice'] is None


def test_single_token_requires_unique_shared_context():
    item,r,crop=example()
    item['focus']={'selected_excerpt':'the regulation (1) applies to all','independent_excerpt':'the regulation (i) applies to all'}
    r.update(selected_diff='(1)',independent_diff='(i)')
    assert propose_diff(r,item,crop)['choice']=='independent'
    item['focus']['selected_excerpt']='the regulation (1) applies to all (1)'
    assert propose_diff(r,item,crop)['choice'] is None


def test_roman_clause_omission_must_not_be_guessed():
    item,r,crop=example()
    item['focus']={'selected_excerpt':'list heading (ii) next clause text','independent_excerpt':'list heading next clause text'}
    r.update(selected_diff='(ii)', independent_diff='', vlm_text='list heading (iii) next clause text', rapid_text='list heading (iii) next clause text')
    assert propose_diff(r,item,crop)['choice'] is None


def test_handwriting_omission_spelling_and_abbreviation_are_not_inferred():
    item,r,crop=example()
    cases=[{'kind':'handwriting','selected_diff':'Annexure','independent_diff':'Annexure 7'},
           {'selected_diff':'(2) The notice may be served','independent_diff':''},
           {'selected_diff':'domestic','independent_diff':'aomestic'},
           {'selected_diff':'BESCOM','independent_diff':'BESCO'}]
    for change in cases:
        assert propose_diff({**r,**change},item,crop)['choice'] is None


def test_date_source_identity_and_unsupported_unicode():
    item,r,crop=example()
    item['focus']={'selected_excerpt':'the order dated 01.05.2015 applies today','independent_excerpt':'the order dated 01.05.20l5 applies today'}
    r.update(kind='number',selected_diff='01.05.2015',independent_diff='01.05.20l5',
             vlm_text='the order dated 01.05.2015 applies today',rapid_text='the order dated 01.05.2015 applies today')
    assert propose_diff(r,item,crop)['choice']=='selected'
    r['rapid_text']='the order dated 01.05.20l5 applies today'
    assert propose_diff(r,item,crop)['choice'] is None
