"""Cross-page table continuation: case, signals and the negatives that must stay apart."""
from copy import deepcopy

import pytest

from test_document_continuations import fixture
from local_vector_batch.document_blocks import span
from local_vector_batch.document_continuations import build_continuations
from local_vector_batch.table_contexts import iter_table_contexts

HEAD = [['Entity', 'Amount'], ['Plant A', '10']]
FOOT, TOP = [50, 700, 350, 980], [50, 60, 350, 200]


def document(fragments, pages=None):
    """fragments: (page, rows, box). One table block per fragment, on 600x1000 pages."""
    pages = pages or sorted({f[0] for f in fragments})
    data = fixture(['page %d text' % n for n in pages], ['paragraph']*len(pages))
    for page, number in zip(data['pages'], pages):
        page.update(id=f'd:p{number}', page=number)
    data['layout'] = [dict(page=n, width=600, height=1000, glyphs=[]) for n in pages]
    data['document_blocks']['blocks'] = []
    for i, (number, rows, box) in enumerate(fragments, 1):
        width = (box[2]-box[0])/max(map(len, rows))
        cells = [dict(id=f't{i}:r{r}:c{c}', row=r, column=c, row_span=1, column_span=1, text=value,
                      bbox=[box[0]+c*width, box[1], box[0]+(c+1)*width, box[3]], role_candidate='unresolved')
                 for r, row in enumerate(rows) for c, value in enumerate(row)]
        data['tables'].append(dict(id=f't{i}', table_version_id=f'tv{i}', page=number, bbox=list(box),
                                   cells=deepcopy(rows), cell_spans=cells))
        add_block(data, number, 'table', list(box), id=f'tb{i}', table_id=f't{i}')
    return data


def add_block(data, number, kind, box, text=None, **extra):
    page = next(p for p in data['pages'] if p['page'] == number)
    item = dict(id=extra.pop('id', f'x{len(data["document_blocks"]["blocks"])}'), kind=kind,
                order=len(data['document_blocks']['blocks']), spans=[span(page, 0, len(page['text']))],
                parent_unit_ids=['u'], bbox=box, semantic_state='candidate', **extra)
    if text is not None:
        item['spans'] = [{**item['spans'][0], 'exact_quote': text}]
    data['document_blocks']['blocks'].append(item)
    data['document_blocks']['blocks'].sort(key=lambda b: (b['spans'][0]['page'], b['bbox'][1]))


def only(data):
    result = build_continuations(data)
    assert len(result['links']) == 1, result['unresolved_boundaries']
    return result['links'][0]


def reason(data):
    result = build_continuations(data)
    assert not result['links']
    return [u['reason'] for u in result['unresolved_boundaries']]


def test_header_repeated_on_next_page_names_origin_and_first_data_row():
    data = document([(1, HEAD, FOOT), (2, [['Entity', 'Amount'], ['Plant B', '20']], TOP)])
    original = deepcopy(data)
    link = only(data)
    assert link['case'] == 'header_repeated' and link['repeated_header_candidate']
    assert link['continues_table_id'] == 't1' and link['origin_table_id'] == 't1' and link['to_id'] == 't2'
    assert link['header_similarity'] == 1.0 and link['first_data_row'] == 1 and link['row_split'] is None
    assert link['signals'] == ['same_column_count', 'matching_column_boundaries', 'repeated_header',
                               'page_foot_to_page_head', 'no_intervening_body_content']
    assert data == original


def test_no_header_on_next_page_joins_on_page_foot_to_page_head():
    link = only(document([(1, HEAD, FOOT), (2, [['Plant B', '20'], ['Plant C', '30']], TOP)]))
    assert link['case'] == 'no_header' and link['first_data_row'] == 0 and link['header_similarity'] is None
    assert 'page_foot_to_page_head' in link['signals'] and 'repeated_header' not in link['signals']
    assert link['header_context']['raw_cells'] == ['Entity', 'Amount']


def test_row_split_points_at_both_fragments_and_moves_no_cell():
    data = document([(1, HEAD+[['Plant B', None]], FOOT), (2, [[None, '20'], ['Plant C', '30']], TOP)])
    original = deepcopy(data)
    link = only(data)
    assert link['case'] == 'row_split'
    assert link['row_split'] == dict(
        from_table_id='t1', from_row=2, to_table_id='t2', to_row=0,
        basis=['earlier_row_has_label_and_blank_cells', 'later_row_has_blank_label',
               'no_column_filled_in_both_fragments'],
        association='row_fragments_referenced_not_merged')
    assert data == original
    assert data['tables'][0]['cells'][2] == ['Plant B', None] and data['tables'][1]['cells'][0] == [None, '20']


def test_row_split_after_a_repeated_header_uses_the_row_below_it():
    link = only(document([(1, HEAD+[['Plant B', '']], FOOT),
                          (2, [['Entity', 'Amount'], ['', '20']], TOP)]))
    assert link['case'] == 'row_split' and link['repeated_header_candidate']
    assert link['row_split']['to_row'] == 1 and link['first_data_row'] == 1


@pytest.mark.parametrize('last,first', [
    (['Plant B', '15'], [None, '20']),       # earlier row is complete: a sparse row, not a split
    (['Plant B', None], ['Plant C', '20']),  # later row has its own label
    (['Plant B', None], [None, None]),       # nothing to complete it with
])
def test_incomplete_evidence_is_not_called_a_row_split(last, first):
    link = only(document([(1, HEAD+[last], FOOT), (2, [first, ['Plant D', '40']], TOP)]))
    assert link['case'] == 'no_header' and link['row_split'] is None


