"""Actual captured one-row-table and synthetic adversarial inventory controls."""
import copy
import json
from pathlib import Path
import pytest
from local_vector_batch.native_source_table_inventory import build_inventory_view,effective_source_tables
from local_vector_batch.native_table_feedback import validate_feedback
from test_native_table_feedback import fixture


def source(tmp_path,guard='if len(cells) < 2:\n            continue'):
    p=tmp_path/'src/local_vector_batch';p.mkdir(parents=True)
    (p/'pdf_ingest.py').write_text('def extract_pdf():\n    for cells in tables:\n        '+guard+'\n')
    return tmp_path


def observed():
    capture=json.loads((Path(__file__).parent/'fixtures/source_one_row_p117.json').read_text())
    s=fixture();table=capture['table'];s.update(source_pdf_sha256=capture['source_pdf_sha256'],page=117)
    # Existing synthetic original table occupies [0,0,40,40], disjoint from
    # the actual captured one-row table at [72.26,72.24,523.18,121.70].
    s['tables'].insert(0,table)
    return s


def test_actual_source_one_row_full_matrix_is_additive_not_raw_rewrite(tmp_path):
    s=observed();original=copy.deepcopy(s['raw_tables']);s['source_table_inventory_view']=build_inventory_view(s,source(tmp_path))
    assert s['raw_tables']==original
    v=s['source_table_inventory_view'];assert v['original_raw_table_count']==1 and v['complete_fresh_table_count']==2
    assert v['additive_tables'][0]['cells']==[['16.','Normal Maximum Leading MVAR at rated\nMW output','','','']]
    assert v['source_quality_clearance']is False
    assert len(effective_source_tables(s))==2
    result=validate_feedback(s,[],{})
    assert len(result['tables'])==2 and result['page_quality_cleared']is False


@pytest.mark.parametrize('attack',['extra','missing','reordered','matrix','digits','geometry','source','page','raw'])
def test_attacker_inventory_changes_cannot_reuse_view(tmp_path,attack):
    s=observed();s['source_table_inventory_view']=build_inventory_view(s,source(tmp_path))
    if attack=='extra':s['tables'].append(copy.deepcopy(s['tables'][0]))
    if attack=='missing':s['tables'].pop()
    if attack=='reordered':s['tables'].reverse()
    if attack=='matrix':s['source_table_inventory_view']['additive_tables'][0]['cells'][0][0]='17.'
    if attack=='digits':s['tables'][0]['cell_spans'][0]['text']='17.'
    if attack=='geometry':s['tables'][0]['bbox'][0]+=1
    if attack=='source':s['source_pdf_sha256']='other'
    if attack=='page':s['page']=118
    if attack=='raw':s['raw_tables'][0]['cells'][0][0]='modified'
    with pytest.raises(ValueError):validate_feedback(s,[],{})


def test_unknown_historical_filter_cannot_authorize_extra_table(tmp_path):
    s=observed();assert build_inventory_view(s,source(tmp_path,'if not cells:\n            continue'))is None
    with pytest.raises(ValueError,match='cardinality'):validate_feedback(s,[],{})


def test_original_missing_or_modified_table_cannot_be_hidden(tmp_path):
    s=observed();s['tables'].pop()
    # Equal count is deliberately not a reconciliation. Ordinary strict
    # geometry matching rejects the substituted table.
    assert build_inventory_view(s,source(tmp_path))is None
    with pytest.raises(ValueError):validate_feedback(s,[],{})
