import hashlib
import json
from pathlib import Path
import pytest
from local_vector_batch.review_queue import load_item, record_decision, compare_lines, latest_feedback


def sha(data):
    return hashlib.sha256(data).hexdigest()


def queue(tmp_path):
    pdf = tmp_path / 'source.pdf'; pdf.write_bytes(b'original source PDF')
    selected = tmp_path / 'selected.txt'; selected.write_text('Clause 5\n', encoding='utf-8')
    independent = tmp_path / 'independent.json'; independent.write_text(json.dumps({'text': 'Clause 6\n', 'engine': 'example'}))
    manifest = tmp_path / 'queue.jsonl'
    manifest.write_text(json.dumps({'source_pdf': str(pdf), 'source_pdf_sha256': sha(pdf.read_bytes()),
        'page': 2, 'title': 'Example', 'prompt': 'Which clause number is visible?',
        'selected_text': str(selected), 'selected_text_sha256': sha(selected.read_bytes()),
        'independent_json': str(independent), 'independent_text_sha256': sha(b'Clause 6\n')}) + '\n')
    return manifest, pdf, selected


def test_review_is_bound_to_source_page_text_and_question_and_never_promotes(tmp_path):
    manifest, pdf, selected = queue(tmp_path)
    item = load_item(manifest, 0)
    request = {key: item[key] for key in ('index', 'source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256')}
    request.update(decision='independent', note='Visible in source')
    out = tmp_path / 'feedback.jsonl'
    receipt = record_decision(manifest, out, request)
    assert receipt['promotes_page'] is False and json.loads(out.read_text()) == receipt
    assert latest_feedback(out, item)['decision'] == 'independent'
    assert latest_feedback(out, {**item, 'prompt_sha256': 'changed'}) is None
    for field in ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256'):
        with pytest.raises(ValueError, match='reload'):
            record_decision(manifest, out, {**request, field: 'changed'})
    selected.write_text('different')
    with pytest.raises(ValueError, match='OCR text changed'):
        load_item(manifest, 0)
    pdf.write_bytes(b'other PDF')
    with pytest.raises(ValueError, match='source PDF changed'):
        load_item(manifest, 0)


def test_difference_segments_preserve_untrusted_text():
    a, b = compare_lines('before\n<script>5</script>\n', 'before\n<script>6</script>\n')
    assert ''.join(x['text'] for x in a) == 'before\n<script>5</script>\n'
    assert ''.join(x['text'] for x in b) == 'before\n<script>6</script>\n'
    assert any(x['changed'] for x in a) and any(x['changed'] for x in b)


def test_queue_generator_binds_current_held_pages_and_does_not_overwrite_inputs(tmp_path, monkeypatch):
    from local_vector_batch.review_queue import generate_review_queue
    pdf = tmp_path / 'source.pdf'; pdf.write_bytes(b'pdf source')
    source_sha = sha(pdf.read_bytes())
    package = tmp_path / 'package'; package.mkdir()
    (package / 'evidence-package.json').write_text(json.dumps({'document': {'pdf_sha256': source_sha, 'title': 'Order'}}))
    text = 'Original page text'; text_sha = sha(text.encode())
    (package / 'pages.jsonl').write_text(json.dumps({'page': 1, 'text': text, 'text_sha256': text_sha, 'status': 'review_required', 'method': 'vision'}) + '\n')
    quality = tmp_path / 'quality.json'; quality.write_text(json.dumps({'source_pdf_sha256': source_sha, 'results': [{'page': 1, 'outcome': 'repair_required', 'page_text_sha256': text_sha, 'failed_components': ['ordered_numbers']}]}))
    manifest = tmp_path / 'jobs.jsonl'; manifest.write_text(json.dumps({'package': str(package), 'source_pdf': str(pdf), 'pages': [1], 'quality_report': str(quality)}) + '\n')
    independent = tmp_path / 'ocr.jsonl'; alt = 'Original page text 5'
    independent.write_text(json.dumps({'source_pdf_sha256': source_sha, 'page': 1, 'selected_page_text_sha256': text_sha, 'vision': {'engine': 'vision'}, 'independent': {'engine': 'other', 'text': alt, 'text_sha256': sha(alt.encode())}}) + '\n')
    monkeypatch.setattr('local_vector_batch.review_queue._bound_independent_text', lambda *args, **kwargs: alt)
    out = tmp_path / 'review'
    result = generate_review_queue(manifest, independent, out, limit=1)
    assert result['created'] == 1
    item = load_item(Path(result['output']), 0)
    assert item['source_pdf_sha256'] == source_sha and item['page'] == 1
    assert item['selected_text'] == text and item['independent_text'] == alt
    assert 'number' in item['prompt'] and item['focus']['kind'] == 'number'
    request = {key: item[key] for key in ('index', 'source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256')}
    feedback = tmp_path / 'feedback.jsonl'
    record_decision(Path(result['output']), feedback, {**request, 'decision': 'unclear'})
    from local_vector_batch.review_disagreements import feedback_summary
    assert feedback_summary([feedback])['disagreement_types'] == {'number': 1}
    after = generate_review_queue(manifest, independent, tmp_path / 'review-next', limit=1, reviewed=[feedback])
    assert after['created'] == 0 and after['skipped']['already_reviewed_difference'] == 1
    with pytest.raises(ValueError, match='already exists'):
        generate_review_queue(manifest, independent, out, limit=1)
    assert pdf.read_bytes() == b'pdf source'


