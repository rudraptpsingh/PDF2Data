from copy import deepcopy

import pytest
from test_document_model import source
from local_vector_batch.document_blocks import build_blocks
from local_vector_batch.document_model import build_document_model,validate_document_model
from local_vector_batch.legal_l1 import structure_document


def bundle(parts):
    pages=[];layouts=[]
    for i,lines in enumerate(parts,1):
        item=source(lines);page=item['pages'][0];page.update(page=i,id=f'd:p{i}')
        item['layout'][0]['page']=i;pages.append(page);layouts.extend(item['layout'])
    result=dict(source_id='d',source_pdf_sha256='a'*64,pages=pages,layout=layouts,tables=[],
                source={'declared':{'family':'explanatory_memorandum'}},
                structure=structure_document(pages,'a'*64,'explanatory_memorandum'))
    result['document_blocks']=build_blocks(result)
    return result


def test_multiple_header_identities_remain_in_one_pdf_with_distinct_source_candidates():
    data=bundle([[('No. X-1/2026 Dated: 26.10.2026',120,False),('1. Body',300,False),('Page 1',950,False)],
                 [('No. X-2/2026 Dated: 27.10.2026',120,False),('Annexure II',220,True),('Page 1',950,False)]])
    before=deepcopy(data);model=build_document_model(data);parts=model['document_parts']
    assert data==before and len(parts['header_clusters'])==2
    assert parts['header_clusters'][1]['relationship_to_previous']=='different_literal_identifier_unresolved_bundle_boundary'
    assert [(p['physical_page'],p['printed_number'])for p in parts['printed_page_labels']]==[(1,1),(2,1)]
    assert parts['explicit_markers'][0]['literal_label']=='II'
    assert all(p['end_boundary']is None and not p['semantic_scope_verified']for p in parts['header_clusters'])
    assert parts['semantic_parts']==[] and not parts['publication_eligible']


def test_repeated_header_is_not_asserted_as_a_new_instrument_and_conflicts_are_retained():
    lines=[('No. X-1/2026 Dated: 26.10.2026',120,False)]
    data=bundle([lines,lines,[('No. X-1/2026 Dated: 27.10.2026',120,False)]])
    data['source']['declared'].update(document_reference='X-1/2026',document_date='2026-10-26')
    parts=build_document_model(data)['document_parts']
    assert [c['relationship_to_previous']for c in parts['header_clusters']]==[
        'first_header_candidate','repeated_identity_candidate','same_identifier_conflicting_or_unknown_date']
    assert len([c for c in parts['catalog_checks']if c['status']=='date_conflict_requires_review'])==1
    assert data['source']['declared']['document_date']=='2026-10-26'


def test_bilingual_annexure_and_language_markers_do_not_claim_translation_equivalence():
    data=bundle([[('अनुलग्नक २',120,True),('हिंदी पाठ',200,True)],
                 [('Annexure 2',120,True),('English version',200,True),('Page iv of 10',950,False)]])
    parts=build_document_model(data)['document_parts']
    assert len(parts['explicit_markers'])==4
    label=parts['printed_page_labels'][0]
    assert label['printed_label']=='iv' and label['printed_number']is None and label['printed_total']==10
    assert not parts['bilingual_text_equivalence_verified']


def test_narrative_reference_and_table_margin_number_do_not_establish_bundle_identity():
    data=bundle([[('See Petition No. 26/MP/2026',120,False),('Dated: 26.10.2026',140,False),('1',950,False)]])
    data['tables']=[dict(id='t',page=1,bbox=[40,940,100,980],cells=[['1']],cell_spans=[])]
    # Build parts directly: this test concerns margin/header selection, not table placement.
    from local_vector_batch.document_parts import build_document_parts
    from local_vector_batch.document_metadata import extract_metadata
    parts=build_document_parts(data,extract_metadata(data),[])
    assert parts['header_clusters']==[] and parts['printed_page_labels']==[]


def test_changed_part_or_printed_page_mapping_is_rejected():
    data=bundle([[('Annexure I',120,True),('Page 9',950,False)]])
    model=build_document_model(data);model['document_parts']['printed_page_labels'][0]['physical_page']=9
    with pytest.raises(ValueError,match='document part'):validate_document_model(model,data)
