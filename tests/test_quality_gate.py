import hashlib
import json

from local_vector_batch.quality_gate import assess_page, load_policy, apply_machine_quality_decisions


def _page(text="At least 51% and 12 years.", *, method="pdf_text", script_languages=None):
    page = {"page": 3, "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "method": method, "selected_quality": {"needs_ocr": False}}
    if script_languages is not None:
        page["ocr"] = {"engine": method, "recognition_languages": script_languages}
    return page


GEOMETRY = {"ready": True, "ink_pixels": 300, "unaccounted_ink_pixels": 0,
            "nontext_objects": {"images": 0, "curves": 0, "rects": 0, "lines": 0}}


def _geometry(page):
    return {**GEOMETRY, "source_pdf_sha256": "a"*64, "page": page["page"],
            "page_text_sha256": page["text_sha256"]}


def _assess(page, **kwargs):
    return assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                       independent_text=page["text"], source_geometry=_geometry(page), **kwargs)


def test_native_source_coverage_and_independent_numbers_can_be_auto_accepted():
    receipt = _assess(_page())
    assert receipt["outcome"] == "auto_accepted"
    assert receipt["acceptance_scope"] == "machine_verified_extraction"
    assert all(component["state"] == "pass" for component in receipt["components"].values())


def test_native_objects_are_allowed_only_with_full_source_raster_proof():
    page = _page("The applicable charge is 51%.")
    geometry = {**_geometry(page), "method": "native_glyph_raster_coverage",
                "nontext_objects": {"images": 1, "curves": 0, "rects": 2, "lines": 0},
                "page_complete": True, "reading_order_proven": True,
                "independent_word_order_agrees": True, "independent_number_order_agrees": True,
                "unresolved_visual_regions": [], "rendered_image_sha256": "b"*64}
    accepted = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                           independent_text=page["text"], source_geometry=geometry)
    assert accepted["outcome"] == "auto_accepted"
    assert accepted["components"]["source_render"]["state"] == "pass"
    for missing in ("rendered_image_sha256", "reading_order_proven", "page_complete"):
        incomplete = {key: value for key, value in geometry.items() if key != missing}
        blocked = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                              independent_text=page["text"], source_geometry=incomplete)
        assert blocked["outcome"] == "repair_required"
        assert blocked["components"]["source_render"]["state"] == "fail"


def test_high_word_agreement_cannot_override_missing_layout_or_numeric_failure():
    page = _page("The rate is 51% and 12 years.")
    no_layout = _assess(page, table_count=1)
    assert no_layout["outcome"] == "repair_required"
    assert "layout" in no_layout["missing_evidence"]
    incorrect = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                            independent_text="The rate is 52% and 12 years.",
                            source_geometry=_geometry(page), table_count=1,
                            layout_proof={"source_pdf_sha256": "a"*64, "physical_page": 3,
                                          "page_text_sha256": page["text_sha256"],
                                          "row_column_proven": True, "numeric_values_exact": True,
                                          "source_render_coverage": True})
    assert incorrect["outcome"] == "repair_required"
    assert "ordered_numbers" in incorrect["failed_components"]
    assert incorrect["components"]["layout"]["state"] == "unknown"


def test_exact_inequalities_and_negation_are_hard_gates():
    page = _page("The share shall not be <51%.")
    changed_limit = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                                independent_text="The share shall not be >51%.",
                                source_geometry=_geometry(page))
    assert "ordered_operators" in changed_limit["failed_components"]
    changed_negation = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                                   independent_text="The share shall be <51%.",
                                   source_geometry=_geometry(page))
    assert "ordered_operators" in changed_negation["failed_components"]


def test_ocr_agreement_without_rendered_source_coverage_is_held():
    page = _page("The rate is 51% and 12 years.", method="vision", script_languages=["en-IN"])
    receipt = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                          independent_text=page["text"], table_count=0)
    assert receipt["outcome"] == "repair_required"
    assert receipt["missing_evidence"] == ["source_render", "layout", "complete_page_visual_accounting"]
    hindi = _page("कम से कम ५१% और १२ साल", method="rapidocr", script_languages=["zh-CN", "en-US"])
    result = _assess(hindi)
    assert "language" in result["failed_components"]


