import copy
from types import SimpleNamespace

import numpy as np

from local_vector_batch.native_cell_space_projection import _projection_context
from local_vector_batch.native_marked_clip_witness import marked_clip_contexts


def fixture():
    glyph = {'text': 'F', 'fontname': 'ArialMT', 'size': 12., 'mcid': 2,
             'x0': 10., 'x1': 17., 'top': 10., 'bottom': 22.}
    native = SimpleNamespace(chars=[glyph], images=[], curves=[], lines=[], rects=[], page_number=7)
    operations = [([], b'q'), ([0, 0, 100, 100], b're'), ([], b'W'), ([], b'n'),
                  (['/P', {'/MCID': 2}], b'BDC'), (['F'], b'Tj'), ([], b'EMC'), ([], b'Q')]
    prepared = {'source_sha': 'a'*64, 'annotation_guard': {'has_annotations': False},
                'unsupported': [], 'paint': {'drawings': []}, 'policy': {},
                'visibility': [{'text': 'F', 'bbox': [10, 10, 17, 22],
                    'visibility_state': 'retained_visible_or_unproven', 'color': [0., 0., 0.]}],
                'characters': [{'native_matches': [0], 'pdfium_index': 5, 'tight_bbox': [10, 10, 17, 22]}],
                'ink': np.ones((60, 100), dtype=bool), 'scale': 1., 'fills': [],
                'source_marked_clip_contexts': marked_clip_contexts(operations, 100),
                'reader_versions': {'fixture_only': 'no_actual_reader_execution'},
                'raster_sha': 'b'*64, 'paint_snapshot_sha256': 'c'*64}
    return prepared, native, {'bbox': [0, 0, 80, 40]}


def test_context_keeps_original_cell_and_does_not_claim_whole_page_raster():
    prepared, native, cell = fixture()
    value = _projection_context(prepared, native, cell, 'a'*64)
    assert value['status'] == 'verified_source_cell_projection_context'
    assert value['original_cell_bbox'] == [0, 0, 80, 40]
    assert not value['pixel_mask_applied'] and not value['source_quality_clearance']
    assert value['complete_page_raster_accounting'] == 'required_separately_by_registered_region_decoder'


def test_source_glyph_crossing_and_ambiguous_independent_match_hold():
    p, n, c = fixture(); p['characters'][0]['tight_bbox'][2] = 81
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['characters'].append(copy.deepcopy(p['characters'][0]))
    assert _projection_context(p, n, c, 'a'*64) is None


def test_foreign_fill_or_stroke_through_glyph_is_not_grid_or_space():
    p, n, c = fixture(); p['fills'] = [{'fill': [0., 0., 0.], 'effective_bbox': [12, 12, 15, 15]}]
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); n.lines = [{'x0': 9, 'top': 14, 'x1': 18, 'bottom': 14, 'linewidth': 1}]
    assert _projection_context(p, n, c, 'a'*64) is None


def test_missing_ink_occlusion_white_text_and_unknown_paint_hold():
    p, n, c = fixture(); p['ink'][:] = False
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['visibility'][0]['visibility_state'] = 'fully_occluded'
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['visibility'][0]['color'] = [1., 1., 1.]
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['unsupported'] = [{'reason': 'unknown_paint'}]
    assert _projection_context(p, n, c, 'a'*64) is None


def test_unresolved_annotation_image_curve_script_and_clip_are_not_ignored():
    for attribute in ('images', 'curves'):
        p, n, c = fixture(); setattr(n, attribute, [{'x0': 30, 'top': 10, 'x1': 50, 'bottom': 20}])
        assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['annotation_guard']['has_annotations'] = True
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); n.chars[0]['fontname'] = 'Devnagari-ChanakyaNormalA'
    assert _projection_context(p, n, c, 'a'*64) is None
    p, n, c = fixture(); p['source_marked_clip_contexts'][2][0]['clips'][0]['bbox'] = [0, 0, 11, 30]
    assert _projection_context(p, n, c, 'a'*64) is None


def test_changed_source_and_unmarked_glyph_cannot_borrow_context():
    p, n, c = fixture(); assert _projection_context(p, n, c, 'd'*64) is None
    p, n, c = fixture(); n.chars[0]['mcid'] = None
    assert _projection_context(p, n, c, 'a'*64) is None
