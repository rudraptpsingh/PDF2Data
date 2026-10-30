import hashlib
from pathlib import Path

import pytest

pdfplumber = pytest.importorskip('pdfplumber')
from pypdf import PdfWriter
from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.corroboration import load_source_page, suggest_related_readings


class FakePage:
    objects = {'char': [1]}
    images = []
    def __init__(self, text): self.text = text
    def extract_text(self): return self.text
    def find_tables(self): return []


class FakePdf:
    def __init__(self, text): self.pages = [FakePage(text)]
    def __enter__(self): return self
    def __exit__(self, *_): pass


def create_doc(tmp_path, monkeypatch, name, text, width):
    pdf = tmp_path / (name + '.pdf')
    writer = PdfWriter(); writer.add_blank_page(width=width, height=800); writer.write(pdf)
    monkeypatch.setattr(pdfplumber, 'open', lambda _: FakePdf(text))
    package = tmp_path / name
    extract_pdf(pdf, package, title=name, source_url=f'https://example.test/{name}.pdf', ocr='off')
    return load_source_page(package, pdf, 1), package, pdf


def span(source, quote):
    page = source.page
    return {'page_id': page['id'], 'page': page['page'], 'page_text_sha256': page['text_sha256'],
            'char_start': page['text'].index(quote), 'char_length': len(quote), 'quote': quote}


def item(source, ref, name, category='order'):
    return {'reading_page': source, 'reading_span': span(source, name), 'reference_page': source,
            'reference_span': span(source, ref), 'category': category}


def test_related_readings_keep_ambiguous_same_name_documents_separate(tmp_path, monkeypatch):
    target, _, _ = create_doc(tmp_path, monkeypatch, 'target', 'Petition 42/MP/2019\nPetitioner: A... Power Limited', 600)
    a, _, _ = create_doc(tmp_path, monkeypatch, 'a', 'Petition 42/MP/2019\nPetitioner: Apex Power Limited', 601)
    b, _, _ = create_doc(tmp_path, monkeypatch, 'b', 'Petition 42/MP/2019\nPetitioner: Apex Power Limited', 602)
    unrelated, _, _ = create_doc(tmp_path, monkeypatch, 'other', 'Petition 88/MP/2019\nPetitioner: Apex Power Limited', 603)
    result = suggest_related_readings(target_page=target, obscured_span=span(target, 'A... Power Limited'),
        target_reference_page=target, target_reference=span(target, 'Petition 42/MP/2019'), category='order',
        related=[item(a, 'Petition 42/MP/2019', 'Apex Power Limited'),
                 item(b, 'Petition 42/MP/2019', 'Apex Power Limited'),
                 item(unrelated, 'Petition 88/MP/2019', 'Apex Power Limited')])
    assert result['candidate_count'] == 2 and result['multiple_possible_sources']
    assert len({row['reading']['source_pdf_sha256'] for row in result['candidates']}) == 2
    assert not result['usable_for_answer'] and result['status'] == 'review_required'
    assert all(row['reading']['quote'] == 'Apex Power Limited' for row in result['candidates'])
    assert all('Petitioner:' in row['reading']['context']['quote'] for row in result['candidates'])
    assert result['target_reference']['line_context']['quote'] == 'Petition 42/MP/2019'
    assert target.page['text'].endswith('A... Power Limited')


def test_category_alone_and_shared_name_do_not_form_link(tmp_path, monkeypatch):
    target, _, _ = create_doc(tmp_path, monkeypatch, 'target', 'Petition 42/MP/2019\nA... Power Limited', 610)
    other, _, _ = create_doc(tmp_path, monkeypatch, 'other', 'Petition 88/MP/2019\nApex Power Limited', 611)
    result = suggest_related_readings(target_page=target, obscured_span=span(target, 'A... Power Limited'),
        target_reference_page=target, target_reference=span(target, 'Petition 42/MP/2019'), category='order',
        related=[item(other, 'Petition 88/MP/2019', 'Apex Power Limited')])
    assert result['candidate_count'] == 0 and not result['usable_for_answer']


def test_numeric_or_text_reading_is_a_candidate_not_a_source_repair(tmp_path, monkeypatch):
    target, _, _ = create_doc(tmp_path, monkeypatch, 'target', 'Tender SECI/ESS-IV/2026\nSecurity: [illegible]', 612)
    related, _, _ = create_doc(tmp_path, monkeypatch, 'notice', 'Tender SECI/ESS-IV/2026\nSecurity: INR 10 lakh', 613)
    result = suggest_related_readings(target_page=target, obscured_span=span(target, '[illegible]'),
        target_reference_page=target, target_reference=span(target, 'Tender SECI/ESS-IV/2026'), category='tender',
        related=[item(related, 'Tender SECI/ESS-IV/2026', 'INR 10 lakh', 'tender')])
    assert result['candidate_count'] == 1 and result['candidates'][0]['reading']['quote'] == 'INR 10 lakh'
    assert result['usable_for_answer'] is False and target.page['text'].endswith('[illegible]')


def test_source_and_text_tampering_rejected(tmp_path, monkeypatch):
    source, package, pdf = create_doc(tmp_path, monkeypatch, 'target', 'Petition 42/MP/2019\nA... Power Limited', 620)
    pdf.write_bytes(b'not the original')
    with pytest.raises(ValueError, match='original PDF hash mismatch'):
        load_source_page(package, pdf, 1)
    ref = span(source, 'Petition 42/MP/2019'); ref['quote'] = 'Petition 88/MP/2019'
    with pytest.raises(ValueError, match='quote does not match'):
        suggest_related_readings(target_page=source, obscured_span=span(source, 'A... Power Limited'),
            target_reference_page=source, target_reference=ref, category='order', related=[])


def test_reference_must_not_be_substring_of_a_different_matter_number(tmp_path, monkeypatch):
    target, _, _ = create_doc(tmp_path, monkeypatch, 'target', 'Petition 42/MP/2019\nA... Power Limited', 630)
    other, _, _ = create_doc(tmp_path, monkeypatch, 'other', 'Petition 142/MP/2019\nApex Power Limited', 631)
    with pytest.raises(ValueError, match='longer identifier'):
        suggest_related_readings(target_page=target, obscured_span=span(target, 'A... Power Limited'),
            target_reference_page=target, target_reference=span(target, '42/MP/2019'), category='order',
            related=[item(other, '42/MP/2019', 'Apex Power Limited')])
