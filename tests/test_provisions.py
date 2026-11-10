import copy
import pytest
from local_vector_batch.provisions import sha, provision_candidates, select_provision, validate_wrapper, provision_children, embedding_inputs


def pages():
    return [{"page": i+1, "text": text, "text_sha256": sha(text)} for i, text in enumerate([
        "😀 Preamble\n1. New clauses shall be inserted:\nDuty subject to",
        "conditions. Provided that copies must be filed;\n",
        "unless terminated.\n2. Separate instruction.\n"]) ]


def test_cross_page_candidate_keeps_clause_one_and_provisos():
    source = pages()
    output = provision_candidates(source, "a"*64)
    first = output["provisions"][0]
    assert first["label"] == "1"
    assert [s["page"] for s in first["spans"]] == [1, 2, 3]
    assert "unless terminated" in first["spans"][-1]["exact_quote"]
    assert first["boundary_state"] == "candidate"
    assert first["semantic_state"] == "unresolved"
    assert first["spans"][0]["char_start"] == 11  # codepoints, including emoji


def test_review_is_explicit_and_children_keep_short_units():
    source = [{"page": 1, "text": "😀 Duty.", "text_sha256": sha("😀 Duty.")}]
    wrapper = select_provision("p", "1", source, "a"*64,
        [{"page": 1, "char_start": 2, "char_length": 5}], review_receipt_id="local-check")
    child, = provision_children(wrapper, source)
    assert child["text"] == "Duty."
    assert child["char_start"] == 2
    assert child["parent_provision_version_id"] == "p"
    assert child["embedding_status"] == "input_only"
    assert wrapper["semantic_state"] == "unresolved"
    from local_vector_batch.chunking import exact_passage
    record, = embedding_inputs(wrapper, source)
    passage = exact_passage(record['id'], record['text'], record['metadata'], 'test-model', record['chunk_index'], record['char_start'])
    assert passage.metadata['parent_provision_version_id'] == 'p'
    assert passage.start == 2 and passage.text == 'Duty.'
    stale = copy.deepcopy(source)
    stale[0]["text"] = "changed"
    with pytest.raises(ValueError):
        provision_children(wrapper, stale)
    with pytest.raises(ValueError):
        validate_wrapper(wrapper, source, "b"*64)


def test_reject_overlap_or_unsupported_complete_boundary():
    source = pages()
    wrapper = provision_candidates(source, "a"*64)["provisions"][0]
    wrapper["spans"].append(copy.deepcopy(wrapper["spans"][0]))
    with pytest.raises(ValueError):
        validate_wrapper(wrapper, source, "a"*64)
    wrapper = provision_candidates(source, "a"*64)["provisions"][0]
    wrapper["boundary_state"] = "complete"
    with pytest.raises(ValueError):
        validate_wrapper(wrapper, source, "a"*64)


def _unit(text, pdf="a"*64):
    source = [{"page": 1, "text": text, "text_sha256": sha(text)}]
    wrapper = select_provision("p", "1", source, pdf, [{"page": 1, "char_start": 0, "char_length": len(text)}], review_receipt_id="local-check")
    return wrapper, source


def test_fixed_children_are_unchanged_and_sentence_children_end_at_sentences():
    text = ("21. The Bid Evaluation Committee evaluated the five bids received. " * 3
            + "The Commission adopted the tariff of Rs. 4.89/kWh for Example Renewables Pvt. Ltd. under No. 12 of the order. "
            + "The petition is disposed of accordingly and the next date is 3.9.2026. ") * 4
    wrapper, source = _unit(text)
    fixed = provision_children(wrapper, source, width=300)
    assert [c["char_length"] for c in fixed[:-1]] == [300] * (len(fixed) - 1)  # the old behaviour, exactly
    sentence = provision_children(wrapper, source, width=300, cut="sentence")
    assert "".join(c["text"] for c in sentence) == text  # exact, contiguous, no overlap
    assert all(c["char_length"] <= 300 for c in sentence)
    for c in sentence[:-1]:
        assert c["text"].rstrip().endswith((".", ";", ":")), c["text"][-40:]
        assert not c["text"].rstrip().endswith((" No.", " Rs.", " Pvt.", " Ltd.", "21.")), c["text"][-40:]
        assert source[0]["text"][c["char_start"]:c["char_start"] + c["char_length"]] == c["text"]


def test_sentence_cut_falls_back_to_line_break_space_and_width():
    wrapper, source = _unit("word " * 120)  # no sentence end at all
    children = provision_children(wrapper, source, width=100, cut="sentence")
    assert all(c["text"].endswith(" ") for c in children[:-1])
    wrapper, source = _unit("x" * 250)  # no break at all
    assert [c["char_length"] for c in provision_children(wrapper, source, width=100, cut="sentence")] == [100, 100, 50]
    with pytest.raises(ValueError):
        provision_children(wrapper, source, cut="paragraph")


def test_structure_records_its_cut_and_old_packages_verify_as_fixed():
    from local_vector_batch import legal_l1
    text = ("1. The Commission heard the parties at length on the petition. " * 40) + "\n2. Ordered accordingly.\n"
    source = [{"page": 1, "text": text, "text_sha256": sha(text)}]
    structure = legal_l1.structure_document(source, "a" * 64, "generic")
    assert structure["child_cut"] == "sentence"
    legal_l1.validate_structure(structure, source, "a" * 64)
    old = copy.deepcopy(structure)
    del old["child_cut"]
    old["children"] = [c for u in old["units"] for c in provision_children(u, source, width=old["child_width"])]
    legal_l1.validate_structure(old, source, "a" * 64)  # a package built before child_cut existed
    old["children"] = structure["children"]
    with pytest.raises(ValueError):
        legal_l1.validate_structure(old, source, "a" * 64)  # sentence children are not accepted as fixed ones
