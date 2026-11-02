"""Generated PDF software control; not a real-source accuracy or decoding case."""
import json

from pypdf import PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject, ArrayObject, NumberObject

from local_vector_batch.pdf_ingest import extract_pdf


def source_pdf(tmp_path):
    source = tmp_path / 'software-control.pdf'
    writer = PdfWriter()
    page = writer.add_blank_page(612, 842)
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/BCDFEE+KrutiDev010')}))
    descriptor = DictionaryObject({NameObject('/Type'): NameObject('/FontDescriptor'),
        NameObject('/FontName'): NameObject('/BCDFEE+KrutiDev010'),
        NameObject('/Flags'): NumberObject(32),
        NameObject('/FontBBox'): ArrayObject([NumberObject(x) for x in (0, -200, 1000, 900)]),
        NameObject('/Ascent'): NumberObject(800), NameObject('/Descent'): NumberObject(-200),
        NameObject('/CapHeight'): NumberObject(700), NameObject('/ItalicAngle'): NumberObject(0),
        NameObject('/StemV'): NumberObject(80)})
    font.get_object()[NameObject('/FontDescriptor')] = writer._add_object(descriptor)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): font})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 50 700 Td (Software control only: Hkkjr 2026. Native character codes do not establish a correct Hindi glyph mapping.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    writer.write(source)
    return source


def test_ingest_metadata_retains_legacy_font_review_even_when_unicode_is_latin(tmp_path):
    source = source_pdf(tmp_path)
    output = tmp_path / 'extracted'
    extract_pdf(source, output, ocr='off', tables=False)
    rows = [json.loads(line) for line in (output / 'pages.jsonl').read_text().splitlines()]
    assert len(rows) == 1
    observed = rows[0]
    assert observed['text_script'] == 'latin'
    assert 'Hkkjr 2026' in observed['text']
    assert observed['script_verification']['required_scripts'] == ['devanagari']
    assert observed['script_verification']['state'] == 'unverified'
    assert observed['script_verification']['source_font_evidence']['decoded_text_modified'] is False
    assert observed['status'] == 'review_required'


def test_unavailable_font_inventory_is_review_required_without_text_rewrite(tmp_path, monkeypatch):
    from local_vector_batch import text_script
    source = source_pdf(tmp_path)
    def unavailable(*_):
        raise OSError('software injected font inventory failure')
    monkeypatch.setattr(text_script, 'native_font_script_evidence', unavailable)
    output = tmp_path / 'failed-inventory'
    extract_pdf(source, output, ocr='off', tables=False)
    page = json.loads((output / 'pages.jsonl').read_text().splitlines()[0])
    assert 'Hkkjr 2026' in page['text']
    assert page['script_verification']['state'] == 'unverified'
    assert page['script_verification']['reason'] == 'native_font_inventory_unavailable'
    assert page['status'] == 'review_required'
