"""Review-only structural candidates with exact selected-page text spans.

These lexical cues are deliberately not legal facts. A reviewer must resolve
subject, version, applicability, OCR fidelity, and table header inheritance.
"""
from __future__ import annotations

import re
import hashlib
import json
from datetime import date
from functools import lru_cache
from importlib.resources import files


_ISSUER = re.compile(r"\b(?:CENTRAL ELECTRICITY REGULATORY COMMISSION|MINISTRY OF POWER|CENTRAL ELECTRICITY AUTHORITY|SOLAR ENERGY CORPORATION OF INDIA)\b", re.I)
_CONTACT = re.compile(r"\b(?:Phone|Tel(?:ephone)?|Fax|E-?mail)\s*[:.]?\s*[^\n]+", re.I)
_ADDRESS = re.compile(r"[^\n]*(?:Floor|Tower|Road|Colony|New Delhi|Mumbai|Kolkata|Chennai|Bengaluru|Hyderabad)[^\n]*", re.I)
_CLAUSE = re.compile(r"(?m)^(?:\d+(?:\.\d+)*\.?\s+[^\n:]{3,100}:?|(?:Regulation|Clause|Section)\s+\d+(?:\.\d+)*[^\n]{0,110})$", re.I)
_OPERATOR = re.compile(r"\b(?:whichever is higher|whichever is lower|greater of|higher of|lower of|sum of|added to|in addition to|at least|not less than|not more than|up to|exceeds?)\b", re.I)
_CONJUNCTION = re.compile(r"\b(?:and|or|both|either|each|all)\b", re.I)
_SUBJECT = re.compile(r"\b(?:generating station|generator transformers?|station auxiliary transformers?|battery energy storage systems?|BESS|ESS|petitioner|respondents?|distribution licensees?|NLDC|REGS|RHGS)\b", re.I)
_TEMPORAL = re.compile(r"\b(?:FY\s*\d{4}(?:[-–/]\d{2,4})?|financial year|annually|year-by-year|after completion of the financial year|effective from|with effect from|as on|as of|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.I)


def _evidence(page: dict, start: int, end: int) -> dict:
    text = page["text"]
    return {"page": page["page"], "page_id": page["id"],
            "page_text_sha256": page["text_sha256"], "char_start": start,
            "char_length": end - start, "quote": text[start:end],
            "offset_encoding": "codepoint", "selected_method": page["method"]}


def _cues(page: dict, segment: re.Match, pattern: re.Pattern) -> list[dict]:
    return [_evidence(page, segment.start() + match.start(), segment.start() + match.end())
            for match in pattern.finditer(segment.group())]


def _lines(text: str):
    return re.finditer(r"[^\n]+", text)


@lru_cache(maxsize=1)
def document_profiles() -> dict:
    """Global extraction hints; no document family establishes a legal fact."""
    raw = files("local_vector_batch").joinpath("document_profiles.json").read_bytes()
    result = json.loads(raw)
    if result.get("schema_version") != 1 or result.get("scope") != "format_and_field_candidates_only":
        raise ValueError("unsupported document format profiles")
    result["config_sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def format_candidates(page: dict, config: dict | None = None) -> list[dict]:
    config = config if config is not None else document_profiles()
    text, rows = page["text"], []
    header = text[:config["header_chars"]]
    for profile in config["profiles"]:
        headings = [match for pattern in profile["heading_patterns"]
                    for match in re.finditer(pattern, header, re.I | re.M)]
        if not headings:
            continue
        common = {"profile_id": profile["id"], "status": "review_required",
                  "profile_config_sha256": config.get("config_sha256"),
                  "review_reasons": ["format_hint_does_not_prove_role_value_date_or_legal_status"]}
        rows.append({**common, "kind": "document_format",
                     "evidence": [_evidence(page, match.start(), match.end()) for match in headings]})
        for field in profile["fields"]:
            field_text = header if field.get("scope") == "header" else text
            for match in re.finditer(field["pattern"], field_text, re.I | re.M):
                if match.end() > match.start():
                    unresolved = next((rule["state"] for rule in config.get("unresolved_value_patterns", [])
                                       if re.search(rule["pattern"], match.group(), re.I)), None)
                    rows.append({**common, "kind": "format_field", "proposed_field": field["role"],
                                 "value_state": unresolved or "unverified_literal",
                                 "evidence": [_evidence(page, match.start(), match.end())]})
    return rows


@lru_cache(maxsize=16)
def _date_patterns(glyphs: str):
    # A literal-looking date is only a cue. All groups refer to source text;
    # no corrected value is written back to a page or a structured fact.
    chars = r"\d" + re.escape(glyphs)
    day = rf"(?P<day>[{chars}]{{1,2}})"
    month = rf"(?P<month>[{chars}]{{1,2}})"
    year = rf"(?P<year>[{chars}]{{4}})"
    names = (r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
             r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?")
    return (
        (re.compile(rf"(?<!\w){year}(?P<sep>[-/.]){month}(?P=sep){day}(?!\w)", re.I), "numeric"),
        (re.compile(rf"(?<!\w){day}(?P<sep>[-/.]){month}(?P=sep){year}(?!\w)", re.I), "numeric"),
        (re.compile(rf"(?<!\w){day}(?:st|nd|rd|th)?\s+(?P<month>{names})\s*,?\s*{year}(?!\w)", re.I), "named"),
    )


def date_ocr_candidates(page: dict, config: dict | None = None) -> list[dict]:
    """Flag plausible OCR digit/letter confusion without asserting a date.

    Month names and the original spelling are retained. The mapping tests only
    whether a review cue is plausible; it never changes the extracted text.
    """
    config = config if config is not None else document_profiles()
    policy = config.get("ocr_date_review", {})
    if not policy.get("enabled"):
        return []
    mapping = policy.get("confusable_digits", {})
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or len(k) != 1 or
            not isinstance(v, str) or len(v) != 1 or v not in "0123456789" for k, v in mapping.items()):
        raise ValueError("invalid OCR date review configuration")
    min_year, max_year = policy.get("min_year", 1), policy.get("max_year", 9999)
    if (type(min_year) is not int or type(max_year) is not int or
            not 1 <= min_year <= max_year <= 9999):
        raise ValueError("invalid OCR date review year range")
    if not mapping:
        return []
    names = {name: number for number, values in enumerate((
        ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
        ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
        ("sep", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")), 1)
        for name in values}
    rows, seen = [], set()
    for pattern, kind in _date_patterns(''.join(sorted(mapping))):
        for match in pattern.finditer(page["text"]):
            fields = ("day", "year") if kind == "named" else ("day", "month", "year")
            ambiguous = [(field, match.start(field) + index, char)
                         for field in fields for index, char in enumerate(match.group(field)) if char in mapping]
            if not ambiguous or match.span() in seen:
                continue
            convert = lambda text: ''.join(mapping.get(char, char) for char in text)
            try:
                day = int(convert(match.group("day")))
                month = names[match.group("month").lower()] if kind == "named" else int(convert(match.group("month")))
                year = int(convert(match.group("year")))
                if not min_year <= year <= max_year:
                    continue
                date(year, month, day)
            except (ValueError, KeyError):
                continue
            seen.add(match.span())
            rows.append({"kind": "date_ocr_anomaly", "status": "review_required",
                         "evidence": [_evidence(page, match.start(), match.end()),
                                      *[_evidence(page, offset, offset + 1) for _, offset, _ in ambiguous]],
                         "uncertain_characters": [{"field": field, "observed": char,
                             "possible_digit": mapping[char], "evidence": _evidence(page, offset, offset + 1)}
                             for field, offset, char in ambiguous],
                         "profile_config_sha256": config.get("config_sha256"),
                         "review_reasons": ["possible_letter_digit_confusion_verify_original_pdf",
                                            "date_role_and_legal_effect_unresolved"]})
    return sorted(rows, key=lambda row: row["evidence"][0]["char_start"])


def page_candidates(page: dict) -> list[dict]:
    """Emit literal spans only; role and meaning are proposals for review."""
    text = page["text"]
    rows: list[dict] = []
    issuer = _ISSUER.search(text[: min(len(text), 1800)]) if page["page"] == 1 else None
    if issuer:
        rows.append({"kind": "party_role", "proposed_role": "issuer_masthead",
                     "evidence": [_evidence(page, issuer.start(), issuer.end())],
                     "review_reasons": ["issuer_must_be_confirmed_against_document_and_registry"],
                     "status": "review_required"})
    sections = document_profiles()["letter_sections"]
    to_line = re.search(sections["recipient_start"], text, re.I | re.M)
    subject_line = re.search(sections["subject_start"], text, re.I | re.M)
    if to_line and subject_line and subject_line.start() > to_line.end():
        target = re.sub(r"^\s*Date\s*:[^\n]*\n", "", text[to_line.end():subject_line.start()], flags=re.I).strip()
        if target:
            start = text.index(target, to_line.end(), subject_line.start())
            rows.append({"kind": "party_role", "proposed_role": "addressee",
                         "evidence": [_evidence(page, start, start + len(target))],
                         "review_reasons": ["addressee_is_not_issuer_or_document_subject"],
                         "status": "review_required"})
    masthead_end = min([m.start() for m in (to_line, subject_line) if m] or [min(len(text), 1500)]) if page["page"] == 1 else 0
    for pattern, role in ((_CONTACT, "masthead_contact"), (_ADDRESS, "masthead_address")):
        for match in pattern.finditer(text[:masthead_end]):
            rows.append({"kind": "party_role", "proposed_role": role,
                         "evidence": [_evidence(page, match.start(), match.end())],
                         "review_reasons": ["ocr_and_issuer_address_association"],
                         "status": "review_required"})
    for heading in _CLAUSE.finditer(text):
        rows.append({"kind": "clause_heading", "evidence": [_evidence(page, heading.start(), heading.end())],
                     "review_reasons": ["hierarchy_and_version_not_inferred"], "status": "review_required"})
    # A line is the smallest safe lexical condition window. Long wrapped clauses
    # can need a reviewer to join adjacent lines; no implicit AND/OR is assigned.
    for line in _lines(text):
        operators = _cues(page, line, _OPERATOR)
        conjunctions = _cues(page, line, _CONJUNCTION)
        subjects = _cues(page, line, _SUBJECT)
        temporal = _cues(page, line, _TEMPORAL)
        if not (operators or (subjects and (conjunctions or temporal))):
            continue
        rows.append({"kind": "condition_cues", "evidence": [_evidence(page, line.start(), line.end())],
                     "operator_cues": operators, "conjunction_cues": conjunctions,
                     "subject_cues": subjects, "temporal_scope_cues": temporal,
                     "review_reasons": ["lexical_cues_do_not_establish_formula_or_applicability",
                                        "resolve_wrapped_lines_and_amendment_scope"],
                     "status": "review_required"})
    return rows + format_candidates(page) + date_ocr_candidates(page)


def table_candidates(table: dict, page: dict) -> list[dict]:
    """Expose row/column/header candidates without filling merged blank cells.

    PDF geometry is kept even when flattened selected-page text cannot locate a
    cell uniquely. Such cells are explicitly unusable as exact quote evidence.
    """
    text = page["text"]
    rows = []
    # A repeated cell literal can still have an exact quote span when its
    # complete row occurs once in selected page text. Keep this as a lexical
    # anchor only: the proposed grid and header relationship still need review.
    row_spans = {}
    for row_index, cells in enumerate(table["cells"]):
        values = [(column, str(raw).strip()) for column, raw in enumerate(cells)
                  if raw is not None and str(raw).strip()]
        if len(values) < 2:
            continue
        pieces = [r"(?<!\w)"]
        for position, (_, value) in enumerate(values):
            if position:
                pieces.append(r"\s+")
            pieces.append("(?P<c%d>%s)" % (position, r"\s+".join(re.escape(part) for part in value.split())))
        pieces.append(r"(?!\w)")
        matches = list(re.finditer("".join(pieces), text))
        if len(matches) == 1:
            row_spans[row_index] = {column: matches[0].span(f"c{position}")
                                    for position, (column, _) in enumerate(values)}
    for row_index, cells in enumerate(table["cells"]):
        for column_index, raw in enumerate(cells):
            if raw is None or not str(raw).strip():
                continue
            value = str(raw).strip()
            # Native extraction often replaces a cell's line breaks with spaces.
            # Permit only whitespace changes, while retaining the exact source span.
            flexible = r"\s+".join(re.escape(part) for part in value.split())
            hits = list(re.finditer(flexible, text)) if flexible else []
            bounds = (hits[0].start(), hits[0].end()) if len(hits) == 1 else row_spans.get(row_index, {}).get(column_index)
            span = _evidence(page, *bounds) if bounds else None
            status = ("exact_unique" if len(hits) == 1 else
                      "exact_unique_row_context" if bounds else "ambiguous_or_not_in_selected_text")
            rows.append({"kind": "table_cell", "table_id": table["id"], "table_version_id": table.get("table_version_id"),
                         "table_bbox": table["bbox"], "row": row_index, "column": column_index,
                         "proposed_header": row_index == 0,
                         "candidate_header_row": 0 if row_index else None,
                         "value_text": value, "evidence": [span] if span else [],
                         "span_status": status,
                         "review_reasons": ["row_column_header_and_merged_cell_association",
                                            "page_text_span_missing_or_ambiguous" if not span else "visual_table_review"],
                         "status": "review_required"})
    return rows
