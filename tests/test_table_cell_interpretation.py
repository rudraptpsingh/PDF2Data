"""Interpreted candidates are source-bound and never quality approvals."""
from copy import deepcopy
import pytest
from local_vector_batch.table_cell_interpretation import canonical, sha, validate_interpretation


def fixture():
    cells = [{'id': 't:r1:c1', 'row': 1, 'column': 1, 'bbox': [10, 10, 50, 20],
              'text': 'Hydro generating station including pumped storage hydro',
              'independent_text': 'Hydro generating station including pumped storage hydro'},
             {'id': 't:r2:c1', 'row': 2, 'column': 1, 'bbox': [10, 20, 50, 30],
              'text': 'generating stations', 'independent_text': 'generating stations'},
             {'id': 't:r3:c1', 'row': 3, 'column': 1, 'bbox': [10, 30, 50, 34],
              'text': '', 'independent_text': 'gg '},
             {'id': 't:r1:c2', 'row': 1, 'column': 2, 'bbox': [50, 10, 70, 30],
              'text': '40 years', 'independent_text': '40 years'}]
    fingerprint = {'source_pdf_sha256': 'a'*64, 'page': 7, 'page_text_sha256': 'b'*64}
    source = {**fingerprint, 'tables': [{'table_version_id': 't', 'table_geometry_sha256': 'c'*64,
                                      'cell_spans': cells}]}
    snapshot = {'fingerprint': fingerprint, 'snapshot': source, 'snapshot_sha256': sha(canonical(source))}
    q = {**fingerprint, 'source_fingerprint': fingerprint, 'table_version_id': 't',
         'table_geometry_sha256': 'c'*64, 'cell_id': 't:r3:c1', 'focus_key': 'question',
         'row': 3, 'column': 1, 'bbox': cells[2]['bbox'], 'selected_text': '', 'independent_text': 'gg ',
         'selected_cell_sha256': sha(''), 'independent_cell_sha256': sha('gg ')}
    feedback = [{**q, 'created_at': 'before', 'promotes_page': False, 'correction': 'empty. ',
                 'note': 'Hydro is wrapped across lines', 'decision': 'corrected'}]
    interpretation = {'question_id': 'question', 'source_cell_ids': [c['id'] for c in cells[:3]],
                      'action': 'logical_cell_continuation',
                      'literal_text': cells[0]['text']+'\n'+cells[1]['text'],
                      'review_origin': 'model_assisted_original_raster_with_recorded_user_clarification',
                      'user_clarification': {'quote': 'Cell contains text',
                                             'channel': 'trusted_current_thread_user_clarification_via_parent'},
                      'reviewer_identity_authenticated': False, 'promotes_page': False,
                      'associations': [{'source_cell_id': 't:r1:c2', 'source_literal': '40 years'}]}
    return q, snapshot, feedback, interpretation


def test_hydro_complete_continuation_retains_original_feedback_and_never_promotes():
    result = validate_interpretation(*fixture())
    assert result['interpretation']['literal_text'].endswith('generating stations')
    assert result['original_feedback']['correction'] == 'empty. '
    assert len(result['original_cells']) == 3
    for flag in ('source_text_modified', 'source_packages_modified', 'source_quality_clearance',
                 'table_semantics_approved', 'publication_eligible', 'legal_correctness_claimed'):
        assert result[flag] is False


def test_exact_retries_preserved_without_duplicate_application():
    q,s,f,i = fixture(); f.append({**f[0], 'created_at': 'later'})
    r = validate_interpretation(q,s,f,i)
    assert r['feedback_unique_count'] == 1 and r['feedback_record_count'] == 2


@pytest.mark.parametrize('change', ['feedback_source', 'snapshot_content', 'question_hash',
                                    'question_source', 'conflicting_retry', 'whole_page',
                                    'false_auth', 'empty_clarification', 'skip_row',
                                    'empty_hydro', 'wrong_association'])
def test_reject_stale_invented_or_unsafe_interpretations(change):
    q,s,f,i = deepcopy(fixture())
    if change == 'feedback_source': f[0]['source_pdf_sha256'] = 'd'*64
    elif change == 'snapshot_content': s['snapshot']['page'] = 8
    elif change == 'question_hash': q['selected_cell_sha256'] = 'd'*64
    elif change == 'question_source': q['source_pdf_sha256'] = 'd'*64
    elif change == 'conflicting_retry': f.append({**f[0], 'correction': 'different'})
    elif change == 'whole_page': i['promotes_page'] = True
    elif change == 'false_auth': i['reviewer_identity_authenticated'] = True
    elif change == 'empty_clarification': i['user_clarification']['quote'] = ''
    elif change == 'skip_row': i['source_cell_ids'].remove('t:r2:c1')
    elif change == 'empty_hydro': i['literal_text'] = 'empty.'
    elif change == 'wrong_association': i['associations'][0]['source_literal'] = '35 years'
    with pytest.raises(ValueError): validate_interpretation(q,s,f,i)


def test_full_literal_and_typography_are_exact_codepoint_spans():
    q,s,f,i = fixture()
    i.update(action='source_literal_and_typography', source_cell_ids=[q['cell_id']],
             complete_cell_visually_reviewed=True, complete_visual_lines=['Level as per', '7th CPC'],
             literal_text='Level as per\n7th CPC', associations=[])
    start = i['literal_text'].index('th')
    i['typography'] = [{'kind': 'superscript', 'start': start, 'end': start+2,
                       'literal': 'th', 'source_visual_basis': 'source crop raised glyphs'}]
    assert validate_interpretation(q,s,f,i)['literal_sha256'] == sha(i['literal_text'])
    i['typography'][0]['start'] += 1
    with pytest.raises(ValueError): validate_interpretation(q,s,f,i)


def test_short_ui_ordering_correction_is_not_automatically_used_as_full_cell():
    q,s,f,i = fixture(); i.update(action='source_literal_and_typography', source_cell_ids=[q['cell_id']],
       complete_cell_visually_reviewed=True, complete_visual_lines=['(4)', 'Level –II',
       'As per 7th CPC', 'pay matrix', '(PB-I, GP-', '₹ 1900/- pre-', 'revised scale)'], associations=[])
    i['literal_text'] = '\n'.join(i['complete_visual_lines'])
    assert 'revised scale)' in validate_interpretation(q,s,f,i)['interpretation']['literal_text']
    i['literal_text'] = '(4) Level -II'
    with pytest.raises(ValueError): validate_interpretation(q,s,f,i)
