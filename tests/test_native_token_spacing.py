import copy

from local_vector_batch.native_token_spacing import reconstitute_line,LEARNED_REPAIR_REGISTRY


def glyphs(text):
    out=[];x=10
    for char in text:
        width=3 if char.isspace() else 6
        out.append({'text':char,'x0':x,'x1':x+width,'top':10,'bottom':22,'size':12})
        x+=width+.05
    return out


def run(native,layout,*,raw=None,source=None,**kwargs):
    return reconstitute_line(native,source or glyphs(native),native if raw is None else raw,layout,**kwargs)


def test_split_digits_and_operator_are_source_bound_not_literal_specific():
    for token,split,kind in [('48th','4     8th','split_numeric_token'),('17.04.2028','17.04.   2028','split_numeric_token'),('and','an   d','split_operator_word'),('therefore','there   fore','split_native_word')]:
        native='Use '+token+' now.';layout='Use '+split+' now.'
        result=run(native,layout)
        assert len(result['repairs'])==1
        proof=result['repairs'][0]
        assert proof['source_token']==token and proof['kind']==kind
        assert proof['boundaries'][0]['source_gap_points']<.1
        assert proof['selected_text_modified']is False


def test_true_wide_word_gap_is_not_joined():
    assert run('Use an d now.','Use an d now.')['repairs']==[]
    source=glyphs('Use and now.')
    # An allegedly single token with a genuine large physical gap is held.
    at='Use and now.'.index('d')
    for glyph in source[at:]:glyph['x0']+=8;glyph['x1']+=8
    result=run('Use and now.','Use an d now.',source=source)
    assert result['repairs']==[]and 'real_or_ambiguous_source_token_gap'in result['holds']


def test_explicit_source_space_inside_claimed_token_blocks_reconstitution():
    result=run('Use and now.','Use an d now.',source=glyphs('Use an d now.'))
    assert result['repairs']==[]


def test_actual_layout_newline_or_table_cell_boundary_is_never_joined():
    assert run('Use and now.','Use an\nd now.')['repairs']==[]
    assert run('Use and now.','Use an d now.',table_boxes=[[1,1,300,40]])['repairs']==[]


def test_pdfium_raised_suffix_separator_requires_same_physical_source_word():
    result=run('Use 48th now.','Use 4    8th now.',raw='Use 48\r\nth now.')
    assert len(result['repairs'])==1
    assert result['repairs'][0]['pdfium_source_token_spacing_proofs']
    source=glyphs('Use 48th now.')
    for glyph in source[6:8]:glyph['top']+=30;glyph['bottom']+=30
    assert run('Use 48th now.','Use 4    8th now.',raw='Use 48\r\nth now.',source=source)['repairs']==[]


def test_conflicting_number_negation_or_independent_word_never_repaired():
    for native,layout in [('Pay 25.06.2025 now.','Pay 25.06.2026 now.'),('shall not pay now.','shall pay now.'),('Use and now.','Use or now.')]:
        assert run(native,layout)['repairs']==[]
    assert run('Use and now.','Use an d now.',raw='Use or now.')['repairs']==[]


def test_repeated_context_does_not_pick_a_convenient_occurrence():
    result=run('Use and now.','Use and now.\nUse an d now.')
    assert result['repairs']==[]and 'independent_line_context_missing_or_ambiguous'in result['holds']


def test_registry_has_explicit_source_only_mutation_contracts():
    assert {'split_numeric_token','split_operator_word','source_bound_pdfium_control_hyphen_mapping'}<=set(LEARNED_REPAIR_REGISTRY)
    assert all('independent'in rule['changes']for rule in LEARNED_REPAIR_REGISTRY.values())
