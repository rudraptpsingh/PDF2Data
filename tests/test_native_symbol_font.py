from types import SimpleNamespace
from local_vector_batch import native_symbol_font as m


class Obj(dict):
    def get_object(self):
        return self


class Stream:
    def __init__(self, data):
        self.data = data
    def get_object(self):
        return self
    def get_data(self):
        return self.data


def fixture(monkeypatch, *, font_name='SymbolMT', font_data=b'font', cmap=b'cmap', x=198.8, extra=False):
    monkeypatch.setattr(m, '_FONT', m._digest(b'font'))
    monkeypatch.setattr(m, '_CMAP', m._digest(b'cmap'))
    font = Obj({'/BaseFont': '/SymbolMT', '/Encoding': '/Identity-H',
                '/ToUnicode': Stream(cmap), '/DescendantFonts': [Obj({'/FontDescriptor': Obj({'/FontFile2': Stream(font_data)})})]})
    char = {'text': '\uf044', 'fontname': font_name, 'x0': x, 'top': 133.892, 'x1': 206.144, 'bottom': 145.892}
    native = SimpleNamespace(chars=[char] * (2 if extra else 1))
    class Page:
        def extract_text(self, visitor_text):
            visitor_text('\uf044', None, None, font, 12)
    return native, Page()


def test_exact_reviewed_page_and_font_repaired_with_anchor(monkeypatch):
    native, page = fixture(monkeypatch)
    text = 'before Supplementary ECR = (\uf044ECR) after'
    out, proof = m.repair_reviewed_symbol(m._SOURCE, 114, text, native, page)
    assert out == text.replace('\uf044', 'Δ')
    assert proof[0]['original_quote'] == text[proof[0]['selected_start']:proof[0]['selected_end']]
    assert proof[0]['source_pdf_sha256'] == m._SOURCE
    assert proof[0]['legal_review'] == 'not_run'


def test_wrong_source_page_font_context_and_ambiguity_fail_closed(monkeypatch):
    text = 'Supplementary ECR = (\uf044ECR)'
    native, page = fixture(monkeypatch)
    for sha, number, value in [('0'*64, 114, text), (m._SOURCE, 113, text),
                               (m._SOURCE, 114, text + text)]:
        assert m.repair_reviewed_symbol(sha, number, value, native, page) == (value, [])
    for opts in ({'font_name': 'Different'}, {'font_data': b'other'}, {'cmap': b'other'}, {'x': 199}, {'extra': True}):
        native, page = fixture(monkeypatch, **opts)
        assert m.repair_reviewed_symbol(m._SOURCE, 114, text, native, page) == (text, [])


def test_quality_clears_only_replayed_exact_finding(monkeypatch):
    native, page = fixture(monkeypatch)
    before = 'Supplementary ECR = (\uf044ECR)'
    text, proof = m.repair_reviewed_symbol(m._SOURCE, 114, before, native, page)
    quality = m.quality_for_reviewed_symbol(text, before, m._SOURCE, 114, native, page, proof)
    assert quality['needs_ocr'] is False
    assert quality['glyph_audit']['source_verified_issues'][0]['literal'] == 'ΔECR'
    for bad in ([], [{**proof[0], 'selected_start': 0}], [{**proof[0], 'font_file_sha256': '0'*64}]):
        assert m.quality_for_reviewed_symbol(text, before, m._SOURCE, 114, native, page, bad)['needs_ocr']
    assert m.quality_for_reviewed_symbol(text, before, '0'*64, 114, native, page, proof)['needs_ocr']
    assert m.quality_for_reviewed_symbol(text, before, m._SOURCE, 115, native, page, proof)['needs_ocr']
    assert m.quality_for_reviewed_symbol(text + ' Pаy', before + ' Pаy', m._SOURCE, 114, native, page, proof)['needs_ocr']
    assert m.quality_for_reviewed_symbol(text + ' αECR', before + ' αECR', m._SOURCE, 114, native, page, proof)['needs_ocr']


def test_quality_gate_fails_closed_when_original_unavailable():
    page = {'page': 114, 'text': 'plain', 'native_symbol_repairs': [{'selected_start': 0}]}
    assert m.verify_symbol_page_quality(page, '/does/not/exist', m._SOURCE)['needs_ocr']


def test_quality_gate_does_not_trust_serialized_exception_without_original():
    import hashlib
    from local_vector_batch.quality_gate import assess_page
    text = 'Some ΔECR text'
    page = {'page': 114, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'method': 'pdf_text', 'selected_quality': {'needs_ocr': False},
            'native_symbol_repairs': [{'selected_start': 5}]}
    result = assess_page(page, source_pdf_sha256=m._SOURCE, package_pdf_sha256=m._SOURCE,
                         source_pdf_path='/does/not/exist')
    assert result['components']['text_content']['state'] == 'fail'
