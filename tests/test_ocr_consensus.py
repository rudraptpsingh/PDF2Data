import json

from local_vector_batch.ocr_consensus import (_unique_receipt_count, compare_ocr,
                                              recognition_model_for_text, recognition_receipt, _page_batches)


def _compare(a, b, layout=False):
    return compare_ocr(a, b, vision_confidence=.99,
                       independent_confidence=.99, has_table_or_layout_warning=layout)


def test_exact_numeric_sequence_required_even_with_nearly_identical_words():
    text = "The battery charging requirement is 51 percent in each fiscal year and applies for 12 years from the date of commercial operation."
    changed = text.replace("51", "57")
    result = _compare(text, changed)
    assert not result["numeric_exact"]
    assert "numeric_disagreement" in result["review_reasons"]


def test_layout_warning_and_missing_clause_block_candidate():
    text = "Clause 1 requires generation from renewable sources. Clause 2 requires monthly reporting of actual drawal. Clause 3 defines the yearly test and billing."
    assert "table_or_layout_review" in _compare(text, text, True)["review_reasons"]
    assert "text_disagreement" in _compare(text, text.replace("Clause 2 requires monthly reporting of actual drawal.", ""))["review_reasons"]


def test_only_high_agreement_clean_page_is_candidate():
    text = ("The commission may examine every application in the register. The applicant must identify the relevant instrument "
            "and provide supporting documents with dates. Each submission is reviewed by the authority before the stated deadline "
            "and a written decision is published after the hearing is complete.")
    assert _compare(text, text)["consensus_candidate"]
    assert not compare_ocr(text, text, vision_confidence=.98,
                           independent_confidence=.75,
                           has_table_or_layout_warning=False)["consensus_candidate"]


def test_independent_recognizer_routes_dominant_script_and_records_weights():
    assert recognition_model_for_text("THE GAZETTE\nकेन्द्रीय विद्युत आयोग द्वारा जारी नियम") == "devanagari"
    assert recognition_model_for_text("Header हिन्दी\nThe applicant shall file a reply") == "en"
    devanagari = recognition_receipt("devanagari")
    english = recognition_receipt("en")
    assert devanagari["recognition_languages"] == ["hi", "en"]
    assert english["recognition_languages"] == ["en"]
    assert len(devanagari["recognition_model_sha256"]) == 64
    assert len(english["recognition_model_sha256"]) == 64


def test_progress_counts_worker_copy_once_and_ignores_incomplete_line(tmp_path):
    one = {"source_pdf_sha256": "a" * 64, "page": 3,
           "selected_page_text_sha256": "b" * 64}
    two = {"source_pdf_sha256": "a" * 64, "page": 4,
           "selected_page_text_sha256": "c" * 64}
    consolidated = tmp_path / "output.jsonl"
    worker = tmp_path / "worker.jsonl"
    consolidated.write_text(json.dumps(one) + "\n")
    worker.write_text(json.dumps(one) + "\n" + json.dumps(two) + "\n" + '{"source_pdf_sha256":"partial"')
    assert _unique_receipt_count([consolidated, worker]) == 2


def test_small_dynamic_batches_cover_every_physical_page_once():
    item = {"package": "parsed", "source_pdf": "original.pdf", "pages": [999]}
    selected = list(range(1, 28))
    batches = _page_batches(item, selected)
    assert [len(row["pages"]) for row in batches] == [8, 8, 8, 3]
    assert [page for row in batches for page in row["pages"]] == selected
    assert all(row["package"] == "parsed" and row["source_pdf"] == "original.pdf" for row in batches)
    assert item["pages"] == [999]


def test_dynamic_audit_consolidates_all_batches_and_resumes_once(tmp_path, monkeypatch):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    import local_vector_batch.ocr_consensus as audit
    import local_vector_batch.evidence_package as evidence
    package = tmp_path / "package"
    package.mkdir()
    source_sha = "a" * 64
    pages = [{"page": n, "method": "vision", "status": "review_required",
              "text": "source text", "text_sha256": hashlib.sha256(b"source text").hexdigest()}
             for n in range(1, 22)]
    (package / "report.json").write_text(json.dumps({"source_pdf_sha256": source_sha}))
    (package / "pages.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pages))
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"package": str(package), "source_pdf": str(tmp_path / "source.pdf")}) + "\n")
    model_receipt = {"recognition_model": "en_model", "recognition_model_sha256": "f" * 64}
    monkeypatch.setattr(audit, "recognition_receipt", lambda model: model_receipt)
    monkeypatch.setattr(audit, "version", lambda name: "test")
    monkeypatch.setattr(audit, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(evidence, "verify_package", lambda *args, **kwargs: {})
    calls = []
    def partition(job):
        selected = json.loads(audit.Path(job[0]).read_text())["pages"]
        calls.append(selected)
        rows = [{"source_pdf_sha256": source_sha, "page": n,
                 "selected_page_text_sha256": pages[n-1]["text_sha256"], "render_dpi": 180,
                 "independent": {"version": "test", **model_receipt, "text": "source text",
                                 "text_sha256": pages[n-1]["text_sha256"], "mean_confidence": .99},
                 "comparison": {"consensus_candidate": False, "review_reasons": ["too_little_text"]}}
                for n in selected]
        audit.Path(job[1]).write_text("".join(json.dumps(row) + "\n" for row in rows))
        return {"audited_pages": len(rows), "counts": {"review_required": len(rows)}}
    monkeypatch.setattr(audit, "_audit_partition", partition)
    output = tmp_path / "audit.jsonl"
    result = audit.audit_manifest(manifest, output, workers=2, limit=21)
    assert result["workers"] == 2 and result["page_batches"] == 3
    assert result["audited_pages"] == 21 and len(calls) == 3
    assert sorted(row["page"] for row in map(json.loads, output.read_text().splitlines())) == list(range(1, 22))
    assert json.loads(output.with_name(output.name + ".progress.json").read_text())["completed_pages"] == 21
    resumed = audit.audit_manifest(manifest, output, workers=2, limit=21, resume=True)
    assert resumed["resumed_pages"] == 21 and resumed["workers"] == 0
    assert len(calls) == 3
    assert len(output.read_text().splitlines()) == 21


def test_source_reader_receipt_preserves_line_order_and_normalized_geometry():
    from types import SimpleNamespace
    from local_vector_batch.ocr_consensus import _source_lines
    result = SimpleNamespace(boxes=[[[20, 30], [100, 30], [100, 50], [20, 50]],
                                    [[10, 5], [70, 5], [70, 15], [10, 15]]],
                             txts=["Clause 17", "Footnote 1"], scores=[.99, .98])
    rows = _source_lines(result, 200, 100)
    assert [row["text"] for row in rows] == ["Clause 17", "Footnote 1"]
    assert rows[0]["bbox"] == [.1, .3, .5, .5]
    assert rows[1]["bbox"] == [.05, .05, .35, .15]
