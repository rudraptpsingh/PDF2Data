"""Validate candidate source evidence locally; never certify legal semantics."""
from __future__ import annotations

import hashlib
import re

DATE_ROLES = {"issue", "publication", "effective", "hearing", "submission_deadline", "observed", "unclassified"}
CONTACT_ROLES = {"issuer", "masthead_contact", "addressee", "counsel", "petitioner", "respondent", "unclassified"}


def _normalized(text: str):
    """Whitespace-only normalization with original codepoint span mapping."""
    chars, spans = [], []
    for match in re.finditer(r"\s+|\S", text):
        chars.append(" " if match.group().isspace() else match.group())
        spans.append(match.span())
    return "".join(chars), spans


def check_quote(page: dict, evidence: dict) -> dict:
    """Resolve a verbatim or explicitly whitespace-normalized contiguous quote.

    Supplied offsets must agree exactly; a wrong offset is never repaired by
    searching elsewhere. Ambiguous repeated quotes require explicit offsets.
    """
    text, quote = page.get("text"), evidence.get("quote")
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        raise ValueError("missing source text or nonempty quote")
    digest = hashlib.sha256(text.encode()).hexdigest()
    if page.get("text_sha256") != digest or evidence.get("page_text_sha256", digest) != digest:
        raise ValueError("page text hash mismatch")
    normalization = evidence.get("normalization", "none")
    if normalization not in {"none", "whitespace"}:
        raise ValueError("only explicit whitespace normalization is supported")
    def matches(fragment):
        return fragment == quote if normalization == "none" else _normalized(fragment)[0].strip() == _normalized(quote)[0].strip()
    if "char_start" in evidence or "char_length" in evidence:
        start, length = evidence.get("char_start"), evidence.get("char_length")
        if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(length, int) or
                isinstance(length, bool) or start < 0 or length < 1 or start + length > len(text)):
            raise ValueError("invalid codepoint offsets")
        end = start + length
        if not matches(text[start:end]):
            raise ValueError("quote does not match supplied source offsets")
    else:
        haystack, mapping = _normalized(text) if normalization == "whitespace" else (text, None)
        needle = _normalized(quote)[0].strip() if normalization == "whitespace" else quote
        hits = list(re.finditer(r"(?=" + re.escape(needle) + r")", haystack))
        if len(hits) != 1:
            raise ValueError("quote is absent or ambiguous; supply exact offsets")
        begin = hits[0].start()
        start = mapping[begin][0] if mapping else begin
        end = mapping[begin + len(needle) - 1][1] if mapping else begin + len(needle)
    return {"page": page["page"], "page_id": page.get("id"), "page_text_sha256": digest,
            "char_start": start, "char_length": end - start, "offset_encoding": "codepoint",
            "source_quote": text[start:end], "normalization": normalization}


def validate_candidate(candidate: dict, pages: list[dict]) -> dict:
    """Check evidence and typed-field support without promoting a candidate.

    Multi-cell tables, legal/date role meaning and applicability always need
    human/downstream review, even when every quote exists in the source.
    """
    errors, reviews, checked = [], [], []
    by_page = {page["page"]: page for page in pages}
    if len(by_page) != len(pages):
        raise ValueError("duplicate page numbers in evidence source")
    evidence = candidate.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("missing_evidence")
        evidence = []
    for item in evidence:
        try:
            if (not isinstance(item, dict) or not isinstance(item.get("page"), int) or
                    isinstance(item.get("page"), bool) or item.get("page") not in by_page):
                raise ValueError("source page missing")
            checked.append(check_quote(by_page[item["page"]], item))
        except ValueError as error:
            errors.append(str(error))
    kind = candidate.get("kind")
    table = candidate.get("evidence_layout") == "table_cells" or any(
        isinstance(item, dict) and any(key in item for key in ("table_id", "cell", "row", "column")) for item in evidence)
    if table:
        reviews.append("table_row_column_header_association")
    values = [c["source_quote"] for c in checked]
    value = candidate.get("value_text")
    if kind in {"number", "date"}:
        if not isinstance(value, str) or not value.strip() or not any(value in q for q in values):
            errors.append("value_not_in_checked_evidence")
    if kind == "number":
        unit = candidate.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            reviews.append("unit_unspecified")
        elif isinstance(value, str):
            v, u = re.escape(value), re.escape(unit)
            pattern = rf"(?:(?<![\w.]){v}\s*{u}(?!\w)|(?<!\w){u}\s*{v}(?![\w.]))"
            if not any(re.search(pattern, q) for q in values):
                unit_present = any(re.search(rf"(?<!\w){u}(?!\w)", q) for q in values)
                (reviews if table and unit_present else errors).append("unit_not_adjacent_to_value")
        reviews.append("subject_version_and_applicability")
    elif kind == "date":
        if candidate.get("date_role") not in DATE_ROLES:
            errors.append("invalid_date_role")
        if candidate.get("date_role") == "unclassified":
            reviews.append("date_role_unclassified")
        reviews.append("date_role_precision_and_validity")
    elif kind == "contact":
        if candidate.get("contact_role") not in CONTACT_ROLES:
            errors.append("invalid_contact_role")
        if candidate.get("contact_role") in {"issuer", "masthead_contact"} and candidate.get("evidence_basis") not in {"masthead", "official_registry"}:
            errors.append("issuer_requires_masthead_or_official_registry")
        reviews.append("institution_role_and_address_association")
    else:
        errors.append("unsupported_candidate_kind")
    return {"status": "rejected_candidate" if errors else "review_required", "verified": False,
            "evidence_valid": not errors, "errors": errors, "review_reasons": reviews,
            "checked_evidence": checked}


def validate_candidates(candidates: list[dict], pages: list[dict]) -> dict:
    results = [validate_candidate(candidate, pages) for candidate in candidates]
    return {"candidates": len(results), "evidence_valid": sum(r["evidence_valid"] for r in results),
            "verified": 0, "results": results}
