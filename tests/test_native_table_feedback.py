import copy
from hashlib import sha256
import pytest

from local_vector_batch.native_table_feedback import validate_feedback
from local_vector_batch.native_table_regions import grid_path_owner


def fixture():
    cells=[]
    for r in range(2):
        for c in range(2):
            text=str(r*2+c);digest=sha256(text.encode()).hexdigest()
            cells.append({'id':f't:v:r{r}:c{c}','row':r,'column':c,'row_span':1,'column_span':1,
                          'bbox':[c*20,r*20,c*20+20,r*20+20],'text':text,'independent_text':text,
                          'selected_cell_sha256':digest,'independent_cell_sha256':digest,
                          'readers_agree':True,'glyph_audit':{'issues':[]},'independent_glyph_audit':{'issues':[]}})
    table={'table_version_id':'t:v','bbox':[0,0,40,40],'table_geometry_sha256':'grid','row_count':2,'column_count':2,'unresolved_grid_slots':[],
           'cell_spans':cells,'page_width':100,'page_height':100}
    raw={**copy.deepcopy(table),'cells':[['0','1'],['2','3']],'extraction_basis':'native_pdf_geometry'}
    return {'source_pdf_sha256':'source','page':1,'page_text_sha256':'page','tables':[table],'raw_tables':[raw],
            'fresh_quality':{'results':[{'components':{'layout':{'state':'unknown'}}}]}}


def test_independent_native_cells_validate_grid_without_page_or_legal_claim():
    result=validate_feedback(fixture(),[],{})
    assert result['status']=='validated'and len(result['tables'][0]['cells'])==4
    assert not result['page_quality_cleared']and not result['answer_publication_eligible']


def test_raw_grid_and_spans_are_independently_checked():
    for field in ['cells','cell_spans','table_geometry_sha256']:
        snapshot=fixture()
        if field=='cells':snapshot['raw_tables'][0]['cells'][0][0]='different'
        elif field=='cell_spans':snapshot['raw_tables'][0]['cell_spans'][0]['text']='different'
        else:snapshot['raw_tables'][0][field]='wrong'
        with pytest.raises(ValueError):validate_feedback(snapshot,[],{})


def test_unknown_feedback_table_or_source_never_promotes_grid():
    with pytest.raises(ValueError):validate_feedback(fixture(),[{'kind':'extraction_table_cell_feedback','source_pdf_sha256':'wrong'}],{})


def test_grid_masks_never_authorize_interiors_or_external_regions():
    table=fixture()['tables'][0]
    assert grid_path_owner([0,0,.5,40],[table],3)=='t:v'
    assert grid_path_owner([10,10,30,30],[table],3)is None
    assert grid_path_owner([60,0,60.5,40],[table],3)is None
