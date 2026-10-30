from copy import deepcopy
from hashlib import sha256
import re
import pytest
from local_vector_batch.document_blocks import build_blocks,render_blocks,validate_blocks,table_html,validated_cells,footer_start
from local_vector_batch.legal_l1 import structure_document,validate_structure

def data_fixture(lines,table=None):
    text='\n'.join(t for t,y,bold in lines)
    page={'id':'d:p1','page':1,'text':text,'text_sha256':sha256(text.encode()).hexdigest(),'method':'pdf_text'}
    glyphs=[]
    for text,y,bold in lines:
        glyphs.extend({'text':char,'bbox':[10+i*5,y,15+i*5,y+10],'font':'Arial-Bold' if bold else 'Arial','size':10} for i,char in enumerate(text))
    layout={'page':1,'height':1000,'width':600,'glyphs':glyphs}
    return {'source_id':'d','source_pdf_sha256':'a'*64,'pages':[page],'layout':[layout],
      'structure':structure_document([page],'a'*64,'technical_procedure'),'tables':[table] if table else [],'fact_candidates':[]}

def table_fixture():
    return {'id':'t','page':1,'bbox':[10,50,180,90],'cells':[['Format','Purpose'],['MD','Body']],
      'table_version_id':'tv','cell_spans':[]}

def test_inline_table_is_between_source_headings_without_flattened_duplicate():
    data=data_fixture([('Guide',10,True),('1. Outputs',30,True),('Format Purpose',50,False),('MD Body',75,False),('2. Review',110,True),('Done.',130,False)],table_fixture())
    blocks=build_blocks(data);data['document_blocks']=blocks;md,html=render_blocks(data)
    assert [b['kind'] for b in blocks['blocks']]==['heading','heading','table','heading','paragraph']
    assert md.index('1. Outputs')<md.index('<table')<md.index('2. Review')
    assert md.count('<table')==1 and 'Format Purpose' not in md and 'MD Body' not in md
    assert 'Format Purpose\nMD Body' in data['pages'][0]['text']
    assert not blocks['unresolved_tables']
    validate_blocks(blocks,data['pages'])

def test_mismatching_source_or_ambiguous_table_keeps_raw_text_and_explicit_candidate():
    data=data_fixture([('1. Outputs',30,True),('Format Purpose',50,False),('MD Body',75,False)],table_fixture())
    data['tables'][0]['cells'][1][1]='Invented'
    blocks=build_blocks(data);data['document_blocks']=blocks;md,html=render_blocks(data)
    assert not any(b['kind']=='table' for b in blocks['blocks'])
    assert blocks['unresolved_tables'][0]['reason']=='table_cells_differ_from_source_glyph_inventory'
    assert 'MD Body' in md and 'Unresolved table candidates' in md
    assert '<details>' in html

def test_headings_and_explicit_list_markers_remain_ordered_and_candidate():
    data=data_fixture([('Terms',10,True),('(a) First option',40,False),('continued condition.',55,False),('(b) Second option',80,False)])
    blocks=build_blocks(data);data['document_blocks']=blocks;md,html=render_blocks(data)
    assert [b['kind'] for b in blocks['blocks']]==['heading','list_item','list_item']
    assert '- (a) First option\n  continued condition.' in md
    assert html.count('<li ')==2 and '<ol class="source-list"' in html
    assert all(b['semantic_state']=='candidate' for b in blocks['blocks'])

def test_hyphenated_legal_subclause_is_not_reinterpreted_as_list_marker():
    data=data_fixture([('Terms',10,True),('(c) Proposed sub-clauses (h-',40,False),('i) or (h-ii) remain conditional.',55,False)])
    blocks=build_blocks(data)
    assert len([b for b in blocks['blocks'] if b['kind']=='list_item'])==1
    assert '(h-\ni) or (h-ii)' in ''.join(a['exact_quote'] for b in blocks['blocks'] for a in b['spans'])

