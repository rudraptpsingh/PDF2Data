"""Suggest cross-document readings without deciding an identity or editing text.

An exact shared reference narrows the review queue. It is not proof that two
people or documents are the same. Source PDF bytes and package anchors are
checked, but OCR and document relationships still require visual review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import unicodedata

from .evidence import check_quote
from .evidence_package import verify_package


@dataclass(frozen=True)
class SourcePage:
    document_id: str
    title: str
    source_url: str | None
    pdf_sha256: str
    page: dict


def load_source_pages(package: Path, original_pdf: Path, page_numbers: list[int]) -> dict[int, SourcePage]:
    """Check a PDF once and load the requested physical pages from its package."""
    package, original_pdf = Path(package), Path(original_pdf)
    if (not isinstance(page_numbers, list) or not page_numbers or
            any(type(number) is not int or number < 1 for number in page_numbers) or
            len(set(page_numbers)) != len(page_numbers)):
        raise ValueError("physical page numbers must be unique positive integers")
    verify_package(package, source_pdf=original_pdf)
    document = json.loads((package / "evidence-package.json").read_text(encoding="utf-8"))["document"]
    pages = (json.loads(line) for line in (package / "pages.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    wanted = set(page_numbers)
    selected = {item['page']: item for item in pages if item.get('page') in wanted}
    if set(selected) != wanted:
        raise ValueError("physical page is absent from the verified package")
    return {number: SourcePage(document["id"], document["title"], document.get("source_url"),
                               document["pdf_sha256"], selected[number]) for number in page_numbers}


def load_source_page(package: Path, original_pdf: Path, page_number: int) -> SourcePage:
    """Load a physical page only after checking the original PDF and package."""
    return load_source_pages(package, original_pdf, [page_number])[page_number]


def _span(source: SourcePage, evidence: dict) -> dict:
    if not isinstance(source, SourcePage) or not isinstance(evidence, dict):
        raise ValueError("source page and literal evidence are required")
    page = source.page
    if (page.get("id") != evidence.get("page_id") or page.get("page") != evidence.get("page") or
            page.get("text_sha256") != evidence.get("page_text_sha256") or
            hashlib.sha256(page["text"].encode()).hexdigest() != page["text_sha256"]):
        raise ValueError("evidence page identity or text hash mismatch")
    if "char_start" not in evidence or "char_length" not in evidence:
        raise ValueError("exact evidence offsets are required")
    checked = check_quote(page, evidence)
    quote = checked["source_quote"]
    if not quote.strip() or evidence.get("quote") != quote:
        raise ValueError("evidence requires an exact, nonempty source text span")
    start, end = checked["char_start"], checked["char_start"] + checked["char_length"]
    context_start, context_end = max(0, start - 160), min(len(page["text"]), end + 160)
    return {"document_id": source.document_id, "title": source.title, "source_url": source.source_url,
            "source_pdf_sha256": source.pdf_sha256, "page": page["page"],
            "page_text_sha256": page["text_sha256"], "page_status": page.get("status"),
            "char_start": evidence["char_start"], "char_length": evidence["char_length"],
            "quote": quote, "offset_encoding": "codepoint",
            "context": {"char_start": context_start, "char_length": context_end - context_start,
                        "quote": page["text"][context_start:context_end]}}


def _reference_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _reference_span(source: SourcePage, evidence: dict) -> dict:
    result = _span(source, evidence)
    text, start = source.page["text"], result["char_start"]
    end = start + result["char_length"]
    # An extracted `42/MP/2019` must never be linked from the middle of
    # `142/MP/2019` or a longer slash-delimited identifier.
    def identifier_char(char: str) -> bool:
        return char.isalnum() or char in "_/-"
    if ((start and identifier_char(text[start - 1])) or
            (end < len(text) and identifier_char(text[end]))):
        raise ValueError("reference span is part of a longer identifier")
    line_start = max(text.rfind("\n", 0, start) + 1, start - 240)
    line_end = text.find("\n", end)
    line_end = min(len(text) if line_end < 0 else line_end, end + 240)
    result["line_context"] = {"char_start": line_start, "char_length": line_end - line_start,
                              "quote": text[line_start:line_end]}
    return result


def suggest_related_readings(*, target_page: SourcePage, obscured_span: dict,
                             target_reference_page: SourcePage, target_reference: dict,
                             category: str, related: list[dict]) -> dict:
    """Return source-backed possibilities for a human to inspect, never a replacement.

    Each related item supplies ``reading_page``, ``reading_span``, ``reference_page``,
    ``reference_span`` and ``category``. The reading and reference may occur on
    different physical pages of the same source PDF. A matching category alone
    is insufficient: a literal shared alphanumeric reference is also required.
    """
    if (not isinstance(target_page, SourcePage) or not isinstance(target_reference_page, SourcePage) or
            not isinstance(category, str) or not category.strip() or not isinstance(related, list)):
        raise ValueError("a category and related candidates are required")
    if target_page.pdf_sha256 != target_reference_page.pdf_sha256:
        raise ValueError("target reference must belong to the target PDF")
    obscured = _span(target_page, obscured_span)
    target_ref = _reference_span(target_reference_page, target_reference)
    reference_key = _reference_key(target_ref["quote"])
    if (len(reference_key) < 4 or not any(c.isalpha() for c in reference_key) or
            not any(c.isdigit() for c in reference_key)):
        raise ValueError("shared reference must be a specific alphanumeric source span")
    results, seen = [], set()
    for item in related:
        if not isinstance(item, dict) or not isinstance(item.get("category"), str):
            raise ValueError("invalid related source candidate")
        if item["category"].strip().casefold() != category.strip().casefold():
            continue
        ref_page, reading_page = item.get("reference_page"), item.get("reading_page")
        if not isinstance(ref_page, SourcePage) or not isinstance(reading_page, SourcePage):
            raise ValueError("related reading and reference pages must be source-checked")
        if ref_page.pdf_sha256 != reading_page.pdf_sha256:
            raise ValueError("related reading and reference must belong to the same PDF")
        if reading_page.pdf_sha256 == target_page.pdf_sha256:
            continue
        ref = _reference_span(ref_page, item.get("reference_span"))
        if _reference_key(ref["quote"]) != reference_key:
            continue
        reading = _span(reading_page, item.get("reading_span"))
        identity = (reading["source_pdf_sha256"], reading["page"], reading["char_start"], reading["char_length"])
        if identity in seen:
            continue
        seen.add(identity)
        results.append({"reading": reading, "shared_reference": ref, "status": "review_required"})
    return {"schema_version": 1, "kind": "related_source_reading_candidates",
            "status": "review_required", "usable_for_answer": False,
            "target": obscured, "target_reference": target_ref,
            "category": category.strip(), "candidates": results,
            "candidate_count": len(results), "multiple_possible_sources": len(results) > 1,
            "review_reasons": ["source_text_may_contain_ocr_errors", "shared_reference_does_not_prove_same_identity_or_context",
                               "confirm_original_pages_and_applicability_before_use"]}
