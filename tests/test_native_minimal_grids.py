"""Source-native one-column/one-row grids keep all ordinary proof gates."""
import copy
import hashlib
import json
from pathlib import Path
import pytest


def grid_pdf(path,rows,columns,*,blank_last=False):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=240,height=320)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    x=[30+180*c/columns for c in range(columns+1)];y=[280-120*r/rows for r in range(rows+1)]
    content=['0 G .6 w']
    for xx in x:content.append(f'{xx} {y[-1]} m {xx} {y[0]} l S')
    for yy in y:content.append(f'{x[0]} {yy} m {x[-1]} {yy} l S')
    values=['ALPHA','BETA','GAMMA','DELTA']
    for r in range(rows):
        for c in range(columns):
            if blank_last and r==rows-1 and c==columns-1:continue
            value=values[(r*columns+c)%len(values)]
            content.append(f'BT /F1 10 Tf {x[c]+8} {(y[r]+y[r+1])/2} Td ({value}) Tj ET')
    stream=DecodedStreamObject();stream.set_data('\n'.join(content).encode());page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path);return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('rows,columns',[(2,1),(1,2),(3,1)])
def test_fresh_preview_preserves_minimal_grid_and_independent_cells(tmp_path,rows,columns):
    from local_vector_batch.l1_review import preview_tables
    path=tmp_path/'grid.pdf';digest=grid_pdf(path,rows,columns)
    result=preview_tables(path,1,digest)
    assert len(result)==1;table=result[0]
    assert(table['row_count'],table['column_count'])==(rows,columns)
    assert len(table['cell_spans'])==rows*columns and not table['unresolved_grid_slots']
    assert all(c['readers_agree']for c in table['cell_spans'])
    assert table['status']=='repair_preview_not_approved'
    assert table['header_associations']=='candidate_only'


def test_blank_source_cell_is_not_inherited_or_filled(tmp_path):
    from local_vector_batch.l1_review import preview_tables
    path=tmp_path/'grid.pdf';digest=grid_pdf(path,2,1,blank_last=True)
    table=preview_tables(path,1,digest)[0];blank=next(c for c in table['cell_spans']if c['row']==1)
    assert blank['text']==''and blank['independent_text']==''and blank['readers_agree']
    assert table['blank_cell_policy']=='never_inherit_or_fill'


@pytest.mark.parametrize('rows,columns',[(2,1),(1,2)])
def test_new_extraction_retains_raw_minimal_grid_and_complete_reproof(tmp_path,rows,columns):
    from local_vector_batch.legal_l1 import extract_document,verify_document
    from local_vector_batch.native_table_regions import build_version
    source=tmp_path/'grid.pdf';digest=grid_pdf(source,rows,columns);parent=tmp_path/'parent'
    extract_document({'id':'synthetic-source','path':str(source),'sha256':digest,'family':'draft',
                      'expected_pages':1,'url':'https://example.org/synthetic.pdf'},parent)
    raw=json.loads((parent/'extraction/tables.json').read_text())
    assert len(raw)==1 and len(raw[0]['cells'])==rows and len(raw[0]['cells'][0])==columns
    feedback=tmp_path/'feedback.jsonl';feedback.write_text('');dossier=tmp_path/'dossier.json';dossier.write_text('{"decisions":[]}')
    runtime=Path(__file__).resolve().parents[1];before={name:(parent/name).read_bytes()for name in('original.pdf','extraction/pages.jsonl','l1-package.json','quality.json')}
    result=build_version(parent,runtime,feedback,dossier,1,tmp_path/'proof')
    assert result['status']=='passed'and result['answer_publication_eligible']is False
    destination=Path(result['new_l1_package_path']);verify_document(destination)
    q=json.loads((destination/'quality.json').read_text())['results'][0]
    assert q['page_complete']and all(c['state']=='pass'for c in q['components'].values())
    assert all((parent/name).read_bytes()==value for name,value in before.items())


def test_minimal_shape_cannot_waive_reading_or_raw_grid_conflict(tmp_path):
    from local_vector_batch.l1_review import preview_tables
    from local_vector_batch.native_table_feedback import validate_feedback
    path=tmp_path/'grid.pdf';digest=grid_pdf(path,2,1);table=preview_tables(path,1,digest)[0]
    raw={**copy.deepcopy(table),'cells':[[c['text']]for c in table['cell_spans']],'extraction_basis':'native_pdf_geometry'}
    snapshot={'source_pdf_sha256':digest,'page':1,'page_text_sha256':'test','tables':[table],'raw_tables':[raw],
              'fresh_quality':{'results':[{'components':{'layout':{'state':'unknown'}}}]}}
    cell=table['cell_spans'][0];cell['independent_text']='DIFFERENT';cell['independent_cell_sha256']=hashlib.sha256(b'DIFFERENT').hexdigest();cell['readers_agree']=False
    assert validate_feedback(snapshot,[],{})['status']=='held'
    raw['cells'][0][0]='MUTATED'
    with pytest.raises(ValueError):validate_feedback(snapshot,[],{})


def test_zero_dimensional_grid_cannot_be_presented_as_a_proved_blank_table():
    from local_vector_batch.native_table_feedback import validate_feedback
    table={'bbox':[0,0,20,20],'row_count':0,'column_count':1,'cell_spans':[],
           'table_geometry_sha256':'fake','unresolved_grid_slots':[]}
    snapshot={'source_pdf_sha256':'source','page':1,'tables':[table],
              'raw_tables':[{**table,'cells':[],'extraction_basis':'native_pdf_geometry'}]}
    with pytest.raises(ValueError,match='positive dimensions'):validate_feedback(snapshot,[],{})
