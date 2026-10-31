"""Pure noncontiguous-context controls; fresh source/ink proof stays separate."""
import copy
import pytest
from local_vector_batch.native_cell_multiline_context import prove_multiline_context,mapped_space_deletions
from local_vector_batch.native_cell_row_context import RowContextUnavailable
from local_vector_batch.native_cell_space_projection import project_page_text


def fixture():
    def cell(identifier,row,column,bounds,text):
        return {'id':identifier,'row':row,'column':column,'row_span':1,'column_span':1,'bbox':bounds,'text':text,'readers_agree':True}
    target=cell('t:r2:c1',2,1,[110.,100.,300.,180.],'De scription\n(a) Name');target['table_geometry_sha256']='geometry'
    anchor=cell('t:r2:c0',2,0,[70.,100.,110.,180.],'3.')
    after=cell('t:r3:c0',3,0,[70.,180.,110.,220.],'4.')
    colon=cell('t:r2:c2',2,2,[300.,100.,330.,180.],':')
    def run(text,x,y,end,index,size=12.):
        return {'text':text,'origin_pdf':[x,500.-y],'advance_end_pdf_x':end,'operator_index':index,'piece_index':0,
            'font_size':size,'font':'Arial','font_interpretable':True,'rotated':False,'flip_vertical':False,'text_rise':0.,
            'operator':'Tj','marked_content':[{'MCID':index}]}
    capture={'contract_version':'pdf2data.pypdf-source-operator-page.v1','source_pdf_sha256':'PDF','page':113,'rotation':0,
        'height':500.,'width':600.,'reader':'synthetic_fixture','reader_version':'synthetic','reader_implementation_sha256':'synthetic',
        'content_stream_sha256':'stream','clip_operator_indices':[9],
        'text_show_operators':[run('3.',80.,120.,90.,1),run(' Description',120.,120.,250.,2),
            run('(a) Name',120.,150.,220.,3),run(':',310.,120.,315.,4),run('4.',80.,200.,90.,5)]}
    processed='Description\n(a) Name';selected='3. Description:\n(a) Name comment\n4. Next';raw='3. De scription:\n(a) Name comment\n4. Next'
    return capture,target,anchor,after,[colon],processed,selected,raw,'independent global layout'


def test_interleaved_foreign_colon_stays_outside_exact_cell_spans():
    args=fixture();w=prove_multiline_context(*args)
    assert [s['exact_quote']for s in w['selected_page_context']['line_spans']]==['Description','(a) Name']
    assert w['selected_page_context']['no_contiguous_whole_cell_page_span_claim']is True
    assert w['independent_foreign_delimiters'][0]['cell_id']=='t:r2:c2'
    assert w['source_quality_clearance']is False


def test_only_proved_space_deletion_reproduces_entire_page():
    args=fixture();w=prove_multiline_context(*args)
    p={'raw_native_page_sha256':w['raw_native_page_sha256'],'source_row_context':w,'raw_text':args[1]['text'],
       'cell_id':args[1]['id'],'processed_cell_version_id':'synthetic',
       'source_space_witnesses':[{'raw_separator_span':[2,3],'nonwhitespace_boundary':2}]}
    edits=mapped_space_deletions(p,args[7]);assert args[7][slice(*edits[0]['source_codepoint_span'])]==' '
    result=project_page_text(args[7],[p],args[6]);assert result['whole_selected_page_exactly_reproduced']is True
    assert ':'in args[6]and'comment'in args[6]


def test_missing_next_row_anchor_rejects_source_context():
    args=list(fixture());args[3]['text']='5.'
    with pytest.raises(RowContextUnavailable):prove_multiline_context(*args)


def test_wrong_physical_row_or_grid_is_not_a_context():
    args=list(fixture());args[3]['id']='foreign:r3:c0'
    with pytest.raises(RowContextUnavailable,match='same_grid'):prove_multiline_context(*args)


def test_primary_duplicate_row_boundaries_remain_ambiguous():
    args=list(fixture());args[6]+='\n3. Description:\n(a) Name comment\n4. Next'
    with pytest.raises(RowContextUnavailable,match='boundary_missing_or_ambiguous'):prove_multiline_context(*args)


def test_foreign_whole_operator_piece_is_not_cropped_into_cell():
    args=list(fixture());args[0]['text_show_operators'][1]['origin_pdf'][0]=80.
    with pytest.raises(RowContextUnavailable):prove_multiline_context(*args)
    args=list(fixture());args[0]['text_show_operators'][1]['text']=' Description FOREIGN'
    with pytest.raises(RowContextUnavailable,match='literal_or_order'):prove_multiline_context(*args)


@pytest.mark.parametrize('changed',['Descrip-tion','Description2','के'])
def test_hyphen_numbers_and_script_cannot_be_compact_normalized(changed):
    args=list(fixture());args[0]['text_show_operators'][1]['text']=changed
    with pytest.raises(RowContextUnavailable):prove_multiline_context(*args)


def test_superscript_fontsize_or_text_rise_stays_held():
    args=list(fixture());args[0]['text_show_operators'][2]['font_size']=8.
    with pytest.raises(RowContextUnavailable,match='fontsize'):prove_multiline_context(*args)
    args=list(fixture());args[0]['text_show_operators'][2]['text_rise']=2.
    with pytest.raises(RowContextUnavailable,match='text_rise'):prove_multiline_context(*args)


def test_no_proven_foreign_delimiter_means_no_adjacent_colon_waiver():
    args=list(fixture());args[4]=[]
    with pytest.raises(RowContextUnavailable,match='boundary_missing_or_ambiguous'):prove_multiline_context(*args)