def test_bold_body_text_does_not_become_a_sequence_of_false_headings():
    data=data_fixture([('Title',10,True),('Ordinary body.',30,False),('The following conditional direction',50,True),('remains subject to review.',65,True)])
    blocks=build_blocks(data)
    assert len([b for b in blocks['blocks'] if b['kind']=='heading'])==1
    assert any(b['kind']=='paragraph' and b.get('bold_candidate') for b in blocks['blocks'])

def test_footer_requires_exact_bottom_geometry_and_terminal_page_number():
    data=data_fixture([('1. Body',50,True),('A reference to Page 1',400,False),('Instrument footer',955,False),('Instrument name Page 1',972,False)])
    page=data['pages'][0];layout=data['layout'][0];start=footer_start(page,layout)
    assert page['text'][start:]=='Instrument footer\nInstrument name Page 1'
    structure=structure_document([page],'a'*64,'technical_procedure',layout=[layout]);validate_structure(structure,[page],'a'*64)
    assert structure['regions'][-1]['kind']=='footer_candidate'
    data['structure']=structure;data['document_blocks']=build_blocks(data);md,html=render_blocks(data)
    assert 'Instrument footer' not in md and 'A reference to Page 1' in md
    lowered=deepcopy(layout)
    for glyph in lowered['glyphs']:glyph['bbox'][1]-=500;glyph['bbox'][3]-=500
    assert footer_start(page,lowered) is None

def test_realistic_footer_continuation_does_not_glue_page_marker_to_next_list():
    first='4. Text\nInstrument title Page 1';second='(a) Suppose the option applies.\nPage 2'
    pages=[{'id':f'd:p{i}','page':i,'text':t,'text_sha256':sha256(t.encode()).hexdigest()} for i,t in enumerate((first,second),1)]
    layout=[]
    for page in pages:
        glyphs=[]
        for line,y in zip(page['text'].splitlines(),(50,970)):
            glyphs.extend({'text':c,'bbox':[10+i*5,y,15+i*5,y+10],'font':'Arial','size':10} for i,c in enumerate(line))
        layout.append({'page':page['page'],'height':1000,'glyphs':glyphs})
    data={'source_id':'d','source_pdf_sha256':'a'*64,'pages':pages,'layout':layout,'structure':structure_document(pages,'a'*64,'explanatory_memorandum'),'tables':[]}
    data['document_blocks']=build_blocks(data);md,_=render_blocks(data)
    assert 'Page 1(a)' not in md and 'Instrument title' not in md and '(a) Suppose' in md
    validate_blocks(data['document_blocks'],pages)

def test_retained_merged_cells_and_header_roles_are_rendered_without_filling_blanks():
    table={'cells':[['Group',''],['A','B']],'cell_spans':[
      {'id':'c0','row':0,'column':0,'row_span':1,'column_span':2,'text':'Group','role_candidate':'header'},
      {'id':'c1','row':1,'column':0,'row_span':1,'column_span':1,'text':'A'},
      {'id':'c2','row':1,'column':1,'row_span':1,'column_span':1,'text':'B'}]}
    html=table_html(table)
    assert '<th colspan="2">Group</th>' in html and html.count('<td>')==2
    broken=deepcopy(table);broken['cells'][0][1]='Must not disappear'
    with pytest.raises(ValueError,match='hide populated'):validated_cells(broken)
    broken=deepcopy(table);broken['cell_spans'][0]['column_span']=3
    with pytest.raises(ValueError,match='invalid retained'):validated_cells(broken)

def test_source_characters_and_hashes_cannot_be_lost_or_promoted():
    data=data_fixture([('1. Exact',30,False),('Condition if any.',50,False)])
    blocks=build_blocks(data)
    changed=deepcopy(blocks);changed['blocks'][0]['spans'][0]['char_length']-=1
    with pytest.raises(ValueError):validate_blocks(changed,data['pages'])
    changed=deepcopy(blocks);changed['publication_eligible']=True
    with pytest.raises(ValueError,match='promote'):validate_blocks(changed,data['pages'])
    changed=deepcopy(data['pages']);changed[0]['text']+='changed'
    with pytest.raises(ValueError,match='hash'):validate_blocks(blocks,changed)
