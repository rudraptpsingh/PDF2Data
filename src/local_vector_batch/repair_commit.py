"""Atomically merge verified page repairs without replacing unrelated PDF pages."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from .evidence_package import create_package, verify_package
from .pdf_repair import _page_rows, _rows
from .quality_gate import assess_package, apply_machine_quality_decisions, write_quality_report
from .repair_pipeline import _bound_independent_text, reader_provenance


ARTIFACTS = ("pages.jsonl", "embedding-input.jsonl", "fact-candidates.jsonl",
             "structure-candidates.jsonl", "tables.json")


def merge_page_rows(current: dict, replacements: dict) -> list[dict]:
    """Replace selected physical pages; retain every other page's records."""
    merged = {**current, **replacements}
    return [row for page in sorted(merged) for row in merged[page]]


def _check_original(package: Path, source: Path, jobs: list[dict], review_sidecar: Path | None):
    verify_package(package, source_pdf=source)
    current = {row["page"]: row for row in _rows(package / "pages.jsonl")}
    decisions = _rows(review_sidecar) if review_sidecar and review_sidecar.is_file() else []
    for job in jobs:
        page = current.get(job["page"], {})
        valid_versions = {value for value in (job["original_text_sha256"], job.get("candidate_text_sha256")) if value}
        if not page or page.get("text_sha256") not in valid_versions or page.get("review_decision"):
            raise ValueError(f"repair compare-and-swap failed for physical page {job['page']}")
        if any(row.get("page") == job["page"] and row.get("source_pdf_sha256") == job["source_pdf_sha256"]
               for row in decisions):
            raise ValueError("source-reviewed decision takes precedence over staged repair")
    return current