def test_continuation_marker_joins_fragments_away_from_the_page_edge():
    fragments = [(1, HEAD, [50, 300, 350, 500]), (2, [['Plant B', '20']], [50, 400, 350, 600])]
    assert reason(document(fragments)) == ['table_not_at_adjacent_page_boundary']
    data = document(fragments)
    add_block(data, 2, 'paragraph', [50, 370, 350, 390], 'Table 4 (Contd.)')
    link = only(data)
    assert link['case'] == 'no_header' and 'continuation_marker' in link['signals']
    assert 'page_foot_to_page_head' not in link['signals'] and 'boundary_geometry' not in link['basis']
    assert link['continuation_markers'] == [dict(location='source_block', block_id=link['continuation_markers'][0]['block_id'],
                                                 page=2, text='Table 4 (Contd.)')]


def test_similar_repeated_header_with_marker_cell_is_recognised():
    link = only(document([(1, [['S. No.', 'Name of Bidder'], ['1', 'A Ltd']], [50, 300, 350, 600]),
                          (2, [['S.No', 'Name of bidder (contd.)'], ['2', 'B Ltd']], TOP)]))
    assert link['case'] == 'header_repeated' and link['header_similarity'] >= .9
    assert {'repeated_header', 'continuation_marker'} <= set(link['signals'])
    assert link['continuation_markers'][0]['location'] == 'first_row_cell'


def test_two_different_tables_with_the_same_column_count_stay_apart():
    assert reason(document([(1, HEAD, FOOT), (2, [['Licensee', 'Fee'], ['X Ltd', '5']], TOP)])) \
        == ['new_or_conflicting_header']
    # one changed column is a different table even when the other header cell agrees
    assert reason(document([(1, [['Entity', 'Amount (Rs.)'], ['A', '1']], FOOT),
                            (2, [['Entity', 'Capacity (MW)'], ['B', '2']], TOP)])) == ['new_or_conflicting_header']
    # same count, no header, neither at the page edge
    assert reason(document([(1, HEAD, [50, 300, 350, 500]), (2, [['7', '20']], [50, 400, 350, 600])])) \
        == ['table_not_at_adjacent_page_boundary']


@pytest.mark.parametrize('rows', [[['Plant B', '20']], [['Entity', 'Amount'], ['Plant B', '20']]])
def test_unrelated_table_after_body_text_is_not_joined(rows):
    data = document([(1, HEAD, FOOT), (2, rows, [50, 150, 350, 300])])
    add_block(data, 2, 'paragraph', [50, 40, 350, 120], 'In continuation of the order dated 1 April, the fee is revised.')
    assert reason(data) == ['intervening_body_content_or_new_section']
    data = document([(1, HEAD, [50, 500, 350, 760]), (2, rows, TOP)])
    add_block(data, 1, 'paragraph', [50, 800, 350, 900], 'The Commission directs as follows.')
    assert reason(data) == ['intervening_body_content_or_new_section']


@pytest.mark.parametrize('pages', [[1, 2, 3], [1, 3]])
def test_page_gap_of_two_is_never_joined(pages):
    data = document([(1, HEAD, FOOT), (3, [['Entity', 'Amount'], ['Plant B', '20']], TOP)], pages)
    add_block(data, 3, 'paragraph', [50, 20, 350, 40], '(continued)')
    result = build_continuations(data)
    assert not [l for l in result['links'] if l['kind'] == 'table_continuation_candidate']


def test_column_count_must_match_even_with_marker_and_page_edge():
    data = document([(1, HEAD, FOOT), (2, [['Plant B', '20', 'MW']], TOP)])
    add_block(data, 2, 'paragraph', [50, 20, 350, 40], '(continued)')
    assert reason(data) == ['column_count_differs']


def test_new_fields_are_additive_and_reach_the_table_context_record():
    data = document([(1, HEAD+[['Plant B', None]], FOOT), (2, [[None, '20']], TOP)])
    tables = deepcopy(data['tables'])
    data['document_model'] = {'continuations': build_continuations(data)}
    link = data['document_model']['continuations']['links'][0]
    added = {'continues_table_id', 'origin_table_id', 'case', 'signals', 'header_similarity',
             'first_data_row', 'row_split', 'continuation_markers'}
    assert added <= set(link)
    assert set(link)-added == {'kind', 'from_id', 'to_id', 'from_page', 'to_page', 'basis', 'status',
                               'source_pdf_sha256', 'page_text_sha256', 'publication_eligible', 'id',
                               'group_id', 'header_context', 'repeated_header_candidate', 'from_bbox', 'to_bbox'}
    first, second = iter_table_contexts(data)
    assert first['continuation'] is None and first['continuation_candidate_id'] is None
    assert second['continuation_candidate_id'] == link['id']
    assert second['continuation'] == {k: link[k] for k in (
        'continues_table_id', 'origin_table_id', 'group_id', 'case', 'signals', 'header_similarity',
        'first_data_row', 'row_split')}
    assert [c['raw_text'] for c in second['cells']] == [None, '20']
    assert [(c['row'], c['column']) for c in second['cells']] == [(0, 0), (0, 1)]
    assert data['tables'] == tables


def test_three_page_chain_keeps_one_origin():
    links = build_continuations(document([(1, HEAD, FOOT), (2, [['Plant B', '20']], [50, 60, 350, 980]),
                                          (3, [['Entity', 'Amount'], ['Plant C', '30']], TOP)]))['links']
    assert [l['case'] for l in links] == ['no_header', 'header_repeated']
    assert [l['continues_table_id'] for l in links] == ['t1', 't2']
    assert {l['origin_table_id'] for l in links} == {'t1'} and len({l['group_id'] for l in links}) == 1
