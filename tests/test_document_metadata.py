from copy import deepcopy
import pytest
from test_document_model import source
from local_vector_batch.document_metadata import extract_metadata,normalize_date,validate_metadata


def data(lines,family='explanatory_memorandum'):
    d=source(lines);d['source']={'declared':{'family':family}};return d


def test_left_reference_and_right_date_are_typed_and_separately_located():
    d=data([('No. L-1/250/2019/CERC Dated: 26.10.2026',150,True),('1. Background',250,True)])
    result=extract_metadata(d);fields=result['fields']
    assert [(f['role_candidate'],f['value']) for f in fields]==[('document_reference','L-1/250/2019/CERC'),('document_date','26.10.2026')]
    assert fields[0]['geometry']['bbox'][2]<fields[1]['geometry']['bbox'][0]
    assert fields[1]['normalization']['iso']=='2026-10-26'
    assert result['primary_fields']['document_date']['status']=='candidate'
    assert all(not f['role_verified'] for f in fields)


def test_hearing_date_is_not_promoted_to_document_date():
    result=extract_metadata(data([('Petition No. 26/MP/2026',100,False),('Date of Hearing : 30.7.2026',150,False),('Record of Proceedings',300,True)],'record_of_proceedings'))
    assert result['primary_fields']['hearing_date']['status']=='candidate'
    assert result['primary_fields']['document_date']['status']=='not_established'


def test_bilingual_dates_agree_by_value_but_identifier_transliteration_stays_unverified():
    result=extract_metadata(data([('No. L-1/250/2019/CERC Dated: 26 October 2026',100,True),('सं. एल-१/२५०/२०१९/सीईआरसी दिनांक: २६ अक्टूबर २०२६',150,True)]))
    assert len(result['fields'])==4
    dates=[f for f in result['fields'] if f['role_candidate']=='document_date']
    assert {f['normalization']['iso'] for f in dates}=={'2026-10-26'}
    assert result['primary_fields']['document_date']['status']=='candidate'
    assert {r['relation'] for r in result['cross_language_correlations']}=={'same_normalized_date_candidate','shared_identifier_numeric_components_candidate'}
    assert not any(r['text_equivalence_verified'] for r in result['cross_language_correlations'])


def test_numeric_ambiguity_invalid_dates_and_unknown_hindi_month_are_explicit():
    assert normalize_date('03/04/2026')['status']=='ambiguous_numeric_date'
    assert normalize_date('31.02.2026')['status']=='invalid_calendar_date'
    assert normalize_date('२६.१०.२०२६')['iso']=='2026-10-26'
    assert normalize_date('4 फरवरा, 2010')['status']=='unrecognized_month'
    assert normalize_date('4 माघ 1931')['iso'] is None


def test_body_publication_and_as_of_dates_do_not_become_header_issue_dates():
    result=extract_metadata(data([('No. L-1/250/2019/CERC Dated: 26.10.2026',100,True),('The draft was published on 31.07.2026.',200,False),('Eligibility as on 31 March, 2026 remains conditional.',250,False)]))
    publication=next(f for f in result['fields'] if f['role_candidate']=='publication_date')
    assert publication['scope']=='body_mention'
    assert result['primary_fields']['publication_date']['status']=='not_established'
    assert any(f['role_candidate']=='as_of_date' for f in result['fields'])


def test_registration_number_retains_entire_spaced_bilingual_identifier():
    result=extract_metadata(data([('REGD. NO. D. L.-33004/99',100,False),('रजिस्ट्री सं० डी० एल०-33004/99',150,False)]))
    assert [f['value'] for f in result['fields']]==['D. L.-33004/99','डी० एल०-33004/99']


def test_conflicting_header_dates_and_changed_evidence_are_not_silently_fixed():
    d=data([('Dated: 26.10.2026',100,False),('दिनांक: २७ अक्टूबर २०२६',150,False)])
    result=extract_metadata(d)
    assert result['primary_fields']['document_date']['status']=='conflicting_candidates'
    assert not result['cross_language_correlations']
    bad=deepcopy(result);bad['fields'][0]['value_span']['char_start']+=1
    with pytest.raises(ValueError,match='anchor differs'):validate_metadata(bad,d)
    bad=deepcopy(result);bad['fields'][0]['normalization']['iso']='2026-10-27'
    with pytest.raises(ValueError,match='normalization differs'):validate_metadata(bad,d)


def test_reference_does_not_swallow_notification_prose_after_printed_delimiter():
    result=extract_metadata(data([('No. L-7/139(159)/2008-CERC.-The Commission orders as follows.',100,False)]))
    assert result['fields'][0]['value']=='L-7/139(159)/2008-CERC'


def test_metadata_overlay_splits_the_combined_row_without_changing_source():
    from local_vector_batch.document_model import build_document_model
    from local_vector_batch.layout_overlay import build_overlays
    d=data([('No. L-1/250/2019/CERC Dated: 26.10.2026',100,True)])
    original=deepcopy(d['pages']);d['document_model']=build_document_model(d)
    overlays=build_overlays(d)['pages'][0]['regions']
    fields=[r for r in overlays if r.get('field_role')]
    assert {r['field_role'] for r in fields}=={'document_reference','document_date'}
    assert fields[0]['bbox'][2]<fields[1]['bbox'][0]
    assert len([r for r in overlays if r['type']=='metadata'])==2
    assert d['pages']==original