def commit_page_repairs(package: Path, source: Path, jobs: list[dict], *, quality_output: Path,
                        independent_cache: Path, review_sidecar: Path | None = None) -> dict:
    """Freshly verify and merge one PDF's staged OCR repairs under a source lock.

    Failed proof or stale source leaves the watched package unchanged. Cloud
    publication and index readback remain the corpus adapter's next stages.
    """
    from filelock import FileLock
    package, source, quality_output = Path(package), Path(source), Path(quality_output)
    if (not jobs or len({job["page"] for job in jobs}) != len(jobs) or
            any(job.get("status") != "ready_for_publication" for job in jobs)):
        raise ValueError("commit requires distinct source-gated ready page jobs")
    if quality_output.resolve().is_relative_to(package.resolve()):
        raise ValueError("quality receipt must be outside the extraction package")
    cached = {(row.get("source_pdf_sha256"), row.get("page"), row.get("selected_page_text_sha256")): row
              for row in _rows(Path(independent_cache))}
    with FileLock(str(package) + ".commit.lock", timeout=0):
        original = _check_original(package, source, jobs, review_sidecar)
        prior_report = json.loads((package / "report.json").read_text())
        source_sha = prior_report["source_pdf_sha256"]
        replacements = defaultdict(dict)
        corrected_results, corrected_issues = [], []
        fresh_template = None
        for job in jobs:
            if job["source_pdf_sha256"] != source_sha:
                raise ValueError("repair source PDF differs from watched package")
            candidate = Path(job["candidate_package"])
            verify_package(candidate, source_pdf=source)
            candidate_pages = _rows(candidate / "pages.jsonl")
            candidate_page = next(row for row in candidate_pages if row["page"] == job["page"])
            if candidate_page["text_sha256"] != job["candidate_text_sha256"]:
                raise ValueError("staged candidate text changed")
            visual_region = job.get("visual_region")
            if visual_region is not None and (not job.get("visual_layout_python") or
                    visual_region.get("source_pdf_sha256") != source_sha or
                    visual_region.get("physical_page") != job["page"] or
                    visual_region.get("page_text_sha256") != original[job["page"]]["text_sha256"] or
                    visual_region.get("kind") != "picture"):
                raise ValueError("stale visual region source proof")
            if job.get("line_correction_receipt"):
                from .line_repair import recheck_line_correction
                from .region_ocr import require_composite_word_agreement
                proof = json.loads(Path(job["line_correction_receipt"]).read_text())
                original_page = original[job["page"]]
                if (proof["source_pdf_sha256"] != source_sha or
                        proof["physical_page"] != job["page"] or
                        proof["original_page_text_sha256"] != original_page["text_sha256"] or
                        job["original_text_sha256"] != original_page["text_sha256"]):
                    raise ValueError("source line proof no longer describes the watched page")
                independent_text = recheck_line_correction(package, source, proof)
                attestation_receipt = proof["full_independent_receipt"]
                expected_lines = [dict(line) for line in original_page["ocr"]["lines"]]
                if expected_lines[proof["line_index"]]["text"] != proof["old_line"]:
                    raise ValueError("source line changed before commit")
                expected_lines[proof["line_index"]]["text"] = proof["proposed_line"]
                expected_text = "\n".join(line["text"] for line in expected_lines)
                if (candidate_page["text"] != expected_text or
                        candidate_page["ocr"]["lines"] != expected_lines):
                    raise ValueError("staged page differs beyond the source-proven line")
                require_composite_word_agreement(candidate_page["text"], independent_text)
            elif job.get("footnote_receipts") or job.get("line_word_proofs"):
                # A paired source-crop correction cannot use the uncorrected
                # whole-page RapidOCR text as independent ground truth. Re-run
                # each crop against the immutable PDF and compose only the
                # proven markers before checking the entire staged page.
                from .footnote_repair import recheck_footnote_receipts
                from .region_ocr import (corrected_footnote_text, source_bound_line_words,
                                         source_bound_complete_line, compose_complete_line_proof,
                                         compose_source_bound_word_line)
                receipts = [json.loads(Path(path).read_text()) for path in job.get("footnote_receipts", [])]
                original_page = original[job["page"]]
                if receipts:
                    recheck_footnote_receipts(package, source, job["page"], receipts)
                independent_receipt = (receipts[0]["independent_full_receipt"] if receipts else
                                       job.get("independent_full_receipt"))
                if not isinstance(independent_receipt, dict):
                    raise ValueError("source-bound independent OCR receipt missing")
                attestation_receipt = independent_receipt
                baseline = _bound_independent_text(independent_receipt, source,
                    source_sha=source_sha, page=job["page"],
                    original_text_sha=job["original_text_sha256"],
                    candidate_engine=original_page.get("ocr", {}).get("engine", ""))
                corrected_lines, independent_text = (corrected_footnote_text(
                    original_page, baseline, receipts) if receipts else
                    ([dict(line) for line in original_page["ocr"]["lines"]], baseline))
                line_proofs = [json.loads(Path(path).read_text())
                               for path in job.get("line_word_proofs", [])]
                touched = {index for receipt in receipts for index in
                           (receipt["body_line_index"], receipt["note_line_index"])}
                if len({proof.get("line_index") for proof in line_proofs}) != len(line_proofs):
                    raise ValueError("duplicate source word proof")
                for proof in line_proofs:
                    index = proof.get("line_index")
                    if (proof.get("kind") not in {"source_bound_line_words", "source_bound_complete_line"} or
                            proof.get("publication_allowed") is not False or
                            proof.get("source_pdf_sha256") != source_sha or
                            proof.get("original_page_text_sha256") != original_page["text_sha256"] or
                            index in touched or not isinstance(index, int)):
                        raise ValueError("stale or overlapping grouped source word proof")
                    fresh_proof = (source_bound_line_words(original_page, source, index,
                        baseline, dpi=proof["crop_dpi"],
                        horizontal_padding=proof.get("crop_horizontal_padding", .005))
                        if proof["kind"] == "source_bound_line_words" else
                        source_bound_complete_line(original_page, source, index,
                            baseline, dpi=proof["crop_dpi"],
                            vertical_padding=proof.get("crop_vertical_padding", .01),
                            horizontal_padding=proof.get("crop_horizontal_padding", .01)))
                    if fresh_proof != proof:
                        raise ValueError("source word crop proof changed")
                    if proof["kind"] == "source_bound_complete_line":
                        corrected_lines, independent_text = compose_complete_line_proof(
                            corrected_lines, independent_text, proof)
                        touched.add(index)
                        continue
                    corrected_lines[index]["text"] = proof["proposed_line"]
                    independent_text = compose_source_bound_word_line(independent_text, proof)
                    touched.add(index)
                from .region_ocr import require_composite_word_agreement
                require_composite_word_agreement(candidate_page["text"], independent_text)
                if candidate_page["text"] != "\n".join(line["text"] for line in corrected_lines):
                    raise ValueError("staged page differs from source-bound marker composition")
            else:
                independent = cached.get((source_sha, job["page"], job["original_text_sha256"]))
                if independent is None:
                    # A later reader may have audited this exact staged candidate.
                    independent = cached.get((source_sha, job["page"], candidate_page["text_sha256"]))
                if independent is None:
                    raise ValueError("source-bound independent OCR receipt unavailable for commit")
                attestation_receipt = independent
                independent_text = _bound_independent_text(independent, source, source_sha=source_sha,
                    page=job["page"], original_text_sha=independent["selected_page_text_sha256"],
                    candidate_engine=candidate_page.get("ocr", {}).get("engine", ""))
            fresh = assess_package(candidate, source, pages=[job["page"]],
                independent_by_page={job["page"]: independent_text},
                visual_region_by_page=({job["page"]: visual_region} if visual_region else None),
                visual_layout_python=(Path(job["visual_layout_python"])
                    if visual_region is not None else None),
                derive_ocr_source_raster=True)
            fresh["results"][0]["reader_provenance"] = reader_provenance(
                attestation_receipt, source, candidate_page, independent_text)
            if fresh["results"][0]["outcome"] != "auto_accepted":
                raise ValueError("fresh source quality checks rejected staged candidate")
            approved, _ = apply_machine_quality_decisions(candidate_pages, source_sha,
                Path(job["quality_receipt"]), source_url=prior_report.get("source_url"))
            if not any(page["page"] == job["page"] and page.get("machine_quality_accepted") for page in approved):
                raise ValueError("staged quality receipt did not approve the exact candidate page")
            fresh_template = fresh
            corrected_results.extend(fresh["results"])
            for name in ARTIFACTS:
                replacements[name][job["page"]] = _page_rows(candidate, name).get(job["page"], [])
            corrected_issues.extend(row for row in json.loads((candidate / "report.json").read_text())["issues"]
                                    if row.get("page") == job["page"])
        held_quality = json.loads(quality_output.read_text()) if quality_output.is_file() else None
        current_hashes = {n: row["text_sha256"] for n, row in original.items()}
        selected = {job["page"] for job in jobs}
        old_results = []
        if held_quality and held_quality.get("policy_sha256") == fresh_template["policy_sha256"]:
            old_results = [row for row in held_quality.get("results", []) if row["page"] not in selected and
                           row.get("page_text_sha256") == current_hashes.get(row["page"]) and
                           row.get("source_pdf_sha256") == source_sha]
        receipt = {**fresh_template, "results": sorted(old_results + corrected_results, key=lambda row: row["page"])}
        receipt["pages_assessed"] = len(receipt["results"])
        receipt["counts"] = {state: sum(row["outcome"] == state for row in receipt["results"])
                             for state in ("auto_accepted", "repair_required", "source_blocked")}
        staged = Path(tempfile.mkdtemp(prefix=f".{package.name}.merge-", dir=package.parent))
        backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
        try:
            shutil.copytree(package, staged, dirs_exist_ok=True)
            for name in ARTIFACTS:
                merged = merge_page_rows(_page_rows(package, name), replacements[name])
                content = json.dumps(merged, ensure_ascii=False) if name == "tables.json" else "".join(
                    json.dumps(row, ensure_ascii=False) + "\n" for row in merged)
                (staged / name).write_text(content)
            pages = _rows(staged / "pages.jsonl")
            report = deepcopy(prior_report)
            report["issues"] = [row for row in report["issues"] if row.get("page") not in selected] + corrected_issues
            report["page_status_counts"] = {status: sum(row["status"] == status for row in pages)
                                            for status in ("ready", "blank", "review_required")}
            report.update(text_chars=sum(len(row["text"]) for row in pages),
                          tables=len(json.loads((staged / "tables.json").read_text())),
                          numeric_candidates=len(_rows(staged / "fact-candidates.jsonl")),
                          structure_candidates=len(_rows(staged / "structure-candidates.jsonl")))
            (staged / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            create_package(staged)
            verify_package(staged, source_pdf=source)
            for name in ARTIFACTS:
                before, after = _page_rows(package, name), _page_rows(staged, name)
                if any(before.get(n, []) != after.get(n, []) for n in set(before) | set(after) if n not in selected):
                    raise ValueError("repair merge changed an untargeted page")
            _check_original(package, source, jobs, review_sidecar)
            package.replace(backup)
            try:
                staged.replace(package)
                write_quality_report(receipt, quality_output)
            except Exception:
                if package.exists():
                    shutil.rmtree(package)
                backup.replace(package)
                raise
            shutil.rmtree(backup)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        return {"source_pdf_sha256": source_sha, "committed_pages": sorted(selected),
                "package": str(package), "quality_receipt": str(quality_output), "index_ready": False}