def test_queue_generator_skips_stale_quality_and_ocr(tmp_path):
    from local_vector_batch.review_queue import generate_review_queue
    pdf = tmp_path / 'source.pdf'; pdf.write_bytes(b'pdf'); source_sha = sha(pdf.read_bytes())
    package = tmp_path / 'package'; package.mkdir()
    (package / 'evidence-package.json').write_text(json.dumps({'document': {'pdf_sha256': source_sha}}))
    (package / 'pages.jsonl').write_text(json.dumps({'page': 1, 'text': 'current', 'text_sha256': sha(b'current'), 'status': 'review_required'}) + '\n')
    quality = tmp_path / 'quality.json'; quality.write_text(json.dumps({'source_pdf_sha256': source_sha, 'results': [{'page': 1, 'outcome': 'repair_required', 'page_text_sha256': 'stale'}]}))
    manifest = tmp_path / 'jobs.jsonl'; manifest.write_text(json.dumps({'package': str(package), 'source_pdf': str(pdf), 'quality_report': str(quality)}) + '\n')
    independent = tmp_path / 'ocr.jsonl'; independent.write_text('')
    result = generate_review_queue(manifest, independent, tmp_path / 'review')
    assert result['created'] == 0 and result['skipped'] == {'not_current_held_quality': 1}
    assert not (tmp_path / 'review').exists()


def test_focus_requires_source_space_boxes_and_literal_ocr_excerpts(tmp_path):
    manifest, _, _ = queue(tmp_path)
    row = json.loads(manifest.read_text())
    row['focus'] = {'boxes': [[0.1, 0.2, 0.8, 0.4]],
                    'selected_excerpt': 'Clause 5', 'independent_excerpt': 'Clause 6'}
    manifest.write_text(json.dumps(row) + '\n')
    item = load_item(manifest, 0)
    assert item['focus']['selected_excerpt'] == 'Clause 5'
    old_sha = item['focus_sha256']
    row['focus']['boxes'] = [[0.1, 0.2, 0.9, 0.4]]
    manifest.write_text(json.dumps(row) + '\n')
    assert load_item(manifest, 0)['focus_sha256'] != old_sha
    row['focus']['independent_excerpt'] = 'invented'
    manifest.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='not in the OCR'):
        load_item(manifest, 0)
    row['focus']['independent_excerpt'] = 'Clause 6'
    row['focus']['boxes'] = [[0.8, 0.2, 0.1, 0.4]]
    manifest.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='focus box'):
        load_item(manifest, 0)


