"""Closed geometry controls, not document-reading accuracy examples."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from local_vector_batch.native_tables import find_native_tables, resolve_cached_candidates, table_shape


def fixture(columns=1,color=1.0):
    boxes=[[(c*100,r*20,(c+1)*100,(r+1)*20)for c in range(columns)]for r in range(2)]
    matrix=[[f'Literal {r},{c}'for c in range(columns)]for r in range(2)]
    found=SimpleNamespace(bbox=(0,0,columns*100,40),rows=[SimpleNamespace(cells=row)for row in boxes],extract=lambda:deepcopy(matrix))
    rects=[dict(object_type='rect',x0=b[0],top=b[1],x1=b[2],bottom=b[3],width=b[2]-b[0],height=b[3]-b[1],fill=True,stroke=False,non_stroking_color=color)for row in boxes for b in row]
    chars=[dict(text='X',x0=b[0]+10,x1=b[0]+20,top=b[1]+2,bottom=b[1]+12)for row in boxes for b in row]
    page=SimpleNamespace(rects=rects,lines=[],curves=[],chars=chars,find_tables=lambda:[found],filter=lambda predicate:SimpleNamespace(find_tables=lambda:[]))
    return page,found,matrix


def test_white_fill_grid_classified_with_full_raw_evidence():
    page,found,matrix=fixture();excluded=[]
    assert find_native_tables(page,white_fill_candidates=excluded)==[found]
    assert len(excluded)==1 and excluded[0]['raw_cells']==matrix
    assert excluded[0]['source_cell_boxes']==[list(r.cells[0])for r in found.rows]
    assert not excluded[0]['source_quality_clearance'] and not excluded[0]['semantic_table_proven']


def assert_classification_held(page,found):
    proofs=[]
    assert find_native_tables(page,white_fill_candidates=proofs)==[found]
    assert proofs==[]


@pytest.mark.parametrize('color',[.9,[.8,.9,1],[0,0,0,1],None])
def test_colored_or_unknown_background_remains_ambiguous(color):
    page,found,_=fixture(color=color)
    assert_classification_held(page,found)


def test_borderless_multi_column_candidate_is_retained():
    page,found,_=fixture(columns=2)
    assert_classification_held(page,found)


@pytest.mark.parametrize('kind',['stroked_rectangle','line_at_boundary','curve','unmatched_box','second_baseline','empty_glyphs','overlapping_rectangles'])
def test_visible_or_ambiguous_source_evidence_retains_candidate(kind):
    page,found,_=fixture()
    if kind=='stroked_rectangle':page.rects[0]['stroke']=True
    elif kind=='line_at_boundary':page.lines=[dict(object_type='line',x0=0,top=0,x1=100,bottom=0,stroke=True)]
    elif kind=='curve':page.curves=[dict(object_type='curve',x0=20,top=5,x1=25,bottom=10,stroke=True)]
    elif kind=='unmatched_box':page.rects[0]['x0']+=.6
    elif kind=='second_baseline':page.chars.append({**page.chars[0],'top':7,'bottom':17})
    elif kind=='empty_glyphs':page.chars=[]
    else:page.rects.append(deepcopy(page.rects[0]))
    assert_classification_held(page,found)


def test_filtered_source_grid_is_never_discarded():
    page,found,_=fixture();page.filter=lambda predicate:SimpleNamespace(find_tables=lambda:[found])
    assert_classification_held(page,found)


def test_cached_table_match_requires_exact_cells_and_geometry():
    page,found,matrix=fixture();table=dict(id='t',page=1,bbox=list(found.bbox),**table_shape(found,matrix,'t'))
    before=deepcopy(table)
    result=resolve_cached_candidates(page,[table]);assert result['layout_gate_table_count']==0
    assert table==before and not result['archived_tables_modified']
    changed=deepcopy(table);changed['cells'][0][0]='Different reading'
    assert resolve_cached_candidates(page,[changed])['layout_gate_table_count']==1
    changed=deepcopy(table);changed['cell_spans'][0]['bbox'][0]+=.01
    assert resolve_cached_candidates(page,[changed])['layout_gate_table_count']==1
