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