def test_hindi_clause_wrap_is_display_only_and_preserves_headings_and_numbers():
    from local_vector_batch.review_queue import normalize_hindi_clause_wraps
    body = 'आवेदक को आयोग के समक्ष निर्धारित समय अवधि में आवेदन प्रस्तुत करना होगा और सभी आवश्यक दस्तावेज संलग्न करने होंगे।'
    raw = '(2)\n' + body + '\n(3)\nप्रयोज्यता\n(4)\nLONG ENGLISH SECTION HEADING THAT MUST REMAIN ON A SEPARATE LINE\n'
    out = normalize_hindi_clause_wraps(raw)
    assert out.startswith('(2) ' + body)
    assert '(3)\nप्रयोज्यता' in out and '(4)\nLONG ENGLISH' in out
    assert ''.join(out.split()) == ''.join(raw.split())
    hindi_heading = '(5)\n' + ('नियमों के अंतर्गत महत्वपूर्ण विस्तृत विशेष प्रावधानों की सूची ' * 2).strip() + ':\n'
    assert normalize_hindi_clause_wraps(hindi_heading) == hindi_heading


def test_hindi_display_normalization_does_not_mask_numeric_disagreement():
    from local_vector_batch.review_queue import normalize_hindi_clause_wraps, compare_lines
    phrase = 'आवेदक को आयोग के समक्ष निर्धारित समय अवधि में आवेदन प्रस्तुत करना होगा और सभी आवश्यक दस्तावेज संलग्न करने होंगे '
    a = '(2)\n' + phrase + '15 दिन।'
    b = '(2)\n' + phrase + '10 दिन।'
    x, y = compare_lines(normalize_hindi_clause_wraps(a), normalize_hindi_clause_wraps(b))
    assert any('15' in s['text'] and s['changed'] for s in x)
    assert any('10' in s['text'] and s['changed'] for s in y)


def test_feedback_hints_create_distinct_enumerator_and_equation_reviews(tmp_path, monkeypatch):
    from local_vector_batch.review_queue import generate_review_queue
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'pdf');source_sha=sha(pdf.read_bytes())
    package=tmp_path/'package';package.mkdir()
    (package/'evidence-package.json').write_text(json.dumps({'document':{'pdf_sha256':source_sha}}))
    a='(ii) Prior duty.\n(111) Missing obligation applies.\n(iv) Next duty.\nCharge 500+250 =750 MW.'
    b='(ii) Prior duty.\n(iv) Next duty.\nCharge 500+250=750 MW.'
    (package/'pages.jsonl').write_text(json.dumps({'page':1,'text':a,'text_sha256':sha(a.encode()),'status':'review_required','method':'vision'})+'\n')
    jobs=tmp_path/'jobs.jsonl';jobs.write_text(json.dumps({'package':str(package),'source_pdf':str(pdf),'pages':[1]})+'\n')
    ocr=tmp_path/'ocr.jsonl';ocr.write_text(json.dumps({'source_pdf_sha256':source_sha,'page':1,'selected_page_text_sha256':sha(a.encode()),'vision':{'engine':'vision'},'independent':{'engine':'rapidocr','text':b,'text_sha256':sha(b.encode())}})+'\n')
    feedback=tmp_path/'feedback.jsonl'; feedback.write_text(json.dumps({'kind':'ocr_disagreement_feedback','source_pdf_sha256':source_sha,'page':1,'selected_text_sha256':sha(a.encode()),'independent_text_sha256':sha(b.encode()),'prompt_sha256':'c'*64,'focus_sha256':'d'*64,'decision':'neither','note':'Roman numeral unclear; math equation has extra space','created_at':'2026-01-01'})+'\n')
    monkeypatch.setattr('local_vector_batch.review_queue._bound_independent_text',lambda *args,**kwargs:b)
    result=generate_review_queue(jobs,ocr,tmp_path/'review',reviewed=[feedback])
    rows=[json.loads(l) for l in Path(result['output']).read_text().splitlines()]
    assert result['created']==2 and {r['focus']['kind'] for r in rows}=={'enumerator_candidate','math_spacing'}
    math=next(r for r in rows if r['focus']['kind']=='math_spacing')
    assert math['focus']['selected_excerpt']=='500+250 =750' and math['focus']['independent_excerpt']=='500+250=750'
    assert (package/'pages.jsonl').read_text().find('500+250 =750') >= 0


