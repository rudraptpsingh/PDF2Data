import json

from local_vector_batch.review_audit import audit_review_pages


def test_review_audit_classifies_without_promoting_any_page(tmp_path):
    package = tmp_path / 'abc'
    package.mkdir()
    report = {
        'source_pdf_sha256': 'a' * 64,
        'source_url': 'https://cercind.gov.in/test.pdf',
        'issues': [
            {'page': 1, 'kind': 'missing_text'},
            {'page': 2, 'kind': 'ocr_used'},
            {'page': 2, 'kind': 'ocr_layout_review'},
            {'page': 3, 'kind': 'low_confidence_ocr'},
        ],
    }
    (package / 'report.json').write_text(json.dumps(report))
    pages = [
        {'page': 1, 'status': 'review_required', 'text': '', 'method': 'pdf_text', 'chars': 0},
        {'page': 2, 'status': 'review_required', 'text': 'table text', 'method': 'vision', 'chars': 10},
        {'page': 3, 'status': 'review_required', 'text': 'possibly wrong', 'method': 'vision',
         'chars': 14, 'selected_quality': {'needs_ocr': False}},
        {'page': 4, 'status': 'ready', 'text': 'verified native', 'method': 'pdf_text', 'chars': 15},
    ]
    (package / 'pages.jsonl').write_text(''.join(json.dumps(page) + '\n' for page in pages))
    details = tmp_path / 'queue.jsonl'
    result = audit_review_pages(tmp_path, details)
    assert result['review_pages'] == 3
    assert result['categories'] == {'missing_text': 1, 'visual_ocr_review': 1, 'uncertain_ocr': 1}
    assert result['next_actions']['retry_ocr_then_review'] == 1
    queue = [json.loads(line) for line in details.read_text().splitlines()]
    assert len(queue) == 3 and all(row['status'] == 'review_required' for row in queue)
    assert queue[0]['source_pdf_sha256'] == 'a' * 64