def test_document_heading_number_is_disambiguated_by_complete_row_lines():
    args=list(fixture());args[6]='4. Document heading\n'+args[6];args[7]='4. Document heading\n'+args[7]
    assert prove_multiline_context(*args)['anchor_literal']=='3.'
    args=list(fixture());args[6]='3. Document heading\n'+args[6];args[7]='3. Document heading\n'+args[7]
    assert prove_multiline_context(*args)['anchor_literal']=='3.'


def test_blank_first_column_subrow_is_preserved_before_next_numeric_anchor():
    args=list(fixture());args[3]['row']=4;args[3]['id']='t:r4:c0'
    blank={'id':'t:r3:c0','row':3,'column':0,'row_span':1,'column_span':1,'bbox':[70.,180.,110.,190.],'text':''}
    args[4].append(blank)
    proof=prove_multiline_context(*args)
    assert proof['intervening_empty_anchor_cells_preserved']==[blank]
    frozen=copy.deepcopy(proof)
    blank['independent_text']=''
    blank['bbox'][0]=69.
    assert proof==frozen
    blank['text']='Different section'
    with pytest.raises(RowContextUnavailable,match='intervening_row_anchor_not_exactly_empty'):prove_multiline_context(*args)


def test_absent_intervening_anchor_cell_cannot_be_treated_as_blank():
    args=list(fixture());args[3]['row']=4;args[3]['id']='t:r4:c0'
    with pytest.raises(RowContextUnavailable,match='intervening_row_anchor_not_exactly_empty'):prove_multiline_context(*args)


def test_page_span_mutation_and_newline_edit_rejected():
    args=fixture();w=prove_multiline_context(*args)
    p={'raw_native_page_sha256':w['raw_native_page_sha256'],'source_row_context':w,'raw_text':args[1]['text'],
       'cell_id':args[1]['id'],'processed_cell_version_id':'synthetic',
       'source_space_witnesses':[{'raw_separator_span':[12,13],'nonwhitespace_boundary':11}]}
    with pytest.raises(ValueError):mapped_space_deletions(p,args[7])
    p['source_space_witnesses']=[{'raw_separator_span':[2,3],'nonwhitespace_boundary':2}]
    with pytest.raises(ValueError):mapped_space_deletions(p,args[7]+'changed')


def test_live_next_anchor_is_read_even_before_preview_visits_it(monkeypatch):
    from local_vector_batch.native_cell_multiline_context import capture_source_multiline_context
    from local_vector_batch.native_tight_cell_ownership import _PAGE_SOURCE_CELLS
    import local_vector_batch.native_table_cell_spacing as spacing
    args=list(fixture());capture,cell,anchor,after,foreign,processed,selected,raw,third=args
    after.pop('readers_agree');native=object();reads=[]
    context={'native_page':native,'source_sha256':'PDF','cells':[cell,anchor,after,*foreign],
        'independent_operator_capture':capture,'pdfium_page':object(),'text_page':object()}
    def read_box(*a):
        reads.append(a[-1]);return ('3.'if len(reads)==1 else'4.'),{'synthetic_rectangle':a[-1]}
    monkeypatch.setattr(spacing,'read_source_box',read_box);token=_PAGE_SOURCE_CELLS.set(context)
    try:w=capture_source_multiline_context(native,cell,processed,selected,raw,third,'PDF')
    finally:_PAGE_SOURCE_CELLS.reset(token)
    assert len(reads)==2
    assert len(w['independent_anchor_source_rectangle_readings'])==2


def test_only_proven_intercell_delimiter_spaces_can_be_joined():
    args=list(fixture());args[4].append({'id':'t:r2:c3','row':2,'column':3,'row_span':1,'column_span':1,
        'bbox':[330.,100.,500.,180.],'text':'(The note)'})
    args[0]['text_show_operators'].append({'text':'(The note)','origin_pdf':[340.,380.],'advance_end_pdf_x':430.,
        'operator_index':6,'piece_index':0,'font_size':12.,'font':'Arial','font_interpretable':True,
        'rotated':False,'flip_vertical':False,'text_rise':0.,'operator':'Tj','marked_content':[]})
    args[6]='3. Description:(The note)\n(a) Name comment\n4. Next'
    args[7]='3. De scription : (The note)\n(a) Name comment\n4. Next'
    w=prove_multiline_context(*args);assert len(w['source_column_boundary_space_joins'])==2
    p={'raw_native_page_sha256':w['raw_native_page_sha256'],'source_row_context':w,'raw_text':args[1]['text'],
        'cell_id':args[1]['id'],'processed_cell_version_id':'synthetic',
        'source_space_witnesses':[{'raw_separator_span':[2,3],'nonwhitespace_boundary':2}]}
    assert len(mapped_space_deletions(p,args[7]))==3
    assert project_page_text(args[7],[p],args[6])['whole_selected_page_exactly_reproduced']is True


def test_unrelated_body_word_spacing_still_fails_full_page_identity():
    args=list(fixture());args[6]='Otherword\n'+args[6];args[7]='Other word\n'+args[7]
    w=prove_multiline_context(*args)
    p={'raw_native_page_sha256':w['raw_native_page_sha256'],'source_row_context':w,'raw_text':args[1]['text'],
       'cell_id':args[1]['id'],'processed_cell_version_id':'synthetic',
       'source_space_witnesses':[{'raw_separator_span':[2,3],'nonwhitespace_boundary':2}]}
    with pytest.raises(ValueError,match='complete selected page'):project_page_text(args[7],[p],args[6])
