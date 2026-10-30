import hashlib
import json

from local_vector_batch.structure_candidates import page_candidates, table_candidates
from local_vector_batch.evidence import check_quote


def page(text, method="pdf_text"):
    return {"id": "d:p1", "page": 1, "text": text, "method": method,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def test_date_ocr_confusion_is_a_review_only_literal_not_a_silent_correction():
    source = page('Dated 3O.12.2O16. Hearing 1O October 2O26; ISO 2O26-10-29.', 'vision')
    rows = [r for r in page_candidates(source) if r['kind'] == 'date_ocr_anomaly']
    assert [r['evidence'][0]['quote'] for r in rows] == ['3O.12.2O16', '1O October 2O26', '2O26-10-29']
    assert all(r['status'] == 'review_required' and 'normalized_date' not in r for r in rows)
    for row in rows:
        assert check_quote(source, row['evidence'][0])['source_quote'] == row['evidence'][0]['quote']
        for char in row['uncertain_characters']:
            assert check_quote(source, char['evidence'])['source_quote'] == char['observed']
            assert char['possible_digit'] in '01'


def test_valid_dates_named_months_and_invalid_or_unrelated_tokens_are_not_flagged():
    from local_vector_batch.structure_candidates import date_ocr_candidates
    source = page('9th December, 2016; 30.12.2016; 2026-10-29; 32.O1.2O26; Section IO/2026.')
    assert date_ocr_candidates(source) == []


def test_date_ocr_review_can_be_disabled_or_tuned_in_global_configuration():
    from local_vector_batch.structure_candidates import date_ocr_candidates
    import pytest
    source = page('Dated 3O.12.2O16 and 1l October 2026.', 'vision')
    assert date_ocr_candidates(source, {'ocr_date_review': {'enabled': False}}) == []
    limited = {'ocr_date_review': {'enabled': True, 'confusable_digits': {'O': '0'}}}
    rows = date_ocr_candidates(source, limited)
    assert [r['evidence'][0]['quote'] for r in rows] == ['3O.12.2O16']
    ancient = page('Dated 1O October 18O1.', 'vision')
    assert len(date_ocr_candidates(ancient)) == 1
    constrained = {'ocr_date_review': {'enabled': True, 'confusable_digits': {'O': '0'},
                                        'min_year': 1900, 'max_year': 2099}}
    assert date_ocr_candidates(ancient, constrained) == []
    with pytest.raises(ValueError, match='year range'):
        date_ocr_candidates(source, {'ocr_date_review': {'enabled': True, 'confusable_digits': {'O': '0'},
                                                           'min_year': 2027, 'max_year': 2026}})


def test_role_cues_keep_issuer_separate_from_addressee_and_all_spans_validate():
    source = page("CENTRAL ELECTRICITY REGULATORY COMMISSION\n"
                  "6t, 71 & 8\" Floor, New Delhi\nPhone: 011-26189709\n"
                  "To,\nShri Nihal Bhardwaj\nAdvocate for Petitioner\n"
                  "Subject:\nRequest for extension\n", "vision")
    rows = page_candidates(source)
    roles = {row["proposed_role"]: row for row in rows if row["kind"] == "party_role"}
    assert {"issuer_masthead", "masthead_address", "masthead_contact", "addressee"} <= roles.keys()
    assert "Nihal" in roles["addressee"]["evidence"][0]["quote"]
    assert "Nihal" not in roles["issuer_masthead"]["evidence"][0]["quote"]
    for row in rows:
        for item in row["evidence"]:
            assert check_quote(source, item)["source_quote"] == item["quote"]
        assert row["status"] == "review_required"


def test_operator_conjunction_subject_and_temporal_cues_have_exact_spans():
    source = page("Generating Station standby meters on Generator Transformers and all Station Auxiliary Transformers.\n"
                  "For FY 2023-24, requirement is the reference contingency or computed reserve, whichever is higher.\n")
    rows = [r for r in page_candidates(source) if r["kind"] == "condition_cues"]
    assert any(c["quote"].lower() == "and" for r in rows for c in r["conjunction_cues"])
    assert any(c["quote"] == "FY 2023-24" for r in rows for c in r["temporal_scope_cues"])
    assert any(c["quote"].lower() == "whichever is higher" for r in rows for c in r["operator_cues"])
    assert any(c["quote"] == "Station Auxiliary Transformers" for r in rows for c in r["subject_cues"])
    for row in rows:
        for key in ("evidence", "operator_cues", "conjunction_cues", "subject_cues", "temporal_scope_cues"):
            for item in row[key]:
                assert check_quote(source, item)["source_quote"] == item["quote"]


def test_table_blank_cells_are_never_filled_and_ambiguous_spans_are_explicit():
    source = page("Category Rate\nBESS 100%\nBESS 75%")
    table = {"id": "d:p1:table1", "bbox": [0, 0, 100, 100],
             "cells": [["Category", "Rate"], ["BESS", "100%"], [None, "75%"]]}
    rows = table_candidates(table, source)
    assert not any(row["row"] == 2 and row["column"] == 0 for row in rows)
    repeated = next(row for row in rows if row["row"] == 1 and row["column"] == 0)
    assert repeated["span_status"] == "exact_unique_row_context"
    assert repeated["evidence"][0]["quote"] == "BESS"
    assert all(row["status"] == "review_required" for row in rows)
    for row in rows:
        for item in row["evidence"]:
            assert check_quote(source, item)["source_quote"] == item["quote"]


def test_repeated_table_rows_do_not_invent_a_unique_cell_span():
    source = page("Category Rate\nBESS 100%\nBESS 100%")
    table = {"id": "d:p1:table1", "bbox": [0, 0, 100, 100],
             "cells": [["Category", "Rate"], ["BESS", "100%"], ["BESS", "100%"]]}
    rows = table_candidates(table, source)
    repeated = [row for row in rows if row["row"] > 0]
    assert all(row["span_status"] == "ambiguous_or_not_in_selected_text" for row in repeated)
    assert all(row["evidence"] == [] for row in repeated)


def test_indian_order_profiles_keep_hearing_and_decision_labels_separate():
    source = page("BEFORE THE CENTRAL ELECTRICITY REGULATORY COMMISSION\nPetition No. 17/MP/2024\n"
                  "CORAM: Chairperson\nDate of Hearing: 10.02.2024\nDate of Order: 20.03.2024\nORDER\n")
    rows = [r for r in page_candidates(source) if r['kind'] == 'format_field']
    fields = {r['proposed_field']: r for r in rows}
    assert '10.02.2024' in fields['hearing_date_label']['evidence'][0]['quote']
    assert '20.03.2024' in fields['decision_date_label']['evidence'][0]['quote']
    assert not any(r.get('proposed_field') == 'effective_date' for r in rows)
    for row in rows:
        assert row['status'] == 'review_required' and len(row['profile_config_sha256']) == 64
        for evidence in row['evidence']:
            assert check_quote(source, evidence)['source_quote'] == evidence['quote']


def test_order_party_and_direction_are_candidates_not_verified_obligations():
    source = page('BEFORE THE CENTRAL ELECTRICITY REGULATORY COMMISSION\n'
                  'Petition No. 17/MP/2024\nABC Power Limited    Petitioner\n'
                  'XYZ Transmission Limited    Respondent\nORDER\n'
                  'The Commission directs the respondent to file its reply within two weeks.\n')
    fields = {r['proposed_field']: r for r in page_candidates(source) if r['kind'] == 'format_field'}
    assert {'party_role_label', 'operative_direction_label'} <= fields.keys()
    assert 'respondent to file its reply' in fields['operative_direction_label']['evidence'][0]['quote']
    assert all(row['status'] == 'review_required' for row in fields.values())
    assert not any(row.get('deadline_days') for row in fields.values())


def test_corrigendum_old_new_heading_does_not_promote_a_revised_deadline():
    source = page('CORRIGENDUM-III\nRfS No: SECI/C&P/2026/14\n'
                  'Existing Date | Revised Date\n31 October 2026 | 15 November 2026\n')
    fields = {r['proposed_field']: r for r in page_candidates(source) if r['kind'] == 'format_field'}
    assert 'old_new_table_heading' in fields
    assert fields['old_new_table_heading']['status'] == 'review_required'
    assert 'submission_deadline_label' not in fields


def test_tender_and_gazette_profiles_do_not_assume_deadlines_or_amendment_effect():
    tender = page("CORRIGENDUM III\nRfS No: SECI/TEST/2026\n"
                  "Last date for bid submission: 31.10.2026 15:00 IST\n"
                  "EMD: INR 10 lakh per MW\nPre-bid meeting: 01.10.2026\n")
    fields = {r['proposed_field']: r for r in page_candidates(tender) if r['kind'] == 'format_field'}
    assert {'corrigendum_heading', 'tender_reference', 'submission_deadline_label',
            'bid_security_label', 'prebid_meeting_label'} <= fields.keys()
    assert not any('superseded' in r for r in fields.values())
    gazette = page("भारत का राजपत्र\nअधिसूचना\nG.S.R. 186(E).\n"
                   "These rules shall come into force on publication.\n")
    rows = page_candidates(gazette)
    assert any(r.get('proposed_field') == 'commencement_clause' for r in rows)
    assert all(r['status'] == 'review_required' for r in rows)
    assert not any('2026' in r['evidence'][0]['quote'] for r in rows)


def test_mentions_in_ordinary_prose_do_not_choose_a_document_format():
    from local_vector_batch.structure_candidates import format_candidates
    assert format_candidates(page('We discuss regulations, a tender and the order of operations.')) == []


def test_corrigendum_is_not_automatically_a_tender_and_external_dates_remain_unresolved():
    from local_vector_batch.structure_candidates import format_candidates
    order = page('CORRIGENDUM\nDate of Order: 20.03.2024\nCORAM: Chairperson\nPetition No. 425/TL/2019\n')
    profiles = {r['profile_id'] for r in format_candidates(order) if r['kind'] == 'document_format'}
    assert 'amendment_notice' in profiles and 'judgment_or_order' in profiles
    assert 'tender' not in profiles
    tender = page('BID INFORMATION SHEET\nBid submission deadline: as per NIT\nRfS No: [Insert reference]\n')
    fields = {r['proposed_field']: r for r in format_candidates(tender) if r['kind'] == 'format_field'}
    assert fields['submission_deadline_label']['value_state'] == 'external_reference'
    assert fields['tender_reference']['value_state'] == 'template_placeholder'
    assert not any('parsed_date' in row for row in fields.values())


def test_real_letter_aliases_and_non_gazette_regulation_heading_are_configured():
    from local_vector_batch.structure_candidates import format_candidates
    letter = page('Ref. No.: CTUIL/LEGAL/2026/1\nSub: Implementation of connectivity conditions\n')
    fields = {r['proposed_field'] for r in format_candidates(letter) if r['kind'] == 'format_field'}
    assert {'reference_number', 'subject'} <= fields
    regulation = page('No. L-7/25(5)/2003-CERC\n1. Short title and commencement\n')
    rows = format_candidates(regulation)
    assert any(r.get('profile_id') == 'gazette_or_regulation' and r['kind'] == 'document_format' for r in rows)
    assert any(r.get('proposed_field') == 'instrument_reference' for r in rows)


def test_indian_regulation_masthead_keeps_inline_printed_date_separate():
    from local_vector_batch.structure_candidates import format_candidates
    from local_vector_batch.evidence import check_quote
    source = page('CENTRAL ELECTRICITY REGULATORY COMMISSION\n'
                  'No. L-1/270/2023/CERC                    Date: 23rd May 2024\n'
                  'NOTIFICATION\n1. Short Title and Commencement\n'
                  'These regulations shall come into force on publication in the Official Gazette.\n')
    rows = format_candidates(source)
    fields = {r['proposed_field']: r for r in rows if r['kind'] == 'format_field'}
    assert fields['printed_date_label']['evidence'][0]['quote'].strip() == 'Date: 23rd May 2024'
    assert fields['instrument_reference']['evidence'][0]['quote'].strip() == 'No. L-1/270/2023/CERC'
    assert fields['commencement_clause']['evidence'][0]['quote'].startswith('These regulations')
    for row in rows:
        for evidence in row['evidence']:
            assert check_quote(source, evidence)['source_quote'] == evidence['quote']
        assert row['status'] == 'review_required'


def test_tender_and_corrigendum_dates_are_document_candidates_not_bid_deadlines():
    from local_vector_batch.structure_candidates import format_candidates
    tender = page('REQUEST FOR SELECTION\nRfS No: SECI/C&P/2026/14    Dated 12 October 2026\n'
                  'Bid submission deadline: 31 October 2026\n')
    fields = {r['proposed_field']: r['evidence'][0]['quote'].strip()
              for r in format_candidates(tender) if r['kind'] == 'format_field'}
    assert fields['printed_date_label'] == 'Dated 12 October 2026'
    assert fields['submission_deadline_label'] == 'Bid submission deadline: 31 October 2026'
    amendment = page('CORRIGENDUM-III\nDate: 20 October 2026\n'
                     + ('Existing date and revised date in the enclosed schedule.\n' * 100)
                     + 'Date: 31 October 2026\n')
    dates = [r for r in format_candidates(amendment)
             if r['kind'] == 'format_field' and r['proposed_field'] == 'printed_date_label']
    assert [r['evidence'][0]['quote'].strip() for r in dates] == ['Date: 20 October 2026']


def test_configured_sub_alias_is_used_by_existing_addressee_extraction():
    source = page('Ref. No.: CTUIL/LEGAL/2026/1\nTo,\nThe Applicant\nSub: Connectivity conditions\n')
    roles = [r for r in page_candidates(source) if r['kind'] == 'party_role' and r['proposed_role'] == 'addressee']
    assert len(roles) == 1 and roles[0]['evidence'][0]['quote'] == 'The Applicant'


def test_indian_money_span_preserves_scale_and_denominator_without_computing():
    from local_vector_batch.pdf_ingest import FACT
    values = ['INR 10 lakh per MW per Project', '₹ 1.25 crore/MW',
              'Rs. 50,000 per month', 'INR10LakhperMWperProject']
    for literal in values:
        assert next(FACT.finditer(literal)).group() == literal
    assert not list(FACT.finditer('₴ 10 lakh'))  # No currency guess from country/issuer.
