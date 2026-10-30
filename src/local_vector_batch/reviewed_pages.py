"""Validate immutable, source-bound decisions for complete physical PDF pages.

The parser package stays unchanged. Only an explicit reviewed sidecar can turn a
held page into searchable text or a confirmed blank page. This validates receipt
integrity; the named reviewer remains responsible for visual transcription.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def apply_reviewed_page_decisions(
    pages: list[dict], source_pdf_sha256: str, receipt_path: Path, evidence_root: Path,
    relative_path_root: Path | None = None,
) -> tuple[list[dict], int]:
    """Return page copies with accepted decisions, preserving the parser input."""
    receipt_path = Path(receipt_path)
    if not receipt_path.exists():
        return pages, 0
    evidence_root = Path(evidence_root).resolve()
    original = {int(page['page']): page for page in pages}
    reviewed = {number: dict(page) for number, page in original.items()}
    seen: set[int] = set()
    promoted = 0
    for line in receipt_path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        decision = json.loads(line)
        number = decision.get('page')
        if type(number) is not int or number not in original or number in seen:
            raise ValueError('Review receipt has duplicate or invalid physical page')
        seen.add(number)
        page = original[number]
        if (page.get('status') != 'review_required' or
                decision.get('source_pdf_sha256') != source_pdf_sha256 or
                decision.get('original_text_sha256') != page.get('text_sha256')):
            raise ValueError(f'Review page {number} source or original text hash mismatch')
        action = decision.get('decision')
        if action not in ('approve_text', 'replace_text', 'blank_page', 'keep_held'):
            raise ValueError(f'Review page {number} has invalid decision')
        if action == 'keep_held':
            reviewed[number]['review_decision'] = 'keep_held'
            continue
        required_scope = 'visually_blank' if action == 'blank_page' else 'complete_recoverable_text'
        if (decision.get('review_scope') != required_scope or
                not isinstance(decision.get('review_basis'), str) or
                not decision['review_basis'].strip()):
            raise ValueError(f'Review page {number} needs {required_scope} scope and review basis')
        excluded = decision.get('excluded_regions')
        if (not isinstance(excluded, list) or
                any(not isinstance(region, str) or not region.strip() for region in excluded)):
            raise ValueError(f'Review page {number} needs an explicit excluded-regions list')
        text = decision.get('reviewed_text')
        if (not isinstance(text, str) or '\x00' in text or
                hashlib.sha256(text.encode('utf-8')).hexdigest() != decision.get('reviewed_text_sha256')):
            raise ValueError(f'Review page {number} reviewed text hash mismatch')
        if action == 'approve_text' and text != page['text']:
            raise ValueError(f'Review page {number} approval changed text')
        if action == 'replace_text' and text == page['text']:
            raise ValueError(f'Review page {number} replacement did not change text')
        if action == 'blank_page' and (text != '' or excluded):
            raise ValueError(f'Review page {number} visually blank decision cannot contain text or exclusions')
        if not isinstance(decision.get('reviewer'), str) or not decision['reviewer'].strip():
            raise ValueError(f'Review page {number} needs reviewer identity')
        try:
            reviewed_at = datetime.fromisoformat(decision['reviewed_at'].replace('Z', '+00:00'))
            if reviewed_at.tzinfo is None:
                raise ValueError()
        except (KeyError, AttributeError, ValueError):
            raise ValueError(f'Review page {number} needs timezone-aware review time')
        visual = decision.get('visual_evidence_path')
        if not isinstance(visual, str):
            raise ValueError(f'Review page {number} needs source-page visual evidence')
        visual_path = Path(visual)
        if not visual_path.is_absolute():
            visual_path = (Path(relative_path_root) if relative_path_root is not None else evidence_root) / visual_path
        visual_path = visual_path.resolve()
        if not visual_path.is_relative_to(evidence_root) or not visual_path.is_file():
            raise ValueError(f'Review page {number} visual evidence unavailable')
        visual_bytes = visual_path.read_bytes()
        if (not visual_bytes.startswith(b'\x89PNG\r\n\x1a\n') or
                hashlib.sha256(visual_bytes).hexdigest() != decision.get('visual_evidence_sha256')):
            raise ValueError(f'Review page {number} visual evidence hash mismatch')
        reviewed[number].update(
            text=text, text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
            status='ready' if text.strip() else 'blank',
            method='pdf_text' if action == 'blank_page' else 'vision_ocr',
            review_decision=action,
        )
        promoted += 1
    return [reviewed[int(page['page'])] for page in pages], promoted
