"""Source-bound multi-page provision wrappers; lexical boundaries remain candidates.

This module deliberately does not infer legal revision edges or promote candidates.
Explicit span selections and review receipts are separate from PDF text readiness.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "powerlex.provisions.v1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span(page: dict, start: int, end: int) -> dict:
    if (type(start) is not int or type(end) is not int or start < 0 or
            end <= start or end > len(page["text"]) or sha(page["text"]) != page["text_sha256"]):
        raise ValueError("invalid source span or page hash")
    return {"page": page["page"], "page_text_sha256": page["text_sha256"],
            "char_start": start, "char_length": end-start, "offset_encoding": "codepoint",
            "exact_quote": page["text"][start:end]}


def validate_wrapper(wrapper: dict, pages: list[dict], pdf_sha256: str) -> None:
    if wrapper.get("source_pdf_sha256") != pdf_sha256:
        raise ValueError("provision PDF hash mismatch")
    previous = (0, 0)
    by_page = {p["page"]: p for p in pages}
    if not wrapper.get("spans"):
        raise ValueError("empty provision")
    for anchor in wrapper["spans"]:
        page = by_page.get(anchor.get("page"))
        start, length = anchor.get("char_start"), anchor.get("char_length")
        if page is None or type(start) is not int or type(length) is not int:
            raise ValueError("missing provision source page or offsets")
        expected = span(page, start, start+length)
        if anchor != expected or (page["page"], start) < previous:
            raise ValueError("inexact, overlapping or unordered provision spans")
        previous = (page["page"], start+length)
    if wrapper.get("boundary_state") == "complete" and not wrapper.get("review_receipt_id"):
        raise ValueError("complete boundaries require an explicit review receipt")


def select_provision(provision_id: str, label: str, pages: list[dict], pdf_sha256: str,
                     selections: list[dict], *, review_receipt_id: str | None = None,
                     dependencies: list[str] | None = None) -> dict:
    """Build a wrapper over exact page units; never flatten/rewrite continuation text."""
    by_page = {p["page"]: p for p in pages}
    anchors = []
    for selected in selections:
        page = by_page[selected["page"]]
        anchor = span(page, selected["char_start"], selected["char_start"]+selected["char_length"])
        if selected.get("exact_quote", anchor["exact_quote"]) != anchor["exact_quote"]:
            raise ValueError("selected quote differs from source")
        if selected.get("page_text_sha256", anchor["page_text_sha256"]) != anchor["page_text_sha256"]:
            raise ValueError("selected page hash differs from source")
        anchors.append(anchor)
    result = {"id": provision_id, "label": label, "source_pdf_sha256": pdf_sha256,
              "spans": anchors, "dependencies": dependencies or [],
              "boundary_state": "complete" if review_receipt_id else "candidate",
              "review_receipt_id": review_receipt_id, "semantic_state": "unresolved"}
    validate_wrapper(result, pages, pdf_sha256)
    return result


def provision_candidates(pages: list[dict], pdf_sha256: str) -> dict:
    """Group wrapped lines and continuation pages for review, with unresolved ends.

    Only top-level numbered headings partition this navigation view. Quoted
    numbering may be another instrument's text; every boundary stays unresolved.
    Number 1 has no special exclusion policy.
    """
    starts = []
    for page in pages:
        for match in re.finditer(r"(?m)^\s*(\d{1,3})\.\s+(?=\S)", page["text"]):
            starts.append((page["page"], match.start(), match.group(1)))
    wrappers = []
    for index, (first_page, first_start, label) in enumerate(starts):
        stop_page, stop_start = starts[index+1][:2] if index+1 < len(starts) else (len(pages)+1, 0)
        selections = []
        for page in pages:
            n = page["page"]
            if first_page <= n <= stop_page:
                lo, hi = first_start if n == first_page else 0, stop_start if n == stop_page else len(page["text"])
                if hi > lo:
                    selections.append({"page": n, "char_start": lo, "char_length": hi-lo})
        wrapper = select_provision(f"pdf-{pdf_sha256[:20]}:candidate:{index}", label,
                                   pages, pdf_sha256, selections)
        wrapper["review_reasons"] = ["quoted_numbering_and_hierarchy_unresolved", "continuation_and_end_require_review"]
        wrappers.append(wrapper)
    return {"schema_version": SCHEMA, "source_pdf_sha256": pdf_sha256,
            "status": "candidate_only", "provisions": wrappers}


def export_candidates(output: Path, pages: list[dict], pdf_sha256: str) -> dict:
    result = provision_candidates(pages, pdf_sha256)
    path = Path(output)/"provision-candidates.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def provision_children(wrapper: dict, pages: list[dict], *, width: int = 1000) -> list[dict]:
    """Exact searchable children, including short meaningful units; no vector invented."""
    validate_wrapper(wrapper, pages, wrapper["source_pdf_sha256"])
    if type(width) is not int or width < 1:
        raise ValueError("invalid child width")
    children = []
    for anchor in wrapper["spans"]:
        for offset in range(0, anchor["char_length"], width):
            text = anchor["exact_quote"][offset:offset+width]
            children.append({"id": sha(f'{wrapper["id"]}:{anchor["page"]}:{anchor["char_start"]+offset}:{sha(text)}'),
                "text": text, "parent_provision_version_id": wrapper["id"],
                "source_pdf_sha256": wrapper["source_pdf_sha256"], "page": anchor["page"],
                "page_text_sha256": anchor["page_text_sha256"],
                "char_start": anchor["char_start"]+offset, "char_length": len(text),
                "offset_encoding": "codepoint", "embedding_status": "input_only"})
    return children


def embedding_inputs(wrapper: dict, pages: list[dict], *, width: int = 1000) -> list[dict]:
    """Adapt exact children to the existing ``--input-mode passages`` contract.

    Each page is an owning source chunk; parent provision identity survives
    encoder/token-window splitting in scalar metadata. No model or vector is
    asserted until the existing embedding pipeline records its real receipt.
    """
    return [{"id": f"pdf-{wrapper['source_pdf_sha256'][:20]}:p{c['page']}",
             "text": c["text"], "chunk_index": 0, "char_start": c["char_start"],
             "metadata": {"parent_provision_version_id": wrapper["id"],
                          "source_pdf_sha256": c["source_pdf_sha256"],
                          "page_text_sha256": c["page_text_sha256"], "page": c["page"],
                          "provision_boundary_state": wrapper["boundary_state"]}}
            for c in provision_children(wrapper, pages, width=width)]
