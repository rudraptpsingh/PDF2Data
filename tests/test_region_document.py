from copy import deepcopy
import hashlib
import pytest
from local_vector_batch.region_document import assemble,search_projections,validate_candidates
from local_vector_batch.document_blocks import render_blocks,validate_blocks


def document(*pages,tables=()):
    out=[]
    for n,entries in enumerate(pages,1):
        blocks=[]
        for i,entry in enumerate(entries):
            text,kind,box=entry
            blocks.append(dict(region_id=f'd:p{n}:r{i}',kind=kind,bbox=box,text=text,scope='body'))
        text='\n\n'.join(b['text'] for b in blocks)
        out.append(dict(page=n,text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest(),blocks=blocks))
    return dict(source_pdf_sha256='a'*64,pages=out,tables=list(tables))


def b(text,kind='text',x=10,y=10):return (text,kind,[x,y,590,y+15])


def regions(data):return {block['region_id']:block for block in data['document_blocks']['blocks'] if 'region_id' in block}


def test_overlapping_key_value_alternatives_are_retained_but_rendered_once():
    doc=document([('Dated: 26.10.2026','key_value_region',[400,10,550,25]),
                  ('Dated:','text',[400,10,450,25]),('26.10.2026','text',[460,10,550,25])])
    data=assemble(doc);md,_=render_blocks(data)
    assert md.count('26.10.2026')==1
    assert sum(x['kind']=='alternative_reading' for x in regions(data).values())==2
    assert data['pages'][0]['text']=='Dated: 26.10.2026\n\nDated:\n\n26.10.2026'
    validate_blocks(data['document_blocks'],data['pages'])
    assert len(search_projections(data)['units'])==1


def test_same_dates_in_distinct_source_positions_are_not_deduplicated():
    data=assemble(document([b('26.10.2026',y=10),b('26.10.2026',y=50)]))
    assert render_blocks(data)[0].count('26.10.2026')==2


def test_disagreeing_or_overlapping_children_do_not_override_parent():
    doc=document([('Dated: 26.10.2026','key_value_region',[400,10,550,25]),
                  ('Dated:','text',[400,10,450,25]),('27.10.2026','text',[460,10,550,25])])
    assert not any(b['kind']=='alternative_reading' for b in regions(assemble(doc)).values())


def test_cross_page_clause_and_quote_scopes_preserve_nested_conditions():
    data=assemble(document([b('1.2. Earlier instrument provides as under:'),b('“Subject: Earlier instrument',x=30),b('2. Cases may qualify:',x=30)],
                           [b('i. First condition, and',x=50),b('ii. Second condition, and',x=50),b('iii. Third condition.',x=50),b('4. End of quotation.”',x=30),b('1.3. Explanation:')],
                           [b('Continued explanation outside the quotation.')]))
    rs=regions(data)
    assert rs['d:p1:r1']['quote_id']==rs['d:p2:r2']['quote_id']
    assert 'quote_id' not in rs['d:p3:r0']
    assert rs['d:p3:r0']['parent_unit_ids']==rs['d:p2:r4']['parent_unit_ids']
    edges=data['structural_candidates']['edges']
    parent=rs['d:p1:r2']['id']
    assert sum(e['type']=='list_child_of' and e['to']==parent for e in edges)==3
    md,html=render_blocks(data)
    assert '> i. First condition' not in md  # retains explicit list structure too
    assert '>   - i. First condition' in md
    assert html.count('<blockquote ')==1 and html.count('</blockquote>')==1
    validate_candidates(data)


def test_illustrations_cross_pages_without_confusing_regulatory_table_reference():
    table=dict(id='d:p2:r0:t',page=2,cells=[['GNA'],['Not effective']],cell_spans=[
        dict(row=0,column=0,row_span=1,column_span=1,text='GNA',role_candidate='header'),
        dict(row=1,column=0,row_span=1,column_span=1,text='Not effective')])
    data=assemble(document([b('1.9. Illustrations follow.'),b('Illustration 1:','section_header')],
                           [b('GNA Not effective','table'),b('(a) Suppose commencement is 1.12.2026.'),b('(b) Table 1 of Regulation 13(2)(a) applies.'),b('Further explanation.')],tables=[table]))
    rs=regions(data);edges=data['structural_candidates']['edges']
    assert any(e['type']=='caption_of' and e['from']==rs['d:p1:r1']['id'] and e['to']==rs['d:p2:r0']['id'] for e in edges)
    assert rs['d:p2:r1']['date_mentions'][0]['context']=='hypothetical_assumption'
    assert rs['d:p2:r3']['scenario_id']==rs['d:p2:r2']['scenario_id']
    citations=[n for n in data['structural_candidates']['nodes'] if n['kind']=='external_citation_candidate']
    assert citations[0]['target_status']=='unresolved'
    assert not any(e['type']=='references' and e['to']==rs['d:p2:r0']['id'] for e in edges)
    projected=next(u for u in search_projections(data)['units'] if u['kind']=='table')
    assert 'GNA = Not effective' in projected['text'] and 'Illustration 1' in projected['text']
    assert len(projected['cell_ids'])==2


def test_ambiguous_illustration_tables_remain_explicitly_unresolved():
    tables=[dict(id=f'd:p1:r{i}:t',page=1,cells=[['A']]) for i in (1,2)]
    data=assemble(document([b('Illustration 1:','section_header'),b('A','table'),b('A','table')],tables=tables))
    assert not any(e['type']=='caption_of' for e in data['structural_candidates']['edges'])
    assert data['structural_candidates']['diagnostics'][0]['kind']=='ambiguous_illustration_table'


def test_unscoped_adjacent_pages_are_not_inferred_as_clause_continuations():
    data=assemble(document([b('Standalone text.')],[b('Unrelated standalone text.')]))
    assert not any(e['type']=='continues' for e in data['structural_candidates']['edges'])


def test_numbered_procedural_paragraph_keeps_cross_page_optional_rejoinder():
    data=assemble(document([b('Record of Proceedings','section_header'),b('2. The respondents may reply within two weeks.')],
                           [b('The petitioner may file its rejoinder, if any, within two weeks thereafter.'),b('3. The matter will be listed later.')]))
    rs=regions(data)
    assert rs['d:p1:r1']['parent_unit_ids']==rs['d:p2:r0']['parent_unit_ids']
    assert rs['d:p2:r1']['parent_unit_ids']!=rs['d:p2:r0']['parent_unit_ids']
    assert any(e['type']=='continues' and e['from']==rs['d:p2:r0']['id'] and e['to']==rs['d:p1:r1']['id'] for e in data['structural_candidates']['edges'])


def test_changed_body_and_fabricated_relationship_evidence_fail():
    doc=document([b('1.7. Exact source.')],[b('Continued source.')]);doc['pages'][0]['blocks'][0]['text']='Changed'
    with pytest.raises(ValueError,match='saved body'):assemble(doc)
    data=assemble(document([b('1.7. Exact source.')],[b('Continued source.')]))
    data['structural_candidates']['edges'][0]['evidence'][0]['exact_quote']='Invented'
    with pytest.raises(ValueError,match='exact transcript'):validate_candidates(data)
