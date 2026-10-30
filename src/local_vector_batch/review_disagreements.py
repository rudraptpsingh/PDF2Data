"""Propose local OCR review questions; no extraction or evidence is promoted."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines
from collections import Counter
from difflib import SequenceMatcher
import json
import re
from pathlib import Path

TOKEN = re.compile(r'\d+(?:[./:-]\d+)*|[^\W\d_]+|[^\w\s]', re.UNICODE)
DATE = re.compile(r'\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b')
NUM = re.compile(r'\d')
LOGIC = {'not', 'no', 'unless', 'and', 'or', 'shall', 'may', 'must', 'except', 'only', 'less', 'more'}
ENUMERATOR = re.compile(r'\(\s*[ivxlcdm1ı]{1,6}\s*\)', re.I)
EQUATION = re.compile(r'\d[\d,.]*(?:[ \t]*[+\-−×*/=][ \t]*\d[\d,.]*)+')
UNIT = re.compile(r'/\s*(?:MW|MWh|kW|kWh)\s*/\s*(?:month|year|annum|day)\b', re.I)


def _kind(a: str, b: str) -> tuple[str, int] | None:
    value = (a + ' ' + b).strip()
    if DATE.search(value):
        return 'date', 0
    if re.search(r'\][ \t]*\d{1,2}\b', value):
        return 'footnote_candidate', 1
    if NUM.search(value):
        return 'number', 2
    if any(word.lower() in LOGIC for word in TOKEN.findall(value)):
        return 'legal_operator', 3
    if re.search(r'\b[A-Z]{2,}[0-9-]*\b|\b[A-Z][a-z]{3,}\b', value):
        return 'name_or_identifier_candidate', 4
    return None


def _excerpt(text: str, start: int, end: int, radius: int = 75) -> str:
    return text[max(0, start-radius):min(len(text), min(end, start + 400)+radius)].strip()


def _inside_url(text: str, start: int, end: int) -> bool:
    """URL digits and encoding artifacts are not legal numeric fields."""
    if start == end:
        return False
    left = start
    while left and not text[left - 1].isspace():
        left -= 1
    right = end
    while right < len(text) and not text[right].isspace():
        right += 1
    return bool(re.search(r'(?:https?://|www\.|%[0-9a-f]{2}|\.pdf\b)', text[left:right], re.I))


def _source_box(page: dict, start: int, end: int) -> list[list[float]]:
    """Crop only an exact uniquely located OCR line with known normalized geometry."""
    ocr = page.get('ocr') or {}
    if ocr.get('bbox_coordinates') != 'normalized top-left':
        return []
    text = page.get('text', '')
    matches = []
    for line in ocr.get('lines') or []:
        value, box = line.get('text'), line.get('bbox')
        if not isinstance(value, str) or not value or text.count(value) != 1:
            continue
        offset = text.find(value)
        if not (offset <= start < offset + len(value) and end <= offset + len(value)):
            continue
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(n) not in (int, float) or not 0 <= n <= 1 for n in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            continue
        matches.append([round(max(0, box[0] - .03), 4), round(max(0, box[1] - .02), 4),
                        round(min(1, box[2] + .03), 4), round(min(1, box[3] + .02), 4)])
    return matches if len(matches) == 1 else []


def discrepancies(selected: str, independent: str, page: dict, *, max_items: int = 8,
                  include_all: bool = False) -> list[dict]:
    """Find high-value token changes, returning literal excerpts and optional source crops."""
    left, right = list(TOKEN.finditer(selected)), list(TOKEN.finditer(independent))
    if len(left) + len(right) > 12000:
        return []
    result = []
    for op, i, j, k, l in SequenceMatcher(None, [m.group() for m in left], [m.group() for m in right]).get_opcodes():
        if op == 'equal':
            continue
        a = ''.join(m.group() for m in left[i:j]); b = ''.join(m.group() for m in right[k:l])
        kind = _kind(a, b)
        a_start = left[i].start() if i < len(left) else len(selected)
        a_end = left[j-1].end() if j > i else a_start
        b_start = right[k].start() if k < len(right) else len(independent)
        b_end = right[l-1].end() if l > k else b_start
        if _inside_url(selected, a_start, a_end) or _inside_url(independent, b_start, b_end):
            if not include_all:
                continue
            kind = ('url_text', 7)
        a_near = selected[max(0, a_start-45):min(len(selected), a_end+55)]
        b_near = independent[max(0, b_start-45):min(len(independent), b_end+55)]
        changed = a + b
        # A missing/extra arithmetic symbol between bracketed operands or
        # numeric terms can change the calculation. Show the source rather
        # than interpreting or repairing the formula.
        if (changed and set(changed) <= set('/+*=−÷×') and
                (re.search(r'\]\s*[/+*=−÷×]\s*\[', a_near + ' ' + b_near) or
                 re.search(r'\d\s*[/+*=−÷×]\s*\d', a_near + ' ' + b_near))):
            kind = ('math_operator', 0)
        elif (UNIT.search(a_near) or UNIT.search(b_near)) and (
                a in ('', '?', '₹', 'Rs', 'Rs.', 'Z') and b in ('', '?', '₹', 'Rs', 'Rs.', 'Z')):
            kind = ('currency_unit_candidate', 0)
        if (ENUMERATOR.search(a) or ENUMERATOR.search(b)) and (
                len(ENUMERATOR.findall(selected[max(0, a_start-500):min(len(selected), a_end+500)])) >= 2 or
                len(ENUMERATOR.findall(independent[max(0, b_start-500):min(len(independent), b_end+500)])) >= 2):
            kind = ('enumerator_candidate', 1)
        if not kind:
            if not include_all:
                continue
            kind = ('text_change', 6)
        if kind[0] == 'number' and any(re.search(r'\][ \t]*\d{1,2}\b', value) for value in
                (selected[max(0, a_start-4):min(len(selected), a_end+2)],
                 independent[max(0, b_start-4):min(len(independent), b_end+2)])):
            kind = ('footnote_candidate', 1)
        marker = ENUMERATOR.search(selected, a_start, a_end) if kind[0] == 'enumerator_candidate' else None
        crop_start = marker.start() if marker else a_start
        crop_end = marker.end() if marker else min(a_end, left[i].end()) if j > i else (
                   left[i].end() if i < len(left) else crop_start)
        focus = {'kind': kind[0], 'selected_excerpt': _excerpt(selected, a_start, a_end),
                 'independent_excerpt': _excerpt(independent, b_start, b_end),
                 'selected_span': [a_start, a_end], 'independent_span': [b_start, b_end],
                 'boxes': _source_box(page, crop_start, crop_end) if crop_end > crop_start else []}
        result.append({'kind': kind[0], 'priority': kind[1], 'focus': focus, 'offset': a_start})
    if include_all:
        # Record line-wrap differences only between adjacent matched tokens;
        # both raw strings remain intact and equation symbols are not changed.
        for op, i, j, k, l in SequenceMatcher(None, [m.group() for m in left], [m.group() for m in right]).get_opcodes():
            if op != 'equal':
                continue
            for n in range(max(0, j-i-1)):
                a0, a1 = left[i+n].end(), left[i+n+1].start()
                b0, b1 = right[k+n].end(), right[k+n+1].start()
                ga, gb = selected[a0:a1], independent[b0:b1]
                if ga == gb or not ('\n' in ga or '\n' in gb) or ga.strip() or gb.strip():
                    continue
                focus = {'kind': 'formatting', 'selected_excerpt': _excerpt(selected, a0, a1),
                         'independent_excerpt': _excerpt(independent, b0, b1),
                         'selected_span': [a0, a1], 'independent_span': [b0, b1],
                         'boxes': _source_box(page, left[i+n].start(), left[i+n].end())}
                result.append({'kind': 'formatting', 'priority': 8, 'focus': focus, 'offset': a0})
    # Whitespace inside a mathematical expression can be presented for review
    # only when every non-space character, including operators, already agrees.
    a_equations, b_equations = list(EQUATION.finditer(selected)), list(EQUATION.finditer(independent))
    for a in a_equations:
        same = [b for b in b_equations if re.sub(r'\s+', '', b.group()) == re.sub(r'\s+', '', a.group())]
        if len(same) != 1 or a.group() == same[0].group():
            continue
        b = same[0]
        if sum(re.sub(r'\s+', '', a.group()) == re.sub(r'\s+', '', x.group()) for x in a_equations) != 1:
            continue
        focus = {'kind': 'math_spacing', 'selected_excerpt': a.group(), 'independent_excerpt': b.group(),
                 'selected_span': [a.start(), a.end()], 'independent_span': [b.start(), b.end()],
                 'boxes': _source_box(page, a.start(), a.end())}
        result.append({'kind': 'math_spacing', 'priority': 5, 'focus': focus, 'offset': a.start()})
    # Stable ordering; keep unique literal comparisons rather than duplicates.
    result.sort(key=lambda r: (r['priority'], r['offset']))
    unique, seen = [], set()
    for row in result:
        key = (row['kind'], tuple(row['focus']['selected_span']), tuple(row['focus']['independent_span']))
        if key not in seen:
            unique.append(row); seen.add(key)
        if len(unique) >= max_items:
            break
    return unique


def feedback_entries(paths: list[Path]) -> tuple[dict, int]:
    latest, invalid = {}, 0
    keys = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256')
    for path in paths:
        for line in jsonl_physical_lines(Path(path).read_text(encoding='utf-8')):
            try:
                row = json.loads(line)
                # Earlier feedback lacked a focus hash. Retain it for summary,
                # but never use it to skip a specific new disagreement.
                key = tuple((row.get(k) or 'legacy') if k in ('focus_sha256', 'proposal_sha256') else row[k] for k in keys)
                if row.get('kind') != 'ocr_disagreement_feedback' or row.get('decision') not in {'selected','independent','neither','unclear'}:
                    raise ValueError()
                if (type(row['page']) is not int or row['page'] < 1 or
                        any(not isinstance(row[k], str) or not re.fullmatch(r'[0-9a-f]{64}', row[k])
                            for k in keys if k not in ('page', 'focus_sha256', 'proposal_sha256')) or
                        (row.get('focus_sha256') is not None and
                         (not isinstance(row['focus_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['focus_sha256']))) or
                        (row.get('proposal_sha256') is not None and
                         (not isinstance(row['proposal_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['proposal_sha256'])))):
                    raise ValueError()
                if key not in latest or str(row.get('created_at', '')) >= str(latest[key].get('created_at', '')):
                    latest[key] = row
            except (ValueError, KeyError, TypeError):
                invalid += 1
    return latest, invalid


def feedback_summary(paths: list[Path]) -> dict:
    """Count latest exact-identity feedback; older generic feedback remains unclassified."""
    latest, invalid = feedback_entries(paths)
    patterns = Counter()
    for row in latest.values():
        note = str(row.get('note') or '').lower()
        if re.search(r'\broman\b|\bnumeral\b|\bnumberal\b|\(iii\)', note):
            patterns['enumerator_mentioned'] += 1
        if re.search(r'\bmath\w*\b|\bequation\b', note) and re.search(r'\bspac\w*\b|\bwhitespace\b', note):
            patterns['math_spacing_mentioned'] += 1
        if re.search(r'\bline.?break\b|\bnew.?line\b', note):
            patterns['line_wrap_mentioned'] += 1
    return {'feedback_entries': len(latest), 'unique_pages': len({(key[0], key[1]) for key in latest}),
            'legacy_unfocused_entries': sum(key[5] == 'legacy' for key in latest),
            'legacy_without_proposal_binding': sum(key[6] == 'legacy' for key in latest),
            'decisions': dict(Counter(r['decision'] for r in latest.values())),
            'suggestion_feedback': {
                'accepted': sum(bool(r.get('proposal_choice')) and r['decision'] == r['proposal_choice'] for r in latest.values()),
                'different_choice': sum(bool(r.get('proposal_choice')) and r['decision'] != r['proposal_choice'] for r in latest.values()),
            },
            'disagreement_types': dict(Counter(r.get('disagreement_kind') or 'unclassified' for r in latest.values())),
            'note_patterns': dict(patterns),
            'invalid_entries': invalid, 'interpretation': 'Feedback is a review label, not a page approval or model quality score.'}