def test_current_diff_uses_exact_span_and_marks_missing_without_editing_text():
    from local_vector_batch.review_queue import focus_segments
    left='[A] [B]'; right='[A]/ [B]'
    a=focus_segments(left,left,[4,4]); b=focus_segments(right,right,[3,4])
    assert any(x.get('missing') and x['text']=='∅' for x in a)
    assert any(x['changed'] and x['text']=='/' for x in b)
    assert ''.join(x['text'] for x in a if not x.get('missing')) == left
    assert ''.join(x['text'] for x in b if not x.get('missing')) == right


def test_per_diff_proposal_is_source_bound_and_disagreement_requires_reason(tmp_path, monkeypatch):
    from local_vector_batch.review_queue import review_group
    manifest, _, _ = queue(tmp_path)
    row=json.loads(manifest.read_text())
    row['focus']={'kind':'number','boxes':[[.1,.1,.8,.3]],'selected_excerpt':'Clause 5','independent_excerpt':'Clause 6',
                  'selected_span':[7,8],'independent_span':[7,8]}
    row['proposal']={'choice':'independent','basis':'source_observation','reason':'The displayed crop appears to show 6.','crop_sha256':sha(b'crop')}
    other={**row,'prompt':'Check punctuation','focus':{'kind':'text_change','boxes':[],'selected_excerpt':'Clause 5','independent_excerpt':'Clause 6','selected_span':[0,6],'independent_span':[0,6]}}
    other.pop('proposal')
    manifest.write_text(json.dumps(row)+'\n'+json.dumps(other)+'\n')
    monkeypatch.setattr('local_vector_batch.review_queue.render_page',lambda *a,**kw:b'crop')
    item=load_item(manifest,0)
    request={key:item[key] for key in ('index','source_pdf_sha256','page','selected_text_sha256','independent_text_sha256','prompt_sha256','focus_sha256','proposal_sha256')}
    output=tmp_path/'feedback.jsonl'
    with pytest.raises(ValueError,match='reason'):
        record_decision(manifest,output,{**request,'decision':'selected'})
    result=record_decision(manifest,output,{**request,'decision':'selected','note':'The crop appears to show 5'})
    assert result['proposal_choice']=='independent' and result['promotes_page'] is False
    group=review_group(manifest,0,output)
    assert len(group)==2 and group[0]['reviewed'] and not group[1]['reviewed']
    row['proposal']['reason']='Changed observation';manifest.write_text(json.dumps(row)+'\n'+json.dumps(other)+'\n')
    assert not review_group(manifest,0,output)[0]['reviewed']
    with pytest.raises(ValueError,match='reload'):
        record_decision(manifest,output,{**request,'decision':'independent'})
    item=load_item(manifest,0);new={key:item[key] for key in request}
    monkeypatch.setattr('local_vector_batch.review_queue.render_page',lambda *a,**kw:b'wrong crop')
    with pytest.raises(ValueError,match='crop changed'):
        record_decision(manifest,output,{**new,'decision':'independent'})


def test_all_diff_generation_keeps_page_items_individually_reviewable(tmp_path, monkeypatch):
    from local_vector_batch.review_queue import generate_review_queue, review_group
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'pdf');source_sha=sha(pdf.read_bytes())
    package=tmp_path/'package';package.mkdir();(package/'evidence-package.json').write_text(json.dumps({'document':{'pdf_sha256':source_sha}}))
    a='(2)\nFirst text is 15. Next line is red.'; b='(2) First text is 10. Next line is blue.'
    (package/'pages.jsonl').write_text(json.dumps({'page':1,'text':a,'text_sha256':sha(a.encode()),'status':'review_required','method':'vision'})+'\n')
    jobs=tmp_path/'jobs.jsonl';jobs.write_text(json.dumps({'package':str(package),'source_pdf':str(pdf),'pages':[1]})+'\n')
    ocr=tmp_path/'ocr.jsonl';ocr.write_text(json.dumps({'source_pdf_sha256':source_sha,'page':1,'selected_page_text_sha256':sha(a.encode()),'vision':{'engine':'vision'},'independent':{'engine':'rapidocr','text':b,'text_sha256':sha(b.encode())}})+'\n')
    monkeypatch.setattr('local_vector_batch.review_queue._bound_independent_text',lambda *args,**kwargs:b)
    result=generate_review_queue(jobs,ocr,tmp_path/'review',all_differences=True,per_page=64)
    m=Path(result['output']);items=[json.loads(l) for l in m.read_text().splitlines()]
    assert {r['focus']['kind'] for r in items} >= {'number','text_change','formatting'}
    assert len(review_group(m,0)) == result['created'] and all(r['page_total_discrepancies']>=result['created'] for r in items)


