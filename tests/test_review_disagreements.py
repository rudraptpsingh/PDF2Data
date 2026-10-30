import json
from local_vector_batch.review_disagreements import discrepancies, feedback_summary


def test_number_difference_has_literal_excerpts_and_only_unique_source_geometry():
    a='The deadline is 15 days.\n'; b='The deadline is 10 days.\n'
    page={'text': a, 'ocr': {'bbox_coordinates': 'normalized top-left', 'lines': [{'text': 'The deadline is 15 days.', 'bbox': [0.1,0.3,0.8,0.4]}]}}
    rows=discrepancies(a,b,page)
    assert rows[0]['kind']=='number'
    focus=rows[0]['focus']
    assert focus['selected_excerpt'] in a and focus['independent_excerpt'] in b
    assert len(focus['boxes']) == 1 and focus['boxes'][0][0] <= .1 and focus['boxes'][0][2] >= .8
    page['text']=a+a
    assert discrepancies(a+a,b+b,page)[0]['focus']['boxes'] == []


def test_footnote_difference_and_date_are_classified():
    f=discrepancies('Act, 2008.]', 'Act, 2008.]5', {'text':'Act, 2008.]'})
    assert f[0]['kind'] == 'footnote_candidate'
    d=discrepancies('Due on 15.03.2026.', 'Due on 16.03.2026.', {'text':'Due on 15.03.2026.'})
    assert d[0]['kind'] == 'date'


def test_feedback_counts_latest_identity_without_calling_it_page_approval(tmp_path):
    from local_vector_batch.review_queue import load_item, record_decision
    # Feedback summary handles old unclassified entries as well as new types.
    key={'source_pdf_sha256':'a'*64,'page':1,'selected_text_sha256':'b'*64,
         'independent_text_sha256':'c'*64,'prompt_sha256':'d'*64,'focus_sha256':'e'*64}
    rows=[{'kind':'ocr_disagreement_feedback', **key, 'decision':'selected','created_at':'2026-01-01','disagreement_kind':'number'},
          {'kind':'ocr_disagreement_feedback', **key, 'decision':'unclear','created_at':'2026-01-02','disagreement_kind':'number'},
          {'kind':'bad'}]
    path=tmp_path/'labels.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    summary=feedback_summary([path])
    assert summary['feedback_entries']==1 and summary['decisions']=={'unclear':1}
    assert summary['disagreement_types']=={'number':1} and summary['invalid_entries']==1
    assert 'not a page approval' in summary['interpretation']


def test_url_date_noise_is_not_promoted_to_a_legal_date_question():
    a='Source: https://example.gov.in/2025/order.pdf'
    b='Source: https://example.gov.in/2026/order.pdf'
    assert discrepancies(a, b, {'text': a}) == []


def test_wrapped_pdf_url_date_is_not_prioritized_and_legacy_feedback_is_counted(tmp_path):
    a='See listing\n17%28FINAL%29%2011.4.2015.pdf\n'
    b='See listing\n17%28FINAL%29%2011.5.2015.pdf\n'
    assert discrepancies(a, b, {'text':a}) == []
    row={'kind':'ocr_disagreement_feedback','source_pdf_sha256':'a'*64,'page':1,
         'selected_text_sha256':'b'*64,'independent_text_sha256':'c'*64,
         'prompt_sha256':'d'*64,'decision':'selected','created_at':'2026-01-01'}
    path=tmp_path/'old.jsonl';path.write_text(json.dumps(row)+'\n')
    summary=feedback_summary([path])
    assert summary['feedback_entries']==1 and summary['legacy_unfocused_entries']==1
    assert summary['disagreement_types']=={'unclassified':1} and summary['invalid_entries']==0


def test_clause_marker_and_math_spacing_are_separate_source_bound_questions():
    a = '(ii) Prior duty.\n(111) Missing obligation applies.\n(iv) Next duty.\nCharge 500+250 =750 MW.'
    b = '(ii) Prior duty.\n(iv) Next duty.\nCharge 500+250=750 MW.'
    page = {'text': a, 'ocr': {'bbox_coordinates': 'normalized top-left', 'lines': [
        {'text':'(111) Missing obligation applies.', 'bbox':[.1,.2,.8,.3]},
        {'text':'Charge 500+250 =750 MW.', 'bbox':[.1,.4,.8,.5]}]}}
    rows = discrepancies(a,b,page,max_items=64)
    enum = next(r for r in rows if r['kind']=='enumerator_candidate')
    math = next(r for r in rows if r['kind']=='math_spacing')
    assert enum['focus']['boxes'] and '(111)' in enum['focus']['selected_excerpt']
    assert math['focus']['boxes'] and math['focus']['selected_excerpt']=='500+250 =750'
    assert math['focus']['independent_excerpt']=='500+250=750'
    assert ''.join(math['focus']['selected_excerpt'].split()) == ''.join(math['focus']['independent_excerpt'].split())


def test_formula_operator_and_currency_unit_are_separate_without_inventing_symbols():
    a='Rate in ?/MW/month.\n[10% Charges] [Total Approved]\nRate (in /MW/month).'
    b='Rate in /MW/month.\n[10% Charges]/ [Total Approved]\nRate (in Z/MW/month).'
    page={'text':a,'ocr':{'bbox_coordinates':'normalized top-left','lines':[
      {'text':'Rate in ?/MW/month.','bbox':[.1,.1,.8,.15]},
      {'text':'[10% Charges] [Total Approved]','bbox':[.1,.2,.8,.25]},
      {'text':'Rate (in /MW/month).','bbox':[.1,.3,.8,.35]}]}}
    rows=discrepancies(a,b,page,max_items=64)
    operator=[r for r in rows if r['kind']=='math_operator']
    currency=[r for r in rows if r['kind']=='currency_unit_candidate']
    assert len(operator)==1 and len(currency)==2
    assert operator[0]['focus']['boxes'] and all(r['focus']['boxes'] for r in currency)
    assert ']/ [' in operator[0]['focus']['independent_excerpt']
    assert any('Z/MW/month' in r['focus']['independent_excerpt'] for r in currency)
    assert all('₹' not in r['focus']['selected_excerpt'] and '₹' not in r['focus']['independent_excerpt'] for r in currency)
