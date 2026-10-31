"""Synthetic context-contract controls; no source quality is established here."""
import copy
import pytest

from local_vector_batch.native_cell_row_context import (
    prove_row_context, validate_row_context, RowContextUnavailable,
)


def fixture():
    cell = {'id':'table:r3:c1','row':3,'column':1,'row_span':1,'column_span':1,
            'bbox':[110.,230.,340.,270.],'text':'DC R esistance (ohm/km)','table_geometry_sha256':'geometry'}
    anchor = {'id':'table:r3:c0','row':3,'column':0,'row_span':1,'column_span':1,
              'bbox':[70.,230.,110.,270.],'text':'iii.'}
    def group(text,left,right,y=590.):
        return {'text':text,'tx':left,'displaced_tx':right,'ty':y,'font_size':12.,'font_height':12.,'flip_sort':1}
    def run(text,left,right,index,y=590.):
        return {'text':text,'origin_pdf':[left,y],'advance_end_pdf_x':right,'font_size':12.,'font':'Arial',
                'font_interpretable':True,'text_rise':0.,'rotated':False,'flip_vertical':False,
                'operator_index':index,'operator':'Tj','piece_index':0,'marked_content':[{'MCID':index}]}
    capture={'contract_version':'pdf2data.pypdf-source-operator-page.v1','source_pdf_sha256':'PDF','page':42,
        'width':600.,'height':840.,'rotation':0,'reader':'synthetic_operator_fixture','reader_version':'synthetic',
        'reader_implementation_sha256':'synthetic','content_stream_sha256':'stream','clip_operator_indices':[4],
        'groups':[group('iii.',80.,100.),group('DC Resistance (ohm/km)',115.,260.),group('ii.',80.,100.,390.),group('DC Resistance (ohm/km)',115.,260.,390.)],
        'text_show_operators':[run('iii.',80.,100.,1),run('DC Resistance (ohm/km)',115.,260.,2),run('ii.',80.,100.,3,390.),run('DC Resistance (ohm/km)',115.,260.,4,390.)]}
    processed='DC Resistance (ohm/km)';selected='iii. DC Resistance (ohm/km)\nii. DC Resistance (ohm/km)';raw='iii. DC R esistance (ohm/km)\nii. DC Resistance (ohm/km)'
    return capture,cell,anchor,processed,selected,raw,'independent full-page layout'


def test_repeated_label_is_bound_to_actual_row_anchor():
    args=fixture();w=prove_row_context(*args)
    assert w['selected_page_context']['codepoint_span']==[5,27]
    assert w['independent_cell']['operator_indices']==[2]
    assert w['independent_row_anchor']['operator_indices']==[1]
    assert w['source_clip_operator_indices_preserved']==[4]
    assert w['clip_or_glyph_ownership_approved']is False
    assert w['source_quality_clearance']is False
    assert validate_row_context(w,args[1],*args[3:],'PDF',42)


def test_identical_words_in_wrong_row_do_not_resolve_this_cell():
    args=list(fixture());args[0]['groups'][1]['ty']=390.
    with pytest.raises(RowContextUnavailable):prove_row_context(*args)


def test_cross_column_group_is_never_clipped_to_make_a_context():
    args=list(fixture());args[0]['groups'][1]['displaced_tx']=400.
    with pytest.raises(RowContextUnavailable,match='crosses_source_cell'):prove_row_context(*args)


def test_missing_or_foreign_row_anchor_rejected():
    args=list(fixture());args[2]['row']=4
    with pytest.raises(RowContextUnavailable):prove_row_context(*args)
    args=list(fixture());args[2]['text']='iv.'
    with pytest.raises(RowContextUnavailable,match='literal_differs'):prove_row_context(*args)


def test_nonidentifier_anchor_cannot_be_used_as_body_column_waiver():
    args=list(fixture());args[2]['text']='company name'
    with pytest.raises(RowContextUnavailable,match='not_configured'):prove_row_context(*args)


def test_anchor_baseline_must_independently_match():
    args=list(fixture());args[0]['groups'][0]['ty']-=2.;args[0]['text_show_operators'][0]['origin_pdf'][1]-=2.
    with pytest.raises(RowContextUnavailable,match='different_operator_baselines'):prove_row_context(*args)


def test_duplicate_anchored_context_remains_ambiguous():
    args=list(fixture());args[4]+='\niii. DC Resistance (ohm/km)'
    with pytest.raises(RowContextUnavailable,match='missing_or_ambiguous'):prove_row_context(*args)


def test_unmapped_font_or_extra_foreign_glyphs_rejected():
    args=list(fixture());args[0]['text_show_operators'][1]['font_interpretable']=False
    with pytest.raises(RowContextUnavailable,match='script_font'):prove_row_context(*args)
    args=list(fixture());args[0]['text_show_operators'][1]['text']+='Z'
    with pytest.raises(RowContextUnavailable,match='order_or_coverage'):prove_row_context(*args)


@pytest.mark.parametrize('field,value',[('rotated',True),('text_rise',2.),('font_size',8.)])
def test_subscript_superscript_or_rotation_not_flattened(field,value):
    args=list(fixture());args[0]['text_show_operators'][1][field]=value
    with pytest.raises(RowContextUnavailable):prove_row_context(*args)


def test_hyphen_or_number_content_change_cannot_be_normalized():
    args=list(fixture());args[3]='DC Resistance (ohm-km)'
    with pytest.raises(RowContextUnavailable,match='literal_differs'):prove_row_context(*args)
    args=list(fixture());args[3]='DC Resistance (ohm/km)2'
    with pytest.raises(RowContextUnavailable,match='literal_differs'):prove_row_context(*args)


def test_non_ascii_script_requires_independent_separate_rule():
    args=list(fixture());args[0]['groups'][1]['text']='कि';args[0]['text_show_operators'][1]['text']='कि'
    with pytest.raises(RowContextUnavailable,match='separate_script'):prove_row_context(*args)


def test_multiline_interleaved_cell_not_silently_accepted():
    args=list(fixture());args[1]['text']+='\nnext line'
    with pytest.raises(RowContextUnavailable,match='initial_single_line'):prove_row_context(*args)


def test_tampered_binding_or_page_sha_rejected():
    args=fixture();w=prove_row_context(*args)
    bad=copy.deepcopy(w);bad['cell_bbox'][0]-=1
    assert not validate_row_context(bad,args[1],*args[3:],'PDF',42)
    assert not validate_row_context(w,args[1],args[3],args[4]+'changed',args[5],args[6],'PDF',42)


def test_raw_native_context_stays_required_for_existing_exact_page_edit_gate():
    args=list(fixture());args[5]='iii. DC R esistance (ohm/km)\niii. DC R esistance (ohm/km)'
    with pytest.raises(RowContextUnavailable,match='raw_native_cell_context'):prove_row_context(*args)
