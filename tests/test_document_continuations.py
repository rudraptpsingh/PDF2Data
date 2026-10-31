from copy import deepcopy
from hashlib import sha256

import pytest

from local_vector_batch.document_blocks import span, render_blocks
from local_vector_batch.document_continuations import build_continuations
from local_vector_batch.document_model import build_document_model, validate_document_model
from local_vector_batch.readable_export import full_document


def fixture(texts, kinds=None):
    pages=[];blocks=[]
    for i,text in enumerate(texts,1):
        page=dict(id=f'd:p{i}',page=i,text=text,text_sha256=sha256(text.encode()).hexdigest(),method='pdf_text')
        pages.append(page)
        blocks.append(dict(id=f'b{i}',kind=(kinds or ['paragraph']*len(texts))[i-1],order=i-1,
                           spans=[span(page,0,len(text))],parent_unit_ids=['u'],
                           bbox=[50,900 if i==1 else 20,350,980 if i==1 else 100],
                           semantic_state='candidate'))
    return dict(source_id='d',source_pdf_sha256='a'*64,title='Example',pages=pages,
                layout=[dict(page=p['page'],width=600,height=1000,glyphs=[]) for p in pages],
                document_blocks=dict(blocks=blocks,unresolved_tables=[],publication_eligible=False),tables=[],fact_candidates=[],
                structure=dict(regions=[],units=[dict(id='u',label='opening',spans=[s for b in blocks for s in b['spans']],
                                                    boundary_state='candidate',semantic_state='candidate')]))


def table_fixture():
    data=fixture(['Entity Amount\nPlant A 10','Plant B 20'],['table','table'])
    for i,(rows,box) in enumerate([([['Entity','Amount'],['Plant A','10']],[50,700,350,980]),
                                 ([['Plant B','20']],[50,20,350,200])],1):
        cells=[]
        for r,row in enumerate(rows):
            for c,value in enumerate(row):
                cells.append(dict(id=f't{i}:r{r}:c{c}',row=r,column=c,row_span=1,column_span=1,
                                  text=value,bbox=[50+c*150,box[1],200+c*150,box[3]],role_candidate='unresolved'))
        data['tables'].append(dict(id=f't{i}',table_version_id=f'tv{i}',page=i,bbox=box,
                                   cells=rows,cell_spans=cells))
        data['document_blocks']['blocks'][i-1].update(table_id=f't{i}',bbox=box)
    return data


def test_continuation_retains_fragments_and_references_original_header_without_filling_cells():
    data=table_fixture();original=deepcopy(data)
    result=build_continuations(data);link=result['links'][0]
    assert link['kind']=='table_continuation_candidate'
    assert link['header_context']['raw_cells']==['Entity','Amount']
    assert link['header_context']['table_id']=='t1'
    assert not link['repeated_header_candidate']
    assert link['page_text_sha256']==[p['text_sha256']for p in data['pages']]
    assert data==original
    assert result['publication_eligible'] is False


@pytest.mark.parametrize('case,reason',[
    ('columns','column_geometry_differs'),('header','new_or_conflicting_header'),
    ('total','preceding_table_has_terminal_total'),('caption','intervening_body_content_or_new_section'),
    ('geometry','source_geometry_unavailable'),('years','header_origin_not_established'),
    ('middle','table_not_at_adjacent_page_boundary')])
def test_new_tables_and_ambiguous_boundaries_are_not_silently_joined(case,reason):
    data=table_fixture();a,b=data['tables']
    if case=='columns':b['cell_spans'][0]['bbox'][2]+=30
    elif case=='header':b['cells'][0]=['Different measure','New period']
    elif case=='total':a['cells'][-1][0]='Grand total'
    elif case=='caption':
        block=deepcopy(data['document_blocks']['blocks'][1]);block.update(id='caption',kind='heading',bbox=[50,1,350,15])
        block['spans']=[{**block['spans'][0],'exact_quote':'Annexure 2'}]
        data['document_blocks']['blocks'].insert(1,block)
    elif case=='geometry':b['bbox']=None
    elif case=='years':a['cells'][0]=['Entity','2026']
    elif case=='middle':a['bbox'][3]=400
    result=build_continuations(data)
    assert not result['links']
    assert result['unresolved_boundaries'][0]['reason']==reason


@pytest.mark.parametrize('first,second',[('(a)','(b)'),('1.','2.'),('(i)','(ii)'),('ii.','iii.'),('•','•')])
def test_list_sequence_carries_across_page_with_its_source_parent(first,second):
    data=fixture([first+' First item',second+' Second item'],['list_item','list_item'])
    for block,marker in zip(data['document_blocks']['blocks'],(first,second)):block['marker']=marker
    assert build_continuations(data)['links'][0]['kind']=='list_continuation_candidate'


@pytest.mark.parametrize('case',['restart','indent','parent','heading','missing_page','finished_sentence'])
def test_unrelated_content_is_not_marked_as_a_continuation(case):
    data=fixture(['(a) First item','(b) Second item'],['list_item','list_item'])
    a,b=data['document_blocks']['blocks'];a['marker']='(a)';b['marker']='(b)'
    if case=='restart':b['marker']='(a)'
    elif case=='indent':b['bbox'][0]=120
    elif case=='parent':b['parent_unit_ids']=['other']
    elif case=='heading':b['kind']='heading'
    elif case=='missing_page':
        data['pages'][1]['page']=3;b['spans'][0]['page']=3
    elif case=='finished_sentence':
        a['kind']='paragraph';a['spans'][0]['exact_quote']='A complete sentence.'
        b['kind']='paragraph';b['spans'][0]['exact_quote']='new text'
    assert not build_continuations(data)['links']


