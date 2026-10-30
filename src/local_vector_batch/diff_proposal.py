"""Conservative diagnostic suggestions for source-bound OCR differences.

This module does not verify or publish a page. Its input is an OCR receipt from
independent readers of the same original-page crop; callers must retain and
protect the receipt and originating image.
"""
from dataclasses import dataclass
from hashlib import sha256
import re

@dataclass(frozen=True)
class DiffPolicy:
    min_context_tokens_each_side: int = 2
    allowed_kinds: tuple[str, ...] = ('enumerator_candidate', 'footnote_candidate', 'number', 'name_or_identifier_candidate', 'legal_operator')


def propose_diff(receipt: dict, item: dict, crop: bytes, policy: DiffPolicy = DiffPolicy()) -> dict:
    def abstain(reason):
        return {'choice': None, 'reason': reason, 'publication_allowed': False}
    keys = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'focus_sha256')
    if any(receipt.get(k) != item.get(k) for k in keys) or receipt.get('crop_sha256') != sha256(crop).hexdigest():
        return abstain('Source or crop identity mismatch')
    if receipt.get('kind') not in policy.allowed_kinds:
        return abstain('Difference type is outside the validated policy')
    a, b = receipt.get('selected_diff', ''), receipt.get('independent_diff', '')
    if not isinstance(a, str) or not isinstance(b, str) or not a.strip() or not b.strip():
        return abstain('Missing span or possible deletion')
    focus = item.get('focus') or {}
    for side, value in (('selected', a), ('independent', b)):
        span = focus.get(side + '_span')
        text = item.get(side + '_text')
        if span is not None and (not isinstance(text, str) or len(span) != 2 or text[span[0]:span[1]] != value):
            return abstain('Receipt difference does not match the source-bound OCR span')
    # Preserve symbols and case: silently rewriting them can change legal meaning.
    norm = lambda s: ' '.join(s.split())
    a, b = norm(a), norm(b)
    if not re.fullmatch(r'[A-Za-z0-9 ()\[\].,:;-]+', a) or not re.fullmatch(r'[A-Za-z0-9 ()\[\].,:;-]+', b):
        return abstain('Unsupported language or symbol')
    # OCR diffs often contain only one token. Recover its surrounding text
    # from the source-bound OCR excerpts, never from generated reader output.
    if len(a.split()) == len(b.split()) == 1:
        focus = item.get('focus') or {}
        def context(excerpt, token):
            if not isinstance(excerpt, str):
                return None
            words = norm(excerpt).split()
            positions = [j for j, word in enumerate(words) if word == token]
            n = policy.min_context_tokens_each_side
            if len(positions) != 1 or positions[0] < n or len(words) - positions[0] - 1 < n:
                return None
            j = positions[0]
            return ' '.join(words[j-n:j+n+1])
        expanded_a = context(focus.get('selected_excerpt'), a)
        expanded_b = context(focus.get('independent_excerpt'), b)
        if not expanded_a or not expanded_b:
            return abstain('Insufficient unique source-bound context')
        a, b = expanded_a, expanded_b
        if not re.fullmatch(r'[A-Za-z0-9 ()\[\].,:;+-]+', a) or not re.fullmatch(r'[A-Za-z0-9 ()\[\].,:;+-]+', b):
            return abstain('Unsupported context language or symbol')
    at, bt = a.split(), b.split()
    if len(at) != len(bt):
        return abstain('Insertion or deletion in candidate spans')
    diffs = [i for i, (x, y) in enumerate(zip(at, bt)) if x != y]
    if len(diffs) != 1:
        return abstain('Difference is not a single aligned token')
    i = diffs[0]
    if i < policy.min_context_tokens_each_side or len(at)-i-1 < policy.min_context_tokens_each_side:
        return abstain('Insufficient aligned context')
    texts = [receipt.get('vlm_text'), receipt.get('rapid_text')]
    if any(not isinstance(x, str) or not x.strip() for x in texts):
        return abstain('Independent crop reader missing')
    # Each reader must contain the complete candidate span as an exact contiguous
    # sequence, never merely the disputed token or a partial line.
    def has(text, candidate):
        return re.search(r'(?<!\S)' + re.escape(candidate) + r'(?!\S)', norm(text)) is not None
    selected = all(has(t, a) and not has(t, b) for t in texts)
    independent = all(has(t, b) and not has(t, a) for t in texts)
    if selected == independent:
        return abstain('Readers disagree or do not cover a complete candidate span')
    return {'choice': 'selected' if selected else 'independent',
            'reason': 'Both crop readers contain the same complete aligned span; diagnostic suggestion only.',
            'source_pdf_sha256': item['source_pdf_sha256'], 'page': item['page'],
            'focus_sha256': item['focus_sha256'], 'crop_sha256': receipt['crop_sha256'],
            'publication_allowed': False}
