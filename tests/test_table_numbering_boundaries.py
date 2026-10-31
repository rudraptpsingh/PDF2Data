"""A numbered illustration row is not automatically a new provision boundary."""
import hashlib
import pytest
from local_vector_batch.legal_l1 import structure_document, validate_structure

def source():
    lines=['10. Amendment to land conditions','Illustration:','1. 600 MW Rajasthan','3. 200 MW Telangana','11. Amendment to withdrawal']
    text='\n'.join(lines);page={'page':1,'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    glyphs=[]
    for row,line in enumerate(lines):
        for col,char in enumerate(line):glyphs.append({'text':char,'bbox':[20+col*6,20+row*20,26+col*6,30+row*20]})
    layout={'page':1,'width':500,'height':500,'glyphs':glyphs}
    table={'id':'t1','page':1,'bbox':[15,55,300,95],'extraction_basis':'native_pdf_geometry'}
    return page,layout,table

def test_source_geometry_keeps_numbered_rows_inside_parent_unit():
    page,layout,table=source();s=structure_document([page],'a'*64,'draft',layout=[layout],tables=[table])
    assert [u['label'] for u in s['units']]==['10','11']
    assert [r['span']['exact_quote'] for r in s['table_numbering_candidates']]==['1. ','3. ']
    assert '1. 600 MW Rajasthan' in s['units'][0]['spans'][0]['exact_quote']
    assert '3. 200 MW Telangana' in s['units'][0]['spans'][0]['exact_quote']
    assert ''.join(x['exact_quote'] for u in s['units'] for x in u['spans'])==page['text']
    validate_structure(s,[page],'a'*64)

@pytest.mark.parametrize('change',['no_layout','no_table','unproven_table','outside'])
def test_without_geometric_binding_numbering_remains_unresolved(change):
    page,layout,table=source();ls=[layout];ts=[table]
    if change=='no_layout':ls=[]
    elif change=='no_table':ts=[]
    elif change=='unproven_table':table['extraction_basis']='model_candidate'
    else:table['bbox']=[350,55,490,95]
    s=structure_document([page],'a'*64,'draft',layout=ls,tables=ts)
    assert [u['label'] for u in s['units']]==['10','1','3','11']
    assert not s['table_numbering_candidates'];validate_structure(s,[page],'a'*64)

def test_table_numbering_receipt_tampering_rejected():
    page,layout,table=source();s=structure_document([page],'a'*64,'draft',layout=[layout],tables=[table]);s['table_numbering_candidates'][0]['span']['exact_quote']='2. '
    with pytest.raises(ValueError,match='anchor'):validate_structure(s,[page],'a'*64)