@pytest.mark.parametrize('first,kind',[('A condition continues','paragraph'),('(a) A condition continues','list_item')])
def test_split_paragraph_or_list_item_links_exact_fragments(first,kind):
    data=fixture([first,'on the next page, subject to review.'],[kind,'paragraph'])
    link=build_continuations(data)['links'][0]
    assert link['kind']==('paragraph_continuation_candidate' if kind=='paragraph' else 'list_item_continuation_candidate')
    assert link['evidence']==[s for b in data['document_blocks']['blocks']for s in b['spans']]


def test_reference_is_chained_over_three_pages_and_terminal_total_stops_next_group():
    data=table_fixture();third=deepcopy(data['tables'][1]);third.update(id='t3',page=3,table_version_id='tv3')
    data['tables'][1]['bbox'][3]=980
    third['cells']=[['Grand total','30']];third['bbox'][3]=980;data['tables'].append(third)
    p=deepcopy(data['pages'][1]);p.update(id='d:p3',page=3);data['pages'].append(p)
    data['layout'].append(dict(page=3,width=600,height=1000,glyphs=[]))
    b=deepcopy(data['document_blocks']['blocks'][1]);b.update(id='b3',table_id='t3')
    b['spans'][0]['page']=3;data['document_blocks']['blocks'].append(b)
    links=build_continuations(data)['links']
    assert len(links)==2 and links[0]['group_id']==links[1]['group_id']
    assert links[1]['header_context']['table_id']=='t1'
    fourth=deepcopy(third);fourth.update(id='t4',page=4);fourth['cells']=[['Plant C','40']];data['tables'].append(fourth)
    p=deepcopy(p);p.update(id='d:p4',page=4);data['pages'].append(p)
    data['layout'].append(dict(page=4,width=600,height=1000,glyphs=[]))
    result=build_continuations(data)
    assert len(result['links'])==2
    assert result['unresolved_boundaries'][-1]['reason']=='preceding_table_has_terminal_total'


@pytest.mark.parametrize('amount',['0','-15.20687','−15.20687','(15.20687)','4,035,512.5','100%'])
def test_native_and_structural_header_proposals_do_not_use_numeric_data_rows(amount):
    from types import SimpleNamespace
    from local_vector_batch.native_tables import table_shape
    from local_vector_batch.structure_candidates import table_candidates
    cells=[['Plant A',amount],['Plant B','7']]
    found=SimpleNamespace(rows=[SimpleNamespace(cells=[(50+c*150,20+r*30,200+c*150,50+r*30)
                                                     for c in range(2)])for r in range(2)])
    shape=table_shape(found,cells,'t')
    data=fixture(['\n'.join(' '.join(row)for row in cells)])
    table=dict(id='t',bbox=[50,20,350,80],**shape)
    candidates=table_candidates(table,data['pages'][0])
    assert all(c['role_candidate']=='unresolved' for c in shape['cell_spans']if c['row']==0)
    assert all(not c['proposed_header'] and c['candidate_header_row'] is None for c in candidates)
    assert [c['value_text']for c in candidates]==[v for row in cells for v in row]


def test_export_exposes_links_in_graph_and_readable_source_navigation_and_rejects_tampering():
    data=table_fixture();data['document_model']=build_document_model(data)
    md,html,graph=full_document(data)
    assert 'Continues on source page 2' in md and 'Continued from source page 1' in html
    assert any(e['type']=='table_continuation_candidate' for e in graph['edges'])
    ids={n['id'] for n in graph['nodes']}
    assert all(e['from'] in ids and e['to'] in ids for e in graph['edges'])
    data['document_model']['continuations']['links'][0]['header_context']['raw_cells'][1]='Invented'
    with pytest.raises(ValueError,match='continuation'):validate_document_model(data['document_model'],data)


def test_common_opening_parent_does_not_glue_contact_line_to_next_pages_abstract():
    from local_vector_batch.document_blocks import build_blocks
    data=fixture(['Office contact: example@example.org','ABSTRACT OF CHARGES'])
    blocks=build_blocks(data)['blocks']
    assert len(blocks)==2
    assert [b['spans'][0]['page'] for b in blocks]==[1,2]


def test_numbered_clause_continuation_can_start_with_a_capitalized_party_role():
    from local_vector_batch.document_blocks import build_blocks
    data=fixture(['2. An opportunity is granted to the impleaded','Respondents to file replies, if any.'])
    data['structure']['units'][0]['label']='2'
    for layout,page,y in zip(data['layout'],data['pages'],(900,20)):
        layout['glyphs']=[dict(text=c,bbox=[50+i*5,y,55+i*5,y+10],font='Arial',size=10)
                          for i,c in enumerate(page['text'])]
    data['document_blocks']=build_blocks(data)
    assert len(data['document_blocks']['blocks'])==1
    data['document_model']=build_document_model(data)
    links=data['document_model']['continuations']['links']
    assert links[0]['kind']=='paragraph_continuation_candidate'
    md,html=render_blocks(data)
    assert 'source.pdf#page=1' in md and 'source.pdf#page=2' in md
