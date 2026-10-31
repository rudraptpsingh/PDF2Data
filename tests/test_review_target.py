import copy
import pytest

from local_vector_batch.review_queue import validate_review_target


def fixture():
    cell={'id':'cell','row':2,'column':1,'bbox':[10,20,30,40],
          'selected_cell_sha256':'a'*64,'independent_cell_sha256':'b'*64}
    table={'table_version_id':'version','table_geometry_sha256':'c'*64,'cell_spans':[cell]}
    target={**cell,'cell_id':cell['id'],'table_version_id':'version','table_geometry_sha256':'c'*64,
            'source_pdf_sha256':'d'*64,'page':3}
    return target,[table]


def test_target_selects_exact_queued_cell_not_first_cell():
    target,tables=fixture()
    assert validate_review_target(target,tables,'d'*64,3)=={'table_index':0,'cell_id':'cell','row':2,'column':1}
    assert validate_review_target(None,tables,'d'*64,3)is None


@pytest.mark.parametrize('key,value', [('page',4),('source_pdf_sha256','e'*64),('table_version_id','old'),
    ('cell_id','other'),('row',0),('column',0),('bbox',[1,2,3,4]),
    ('selected_cell_sha256','e'*64),('independent_cell_sha256','e'*64),('table_geometry_sha256','e'*64)])
def test_stale_source_geometry_or_reading_cannot_select_another_cell(key,value):
    target,tables=fixture();target[key]=value
    with pytest.raises(ValueError):validate_review_target(target,tables,'d'*64,3)


def test_ambiguous_cell_membership_rejects():
    target,tables=fixture();tables.append(copy.deepcopy(tables[0]))
    with pytest.raises(ValueError):validate_review_target(target,tables,'d'*64,3)
