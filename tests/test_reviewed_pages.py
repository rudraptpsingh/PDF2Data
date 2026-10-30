import hashlib
import json

import pytest

from local_vector_batch.reviewed_pages import apply_reviewed_page_decisions


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_complete_review_is_source_bound_and_does_not_change_parser_pages(tmp_path):
    pdf_sha = 'a' * 64
    original = 'OCR wr0ng'
    corrected = 'OCR wrong'
    pages = [{'page': 1, 'text': original, 'text_sha256': sha(original),
              'status': 'review_required', 'method': 'rapidocr'},
             {'page': 2, 'text': 'held', 'text_sha256': sha('held'),
              'status': 'review_required', 'method': 'rapidocr'}]
    image = tmp_path / 'p1.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    decision = {'source_pdf_sha256': pdf_sha, 'page': 1, 'original_text_sha256': sha(original),
                'decision': 'replace_text', 'review_scope': 'complete_recoverable_text',
                'review_basis': 'Compared every recoverable word with original page render.',
                'excluded_regions': [], 'reviewed_text': corrected,
                'reviewed_text_sha256': sha(corrected), 'reviewer': 'source reviewer',
                'reviewed_at': '2026-10-28T12:00:00Z', 'visual_evidence_path': str(image),
                'visual_evidence_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
    receipt = tmp_path / 'review.jsonl'
    receipt.write_text(json.dumps(decision) + '\n')
    updated, count = apply_reviewed_page_decisions(pages, pdf_sha, receipt, tmp_path)
    assert count == 1
    assert updated[0]['text'] == corrected
    assert updated[0]['method'] == 'vision_ocr'
    assert updated[1] == pages[1]
    assert pages[0]['text'] == original
    for field, replacement in [('original_text_sha256', 'b' * 64),
                               ('reviewed_text_sha256', 'c' * 64),
                               ('visual_evidence_sha256', 'd' * 64),
                               ('review_scope', 'partial')]:
        receipt.write_text(json.dumps({**decision, field: replacement}) + '\n')
        with pytest.raises(ValueError):
            apply_reviewed_page_decisions(pages, pdf_sha, receipt, tmp_path)


def test_blank_page_needs_explicit_visual_blank_decision(tmp_path):
    pdf_sha = 'a' * 64
    image = tmp_path / 'blank.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    pages = [{'page': 1, 'text': '', 'text_sha256': sha(''),
              'status': 'review_required', 'method': 'pdf_text'}]
    decision = {'source_pdf_sha256': pdf_sha, 'page': 1, 'original_text_sha256': sha(''),
                'decision': 'blank_page', 'review_scope': 'visually_blank',
                'review_basis': 'Verified original PDF render has no visible content.',
                'excluded_regions': [], 'reviewed_text': '', 'reviewed_text_sha256': sha(''),
                'reviewer': 'source reviewer', 'reviewed_at': '2026-10-28T12:00:00Z',
                'visual_evidence_path': str(image),
                'visual_evidence_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
    receipt = tmp_path / 'blank.jsonl'
    receipt.write_text(json.dumps(decision) + '\n')
    updated, count = apply_reviewed_page_decisions(pages, pdf_sha, receipt, tmp_path)
    assert count == 1 and updated[0]['status'] == 'blank' and updated[0]['method'] == 'pdf_text'
    receipt.write_text(json.dumps({**decision, 'reviewed_text': 'fabricated'}) + '\n')
    with pytest.raises(ValueError):
        apply_reviewed_page_decisions(pages, pdf_sha, receipt, tmp_path)