def test_independent_devanagari_model_must_be_source_and_text_bound():
    page = _page("नियम ५१% लागू है", method="vision", script_languages=[])
    metadata = {"source_pdf_sha256": "a"*64, "page": 3,
                "selected_page_text_sha256": page["text_sha256"],
                "text_sha256": page["text_sha256"], "engine": "rapidocr_onnx",
                "version": "3.10.0", "recognition_model_sha256": "c"*64,
                "recognition_model": "devanagari_PP-OCRv5_rec_mobile",
                "recognition_languages": ["hi"]}
    accepted_language = _assess(page, independent_metadata=metadata)
    assert accepted_language["components"]["language"]["state"] == "pass"
    assert accepted_language["components"]["language"]["independent_model_confirmed"] is True
    for changed in ({"text_sha256": "b"*64}, {"source_pdf_sha256": "b"*64},
                    {"recognition_model_sha256": None}, {"version": None},
                    {"recognition_model": "ch_ppocr_mobile_v2.0_cls_mobile"}):
        bad = _assess(page, independent_metadata={**metadata, **changed})
        assert bad["components"]["language"]["state"] == "fail"


def test_source_or_page_hash_failure_blocks_before_all_other_gates():
    page = _page()
    assert assess_page(page, source_pdf_sha256="b"*64, package_pdf_sha256="a"*64)["outcome"] == "source_blocked"
    page["text"] += " altered"
    assert assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64)["outcome"] == "source_blocked"


def test_policy_is_packaged_and_has_separate_hard_gates():
    policy = load_policy()
    assert policy["require_exact_ordered_numbers"] is True
    assert policy["require_row_column_proof_for_tables"] is True


def test_machine_decision_requires_complete_source_bound_proof(tmp_path):
    page = _page()
    page["status"] = "review_required"
    import local_vector_batch.quality_gate as gate
    policy_sha = hashlib.sha256(json.dumps(load_policy(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    row = _assess(page)
    row.update(policy_sha256=policy_sha, source_url="https://example.org/source.pdf",
               independent_text_sha256=hashlib.sha256(page["text"].encode()).hexdigest(),
               page_complete=True, unresolved_visual_regions=[])
    row["components"]["source_render"].update(receipt_sha256="b"*64, rendered_image_sha256="c"*64)
    receipt = {"schema_version": 1, "source_verified": True, "source_pdf_sha256": "a"*64,
               "source_url": "https://example.org/source.pdf", "policy_sha256": policy_sha,
               "human_review_receipts_created": 0, "results": [row]}
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(receipt))
    pages, count = apply_machine_quality_decisions([page], "a"*64, path,
                                                    source_url="https://example.org/source.pdf")
    assert count == 1 and pages[0]["status"] == "ready" and pages[0]["method"] == "pdf_text"
    assert page["status"] == "review_required"
    repaired_native = {**page, "status": "ready"}
    proven, native_count = apply_machine_quality_decisions([repaired_native], "a"*64, path,
                                                           source_url="https://example.org/source.pdf")
    assert native_count == 1 and proven[0]["machine_quality_accepted"] is True
    assert proven[0]["method"] == "pdf_text"
    assert "machine_quality_accepted" not in repaired_native
    for reviewed_status, decision in (("ready", "replace_text"), ("blank", "blank_page"),
                                      ("ready", "approve_text"),
                                      ("review_required", "keep_held")):
        reviewed = {**page, "status": reviewed_status, "review_decision": decision}
        unchanged, reviewed_count = apply_machine_quality_decisions([reviewed], "a"*64, path,
                                                                     source_url="https://example.org/source.pdf")
        assert reviewed_count == 0 and unchanged[0]["status"] == reviewed_status
    receipt["results"][0]["page_complete"] = False
    path.write_text(json.dumps(receipt))
    import pytest
    with pytest.raises(ValueError, match="incomplete machine quality proof"):
        apply_machine_quality_decisions([page], "a"*64, path,
                                        source_url="https://example.org/source.pdf")
