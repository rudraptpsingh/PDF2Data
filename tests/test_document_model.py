from copy import deepcopy
from hashlib import sha256
import pytest
from local_vector_batch.document_blocks import build_blocks,validate_blocks
from local_vector_batch.document_model import build_document_model,validate_document_model
from local_vector_batch.legal_l1 import structure_document


def source(lines):
    text='\n'.join(t for t,y,bold in lines)
    page=dict(id='d:p1',page=1,text=text,text_sha256=sha256(text.encode()).hexdigest())
    glyphs=[dict(text=c,bbox=[50+i*4,y,54+i*4,y+10],font='Arial-Bold' if bold else 'Arial',size=10) for t,y,bold in lines for i,c in enumerate(t)]
    data=dict(source_id='d',source_pdf_sha256='a'*64,pages=[page],layout=[dict(page=1,width=600,height=1000,glyphs=glyphs)],
              tables=[],structure=structure_document([page],'a'*64,'explanatory_memorandum'))
    data['document_blocks']=build_blocks(data)
    return data


def test_model_keeps_exact_characters_and_literal_inline_mentions_with_source_anchors():
    data=source([('Under Regulation 13(2)(h), 25% applies to 100 MW.',150,False),('Dated: 26.10.2026',220,False)])
    original=deepcopy(data);model=build_document_model(data)
    mentions=[m for n in model['elements'] for m in n['inline_candidates']]
    assert {'citation','date','quantity'}<={m['kind'] for m in mentions}
    assert all(m['resolved_target'] is None for m in mentions)
    assert all(''.join(s['exact_quote'] for s in m['source_spans'])==m['text'] for m in mentions)
    assert data==original and not model['semantic_sections']
    assert model['section_recovery']=='not_proven_by_pdf_page_breaks'
    validate_document_model(model,data)


def test_title_subtitle_metadata_and_numbered_body_are_distinct():
    data=source([('Supplementary draft notification to the Draft Central Electricity Regulatory Commission',120,True),
                 ('No. L-1/250/2019/CERC Dated: 26.10.2026',200,True),('Explanatory Memorandum',260,True),
                 ('1. Background',300,True),('1.1. The condition applies to the project.',350,False)])
    roles=[n['role_candidate'] for n in build_document_model(data)['elements']]
    assert roles==['title','metadata','subtitle','heading','numbered_paragraph']


def test_observed_paragraph_spacing_splits_display_without_rewriting_legal_units():
    data=source([('First paragraph.',150,False),('Second paragraph.',200,False)])
    assert len(data['document_blocks']['blocks'])==2
    assert [b['spans'][0]['exact_quote'] for b in data['document_blocks']['blocks']]==['First paragraph.\n','Second paragraph.']
    validate_blocks(data['document_blocks'],data['pages'])


def test_date_continuation_is_neither_a_heading_nor_a_new_numbered_paragraph():
    data=source([('1.2. The instrument dated',150,False),('16.04.2026provides as under:',162,False)])
    model=build_document_model(data)
    assert len(model['elements'])==1 and model['elements'][0]['role_candidate']=='numbered_paragraph'
    assert '16.04.2026provides' in model['elements'][0]['text']


def test_formula_is_a_literal_candidate_not_an_invented_editable_equation():
    model=build_document_model(source([('Charge = Rate × Quantity',150,False)]))
    mention=model['elements'][0]['inline_candidates'][0]
    assert mention['kind']=='formula' and mention['text']=='Charge = Rate × Quantity'
    assert 'editable_equation_trees' in model['unsupported_structure']


def test_model_rejects_lost_text_and_forged_run_source_anchors():
    data=source([('A source paragraph.',150,False)]);model=build_document_model(data)
    broken=deepcopy(model);broken['elements'][0]['runs'][0]['text']='changed'
    with pytest.raises(ValueError,match='lost source text'):validate_document_model(broken,data)
    broken=deepcopy(model);broken['elements'][0]['runs'][0]['source_span']['char_start']=99
    with pytest.raises(ValueError,match='anchor changed'):validate_document_model(broken,data)


def test_literal_lists_keep_marker_and_ambiguous_roman_numbering():
    data=source([('(a) First item.',150,False),('(i) Nested item.',180,False),('• Bullet item.',210,False)])
    nodes=build_document_model(data)['elements']
    assert [n['list_type_candidate'] for n in nodes]==['letter','roman_or_letter_ambiguous','bullet']
    assert [n['text'].strip()[0] for n in nodes]==['(','(','•']


def test_lowercase_sentence_ending_in_colon_is_not_a_new_heading():
    data=source([('The proposal applies as',150,False),('follows:',162,False)])
    model=build_document_model(data)
    assert len(model['elements'])==1 and model['elements'][0]['kind']=='paragraph'


def test_dotted_roman_lists_and_reference_context_are_retained():
    data=source([('Reference:',120,True),('(i) OM dated 16.04.2026.',150,False),('(ii) Order 12/2026.',180,False),('i. An ordinary list item.',220,False)])
    nodes=build_document_model(data)['elements']
    assert nodes[1]['list_context_candidate']=='reference_list'
    assert nodes[2]['inline_candidates'][0]['citation_type_candidate']=='order_reference'
    assert nodes[-1]['list_type_candidate']=='roman_or_letter_ambiguous'
