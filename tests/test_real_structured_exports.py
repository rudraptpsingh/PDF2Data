"""Opt-in checks of CLI-produced real exports, never ad hoc PDF extraction.

Set PDF2DATA_REAL_EXPORTS to a directory containing sample, october-em and rop
readable exports generated from the three documented source packages.
"""
import json,os
from pathlib import Path
import pytest
from local_vector_batch.document_blocks import validate_blocks

ROOT=Path(os.environ['PDF2DATA_REAL_EXPORTS']) if os.environ.get('PDF2DATA_REAL_EXPORTS') else None
pytestmark=pytest.mark.skipif(ROOT is None,reason='real CLI exports not configured')

def load(name):
    folder=ROOT/name;data=json.loads((folder/'data.json').read_text());validate_blocks(data['document_blocks'],data['pages'])
    graph=json.loads((folder/'relations.json').read_text());ids={n['id'] for n in graph['nodes']}
    assert all(e['from'] in ids and e['to'] in ids for e in graph['edges'])
    assert (folder/'layout.jsonl').is_file() and data['layout']
    assert data['publication_eligible'] is False
    return data,(folder/'document.md').read_text()

def test_real_generic_table_is_inline_once_with_original_heading_order():
    data,md=load('sample')
    assert md.index('3. Outputs')<md.index('<table')<md.index('4. Review')
    assert md.count('<table')==1 and 'Format Purpose' not in md
    assert '<th>Format</th>' in md and not data['document_blocks']['unresolved_tables']

def test_real_two_illustrations_keep_all_36_cells_and_distinct_values():
    data,md=load('october-em');tables=data['tables']
    assert len(tables)==2 and all((t['row_count'],t['column_count'])==(2,9) for t in tables)
    assert sum(len(row) for t in tables for row in t['cells'])==36
    assert tables[0]['cells'][1][7]=='31.12.2027' and tables[1]['cells'][1][7]=='Not effective'
    assert md.count('<table')==2 and md.count('Not effective')==1
    blocks=data['document_blocks']['blocks'];positions=[i for i,b in enumerate(blocks) if b['kind']=='table']
    between=blocks[positions[0]+1:positions[1]]
    assert [b['marker'] for b in between if b['kind']=='list_item']==['(a)','(b)','(c)']
    assert any(b['kind']=='heading' and 'Illustration 2' in b['spans'][0]['exact_quote'] for b in between)
    assert len([b for b in blocks if b['kind']=='footer'])==6
    assert any(r['kind']=='footer_candidate' and r['span']['page']==5 for r in data['structure']['regions'])
    assert 'Page 5(a)' not in md and 'Explanaotory Memorandum to supplementary draft' not in md
    assert not data['document_blocks']['unresolved_tables']

def test_real_cross_page_optional_rejoinder_stays_intact():
    data,md=load('rop')
    direction=next(b for b in data['document_blocks']['blocks'] if 'if any' in ''.join(s['exact_quote'] for s in b['spans']))
    assert [s['page'] for s in direction['spans']]==[1,2]
    assert 'within two weeks thereafter' in md and 'RoP in Petition' not in md
    raw='\n'.join(p['text'] for p in data['pages']);assert 'RoP in Petition' in raw