def test_observation_attachment_requires_exact_focus_and_original_crop(tmp_path, monkeypatch):
    from local_vector_batch.review_queue import attach_observations
    manifest, _, _ = queue(tmp_path)
    row=json.loads(manifest.read_text());row['focus']={'kind':'number','boxes':[[.1,.1,.8,.3]],
        'selected_excerpt':'Clause 5','independent_excerpt':'Clause 6'}
    manifest.write_text(json.dumps(row)+'\n');item=load_item(manifest,0)
    keys=('source_pdf_sha256','page','selected_text_sha256','independent_text_sha256','prompt_sha256','focus_sha256')
    obs={**{key:item[key] for key in keys},'choice':'independent','reason':'The crop shows 6.',
         'focus_image_sha256':sha(b'crop'),'scope':'focused_difference_only','source_image_reviewed':True}
    observations=tmp_path/'observations.jsonl';observations.write_text(json.dumps(obs)+'\n')
    monkeypatch.setattr('local_vector_batch.review_queue.render_page',lambda *a,**kw:b'crop')
    output=tmp_path/'with-proposals.jsonl'
    result=attach_observations(manifest,observations,output)
    assert result['status']=='unverified_suggestions_only' and load_item(output,0)['proposal']['choice']=='independent'
    with pytest.raises(ValueError,match='new proposal'):
        attach_observations(manifest,observations,output)
    observations.write_text(json.dumps({**obs,'focus_image_sha256':'f'*64})+'\n')
    with pytest.raises(ValueError,match='crop does not match'):
        attach_observations(manifest,observations,tmp_path/'bad.jsonl')
    observations.write_text(json.dumps({**obs,'source_image_reviewed':False})+'\n')
    with pytest.raises(ValueError,match='unreviewed'):
        attach_observations(manifest,observations,tmp_path/'bad2.jsonl')


def test_filter_only_skips_exact_source_text_and_focus_not_whole_page(tmp_path):
    from local_vector_batch.review_queue import filter_reviewed_focus
    manifest, _, _ = queue(tmp_path)
    row=json.loads(manifest.read_text());row['focus']={'kind':'number','selected_excerpt':'Clause 5','independent_excerpt':'Clause 6','selected_span':[7,8],'independent_span':[7,8]}
    second={**row,'prompt':'Check the word','focus':{'kind':'text_change','selected_excerpt':'Clause 5','independent_excerpt':'Clause 6','selected_span':[0,6],'independent_span':[0,6]}}
    manifest.write_text(json.dumps(row)+'\n'+json.dumps(second)+'\n')
    item=load_item(manifest,0);keys=('index','source_pdf_sha256','page','selected_text_sha256','independent_text_sha256','prompt_sha256','focus_sha256','proposal_sha256')
    feedback=tmp_path/'feedback.jsonl';record_decision(manifest,feedback,{**{k:item[k] for k in keys},'decision':'selected'})
    result=filter_reviewed_focus(manifest,[feedback],tmp_path/'filtered.jsonl')
    assert result['already_reviewed_focus']==1 and result['kept']==1
    assert load_item(Path(result['output']),0)['focus']['kind']=='text_change'
    with pytest.raises(ValueError,match='new review manifest'):
        filter_reviewed_focus(manifest,[feedback],Path(result['output']))
