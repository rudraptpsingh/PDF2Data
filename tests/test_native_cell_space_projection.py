import copy

from local_vector_batch.native_cell_space_projection import propose_processed_cell, project_page_text


def glyph(text, x, width, *, top=10, size=12):
    return {'text': text, 'x0': x, 'x1': x + width, 'top': top, 'bottom': top + 12,
            'fontname': 'ExampleNativeFont', 'size': size}


def source():
    # Content-stream leading SPACE is physically inside O. Every source object
    # is retained; the table reader's spatial sort yields the spurious FO DP.
    result = [glyph(' ', 20, 3), glyph('F', 10, 7), glyph('O', 17, 9), glyph('D', 26, 8), glyph('P', 34, 8)]
    x = 42
    for c in ' terminations capacity':
        result.append(glyph(c, x, 3)); x += 3
    return result


def prove(raw='FO DP terminations capacity', independent='FODP terminations capacity',
          glyphs=None, third='before FODP  terminations capacity after',
          selected='heading\nFODP terminations capacity\nfooter', box=None):
    return propose_processed_cell(raw, independent, source() if glyphs is None else glyphs,
                                  third, selected, cell_bbox=box or [0, 0, 200, 30],
                                  source_pdf_sha256='a' * 64, page=7, cell_id='fixture-cell',
                                  table_geometry_sha256='b' * 64)


def test_additive_projection_preserves_raw_and_exact_page_span():
    original = source(); before = copy.deepcopy(original)
    value = prove(glyphs=original)
    assert value['processed_text'] == 'FODP terminations capacity'
    assert value['raw_text'] == 'FO DP terminations capacity'
    assert original == before and not value['page_text_modified'] and not value['raw_matrix_modified']
    assert value['status'] == 'candidate_pending_source_ink_ownership'
    assert value['selected_page_context']['exact_quote'] == value['processed_text']
    assert len(value['source_space_witnesses']) == 1
    assert not value['source_quality_clearance'] and not value['answer_publication_eligible']


def test_existing_reader_restoration_does_not_authorize_projection_without_misplaced_object():
    assert prove(glyphs=source()[1:]) is None
    assert prove(raw='FODP terminations capacity') is None


def test_true_source_space_between_letters_is_never_erased():
    items = source(); items.insert(3, glyph(' ', 26, 1))
    assert prove(glyphs=items) is None


def test_real_gap_and_cross_line_stay_held():
    items = source()
    for g in items[3:]:
        g['x0'] += 2; g['x1'] += 2
    assert prove(glyphs=items) is None
    items = source(); items[3]['top'] = 24; items[3]['bottom'] = 36
    assert prove(glyphs=items) is None


def test_digits_punctuation_operators_and_newlines_not_normalized():
    for raw, independent in [('1 6', '16'), ('+ -', '+-'), ('FO\nDP terminations capacity', 'FODP terminations capacity')]:
        assert prove(raw=raw, independent=independent) is None


def test_source_letter_loss_change_and_order_never_repaired():
    for independent in ['FOP terminations capacity', 'F0DP terminations capacity', 'FDOP terminations capacity']:
        assert prove(independent=independent) is None
    items = source(); items[3]['text'] = 'X'
    assert prove(glyphs=items) is None


def test_full_selected_and_third_contexts_are_unique_and_unsplit():
    assert prove(third='FO DP terminations capacity') is None
    assert prove(third='FODP terminations capacity\nFODP terminations capacity') is None
    assert prove(selected='FODP terminations capacity\nFODP terminations capacity') is None


def test_glyph_crossing_foreign_font_and_multiple_spaces_are_held():
    assert prove(box=[18, 0, 200, 30]) is None
    items = source(); items[0]['fontname'] = 'ForeignFont'
    assert prove(glyphs=items) is None
    items = source(); items.insert(0, copy.deepcopy(items[0]))
    assert prove(glyphs=items) is None


def test_displaced_space_must_be_leading_and_inside_letter_run():
    items = source(); ghost = items.pop(0); items.append(ghost)
    assert prove(glyphs=items) is None


def test_page_projection_only_deletes_exact_proved_spaces_and_preserves_offsets():
    raw_page = 'heading\nFO DP terminations capacity\nfooter'
    selected = 'heading\nFODP terminations capacity\nfooter'
    value = propose_processed_cell('FO DP terminations capacity', 'FODP terminations capacity',
        source(), 'FODP terminations capacity', selected, cell_bbox=[0, 0, 200, 30],
        source_pdf_sha256='a'*64, page=7, cell_id='fixture', table_geometry_sha256='b'*64,
        raw_native_page=raw_page)
    receipt = project_page_text(raw_page, [value], selected)
    assert receipt['whole_selected_page_exactly_reproduced']
    assert receipt['exact_source_SPACE_deletions'][0]['source_codepoint_span'] == [10, 11]
    assert not receipt['original_selected_page_modified']
    import pytest
    with pytest.raises(ValueError, match='complete selected page'):
        project_page_text(raw_page, [value], selected.replace('footer', 'changed'))
    with pytest.raises(ValueError, match='overlap'):
        project_page_text(raw_page, [value, value], selected)
    with pytest.raises(ValueError, match='identity'):
        project_page_text(raw_page.replace('heading', 'different'), [value], selected)
    items = source(); items[0]['x0'] = 80; items[0]['x1'] = 83
    assert prove(glyphs=items) is None
