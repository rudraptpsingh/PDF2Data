import pytest
from local_vector_batch.native_marked_clip_witness import marked_clip_contexts, prove_marked_glyph_clips


def ops(rect=(0, 0, 100, 100), mcid=2):
    return [([], b'q'), (list(rect), b're'), ([], b'W*'), ([], b'n'),
            (['/P', {'/MCID': mcid}], b'BDC'), (['literal'], b'Tj'),
            ([], b'EMC'), ([], b'Q')]


def glyph(bounds=(10, 10, 20, 20), mcid=2):
    return {'source_glyph_index': 3, 'mcid': mcid, 'bbox': list(bounds)}


def test_exact_marked_scope_keeps_operator_and_clip_identity():
    proof = prove_marked_glyph_clips(marked_clip_contexts(ops(), 100), [glyph()])
    assert proof and proof['glyphs'][0]['text_operator_indexes'] == [5]
    assert proof['glyphs'][0]['active_clips'][0]['bbox'] == [0, 0, 100, 100]
    assert not proof['page_quality_cleared']


def test_other_mcid_clip_is_not_falsely_applied_to_this_text():
    source = ops() + ops((80, 0, 20, 100), 9)
    assert prove_marked_glyph_clips(marked_clip_contexts(source, 100), [glyph()])
    assert prove_marked_glyph_clips(marked_clip_contexts(source, 100), [glyph(mcid=9)]) is None


def test_reused_mcid_incompatible_context_is_ambiguous_and_holds():
    assert prove_marked_glyph_clips(marked_clip_contexts(ops() + ops((80, 0, 20, 100)), 100), [glyph()]) is None


def test_partial_clip_and_missing_marked_scope_stay_held():
    assert prove_marked_glyph_clips(marked_clip_contexts(ops(), 100), [glyph((95, 10, 101, 20))]) is None
    assert prove_marked_glyph_clips(marked_clip_contexts(ops(), 100), [glyph(mcid=None)]) is None


def test_unknown_path_and_nonidentity_transform_cannot_be_assumed_safe():
    source = ops(); source[1] = ([0, 0], b'm')
    assert prove_marked_glyph_clips(marked_clip_contexts(source, 100), [glyph()]) is None
    source = ops(); source.insert(1, ([2, 0, 0, 2, 0, 0], b'cm'))
    assert prove_marked_glyph_clips(marked_clip_contexts(source, 100), [glyph()]) is None


def test_forms_optional_content_and_unbalanced_states_fail_closed():
    with pytest.raises(ValueError, match='XObjects'):
        marked_clip_contexts([(['/Form'], b'Do')], 100)
    for source in ([([], b'Q')], ops()[:-1], ops()[:-2] + [([], b'Q')]):
        with pytest.raises(ValueError):
            marked_clip_contexts(source, 100)
    source = ops(); source[4] = (['/OC', {'/MCID': 2}], b'BDC')
    assert prove_marked_glyph_clips(marked_clip_contexts(source, 100), [glyph()]) is None


def test_nonfinite_degenerate_glyphs_never_validate_even_without_clip():
    contexts = {2: [{'operator_index': 1, 'clips': [], 'supported': True}]}
    for bounds in ((0, 0, float('nan'), 1), (0, 0, 0, 1), (1, 0, 0, 1)):
        assert prove_marked_glyph_clips(contexts, [glyph(bounds)]) is None
