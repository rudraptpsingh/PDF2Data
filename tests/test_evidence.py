import hashlib

import pytest

from local_vector_batch.evidence import check_quote, validate_candidate, validate_candidates


def page(text, number=1):
    return {"page": number, "id": f"doc:p{number}", "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def candidate(quote, **kwargs):
    return {"kind": "number", "value_text": "100", "unit": "MW",
            "evidence": [{"page": 1, "quote": quote}], **kwargs}


def test_exact_unicode_offsets_and_hash():
    p = page("制度 🙂 Capacity 100 MW.")
    quote = "100 MW"
    proof = check_quote(p, {"quote": quote, "char_start": p["text"].index(quote), "char_length": len(quote)})
    assert p["text"][proof["char_start"]:proof["char_start"] + proof["char_length"]] == quote
    assert proof["offset_encoding"] == "codepoint"
    with pytest.raises(ValueError, match="offsets"):
        check_quote(p, {"quote": quote, "char_start": 0, "char_length": len(quote)})
    with pytest.raises(ValueError, match="hash"):
        check_quote(p, {"quote": quote, "page_text_sha256": "wrong"})


def test_paraphrase_missing_page_and_ambiguous_quote_rejected():
    p = page("Capacity 100 MW. Capacity 100 MW.")
    assert not validate_candidate(candidate("The capacity is 100 MW"), [p])["evidence_valid"]
    assert not validate_candidate(candidate("100 MW"), [p])["evidence_valid"]
    assert not validate_candidate(candidate("100 MW", evidence=[{"page": 2, "quote": "100 MW"}]), [p])["evidence_valid"]
    assert check_quote(p, {"quote": "100 MW", "char_start": 9, "char_length": 6})["char_start"] == 9


def test_normalization_is_explicit_and_contiguous():
    p = page("Capacity 100\n MW. Deadline later.")
    with pytest.raises(ValueError):
        check_quote(p, {"quote": "100 MW"})
    proof = check_quote(p, {"quote": "100 MW", "normalization": "whitespace"})
    assert proof["source_quote"] == "100\n MW"
    with pytest.raises(ValueError):
        check_quote(p, {"quote": "Capacity Deadline", "normalization": "whitespace"})


def test_units_values_and_table_associations_are_not_inferred():
    p = page("Capacity 100 MWh. Duration 10 years. 12 years. 25 percent.")
    assert not validate_candidate(candidate("Capacity 100 MWh"), [p])["evidence_valid"]  # MW != MWh
    assert not validate_candidate(candidate("Capacity 100 MWh", value_text="10", unit="MWh"), [p])["evidence_valid"]
    assert not validate_candidate(candidate("25 percent", value_text="25", unit="%"), [p])["evidence_valid"]
    assert not validate_candidate(candidate("Capacity 100 MWh", evidence_layout="table_cells"), [p])["evidence_valid"]
    p_table = page("Capacity MW | 100 | Duration years | 12")
    cells = candidate("100", evidence_layout="table_cells", evidence=[
        {"page": 1, "quote": "100", "cell": "B2"}, {"page": 1, "quote": "Capacity MW", "cell": "B1"}])
    result = validate_candidate(cells, [p_table])
    assert result["status"] == "review_required" and result["verified"] is False
    assert "table_row_column_header_association" in result["review_reasons"]
    assert "unit_not_adjacent_to_value" in result["review_reasons"]
    okay = validate_candidate(candidate("Capacity 100 MWh", unit="MWh"), [p])
    assert okay["evidence_valid"] and not okay["verified"]


def test_dates_and_contact_roles_never_auto_promoted():
    p = page("To Counsel Delhi. Hearing on 28 October 2026. Issued 27 October 2026.")
    date = {"kind": "date", "value_text": "28 October 2026", "date_role": "hearing",
            "evidence": [{"page": 1, "quote": "Hearing on 28 October 2026"}]}
    result = validate_candidate(date, [p])
    assert result["evidence_valid"] and result["status"] == "review_required"
    assert "date_role_precision_and_validity" in result["review_reasons"]
    assert not validate_candidate(dict(date, date_role="deadline_guessed"), [p])["evidence_valid"]
    contact = {"kind": "contact", "contact_role": "issuer", "evidence_basis": "addressee",
               "evidence": [{"page": 1, "quote": "To Counsel Delhi"}]}
    assert not validate_candidate(contact, [p])["evidence_valid"]
    contact["contact_role"] = "counsel"
    assert validate_candidate(contact, [p])["evidence_valid"]
    assert validate_candidates([date, contact], [p])["verified"] == 0
