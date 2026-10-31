from copy import deepcopy
import json
from hashlib import sha256

from test_document_continuations import table_fixture
from local_vector_batch.document_continuations import build_continuations
from local_vector_batch.table_contexts import iter_table_contexts, text_evidence, write_table_contexts


def test_repeated_values_retain_cell_local_quotes_but_never_invent_page_offsets():
    evidence=text_evidence('10','10 10')
    assert evidence['cell_text_span']['exact_quote']=='10'
    assert evidence['raw_text_sha256']==sha256(b'10').hexdigest()
    assert evidence['selected_page_evidence']==dict(status='ambiguous_multiple_occurrences',spans=[])
    assert text_evidence('11','10')['selected_page_evidence']['status']=='not_in_selected_text'
    assert text_evidence(None,'')['cell_text_span'] is None


def test_continued_table_references_real_header_without_copying_or_mutating_cells(tmp_path):
    data=table_fixture();data['document_model']={'continuations':build_continuations(data)}
    original=deepcopy(data);records=list(iter_table_contexts(data))
    cell=records[1]['cells'][1]
    assert cell['raw_text']=='20' and cell['left_row_context_cell_ids']==['t2:r0:c0']
    assert cell['column_header_candidates']==[dict(table_id='t1',cell_id='t1:r0:c1',page=1)]
    assert records[1]['header_source_table_id']=='t1'
    assert records[1]['continuation_candidate_id']==data['document_model']['continuations']['links'][0]['id']
    assert all(not r['publication_eligible'] and r['semantic_review_required'] for r in records)
    assert data==original
    path=tmp_path/'table-contexts.jsonl';assert write_table_contexts(path,data)==2
    assert [json.loads(l)for l in path.read_text().split('\n')if l]==records


def test_multirow_merged_header_paths_and_blank_cells_are_preserved():
    data=table_fixture();table=data['tables'][0]
    table['cells']=[['Charge (INR lakh)',None],['Entity','Amount'],['A',None]]
    def cell(r,c,text,span=1,role='data'):
        return dict(id=f'h{r}{c}',row=r,column=c,row_span=1,column_span=span,text=text,
                    bbox=[10+c*50,10+r*10,10+(c+span)*50,20+r*10],role_candidate=role)
    table['cell_spans']=[cell(0,0,'Charge (INR lakh)',2,'header'),cell(1,0,'Entity',role='header'),
                        cell(1,1,'Amount',role='header'),cell(2,0,'A'),cell(2,1,None)]
    record=next(iter_table_contexts(data));value=record['cells'][-1]
    assert [r['cell_id']for r in value['column_header_candidates']]==['h00','h11']
    assert value['raw_text'] is None and value['cell_text_span'] is None
    assert record['context_candidates'][0]['roles']==['unit']


def test_numeric_first_row_is_not_a_header_and_proximity_does_not_assign_period_or_unit():
    data=table_fixture();table=data['tables'][0]
    table['cells']=[['Plant A','10'],['Plant B','20']]
    data['layout'][0]['lines']=[dict(text='As of 02.03.2025 (INR lakh)',x0=50,top=660,x1=350,bottom=680),
                              dict(text='Note: as of 02.03.2026',x0=50,top=985,x1=350,bottom=995)]
    record=next(iter_table_contexts(data))
    assert 'column_headers_unresolved' in record['quality_warnings']
    assert all(not c['column_header_candidates'] for c in record['cells'])
    assert len(record['context_candidates'])==2
    assert all(c['association']=='proximity_only_scope_not_inferred'for c in record['context_candidates'])
    assert 'normalized_period' not in record and 'amount' not in record


def test_reader_grid_without_geometry_is_explicitly_unresolved_and_keeps_unicode():
    data=table_fixture();data['tables'][0].pop('cell_spans')
    data['tables'][0]['cells'][1][0]='नाम\u2028दूसरा'
    record=next(iter_table_contexts(data))
    assert len(record['cells'])==4 and 'cell_geometry_unavailable' in record['quality_warnings']
    assert record['cells'][2]['raw_text']=='नाम\u2028दूसरा'
    assert record['cells'][2]['bbox'] is None


def test_native_glyph_layout_supplies_caption_evidence_without_a_lines_sidecar():
    data=table_fixture();caption='Amounts (INR lakh)'
    data['pages'][0]['text']=caption+'\n'+data['pages'][0]['text']
    data['layout'][0]['glyphs']=[dict(text=char,bbox=[50+i*5,670,55+i*5,680]) for i,char in enumerate(caption)]
    record=next(iter_table_contexts(data))
    candidate=next(c for c in record['context_candidates']if c['location']=='nearby_source_line')
    assert candidate['raw_text']==caption and candidate['roles']==['unit']
    assert candidate['selected_page_evidence']['status']=='exact_unique'
