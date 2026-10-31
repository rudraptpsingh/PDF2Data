import hashlib
import json
from pathlib import Path
import pytest
from local_vector_batch.page_map import build_page_map


def fixture(tmp_path):
    def write(name, data):
        p = tmp_path / name
        p.write_text(json.dumps(data))
        return p
    sha = lambda b: hashlib.sha256(b).hexdigest()
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'original fixture bytes; not parsed by mapping')
    image = tmp_path / 'page.png'
    image.write_bytes(b'original raster bytes; preserved without transformation')
    rid = 'source:p1:r0'
    text = '</script><script>alert("source text")</script>'
    block = dict(region_id=rid, kind='text', scope='body', bbox=[1, 1, 9, 9], text=text)
    page = dict(page=1, text=text, text_sha256=sha(text.encode()), blocks=[block])
    document = dict(source_pdf_path=str(source), source_pdf_sha256=sha(source.read_bytes()), pages=[page])
    layout = dict(schema_version='pdf2data.region-layout.v1', documents=[dict(source_pdf_sha256=document['source_pdf_sha256'], pages=[dict(page=1, source_pdf_sha256=document['source_pdf_sha256'], source_image=str(image), source_image_sha256=sha(image.read_bytes()), page_size_points=[10, 10], regions=[dict(id=rid)])])])
    return write('document.json', document), write('layout.json', layout), document, layout, source, image


def test_map_conserves_source_and_text_without_executable_source_markup(tmp_path):
    doc, layout, d, _, source, image = fixture(tmp_path)
    out = tmp_path / 'map'
    result = build_page_map(doc, layout, out)
    assert result['pages_mapped'] == 1 and result['blocks_mapped'] == 1
    assert (out / 'original.pdf').read_bytes() == source.read_bytes()
    assert (out / 'page-1.png').read_bytes() == image.read_bytes()
    mapped = json.loads((out / 'page-map.json').read_text())
    assert mapped['pages'][0]['text'] == d['pages'][0]['text']
    assert mapped['publication_eligible'] is False
    assert '</script><script>alert' not in (out / 'index.html').read_text()


@pytest.mark.parametrize('corruption', ['source', 'raster', 'page', 'region', 'text', 'bbox'])
def test_changed_source_or_unbound_output_fails_before_writing(tmp_path, corruption):
    doc, layout, d, l, source, image = fixture(tmp_path)
    if corruption == 'source':source.write_bytes(b'changed')
    if corruption == 'raster':image.write_bytes(b'changed')
    if corruption == 'page':d['pages'][0]['page'] = 2
    if corruption == 'region':d['pages'][0]['blocks'][0]['region_id'] = 'missing'
    if corruption == 'text':d['pages'][0]['text'] = 'changed'
    if corruption == 'bbox':d['pages'][0]['blocks'][0]['bbox'] = [1, 1, 20, 20]
    doc.write_text(json.dumps(d))
    with pytest.raises(ValueError):build_page_map(doc, layout, tmp_path / 'map')
    assert not (tmp_path / 'map').exists()


def test_invalid_review_edges_cannot_create_fabricated_source_anchors(tmp_path):
    doc, layout, *_ = fixture(tmp_path)
    notes = tmp_path / 'notes.json'
    notes.write_text(json.dumps(dict(relationships=[dict(id='R1', **{'from':['missing'], 'to':['source:p1:r0']})])))
    with pytest.raises(ValueError, match='absent block'):
        build_page_map(doc, layout, tmp_path / 'map', notes)
    assert not (tmp_path / 'map').exists()


def test_merged_cells_retain_values_and_spans(tmp_path):
    doc, layout, d, *_ = fixture(tmp_path)
    block = d['pages'][0]['blocks'][0]
    block.update(kind='table', selected_table_id='source:p1:r0:table')
    d['tables'] = [dict(id=block['selected_table_id'], page=1, cells=[['A', None], ['1', '2']],
                         cell_spans=[dict(row=0,column=0,row_span=1,column_span=2,text='A',role_candidate='header')])]
    doc.write_text(json.dumps(d))
    build_page_map(doc, layout, tmp_path / 'map')
    table = json.loads((tmp_path / 'map/page-map.json').read_text())['pages'][0]['blocks'][0]['tables'][0]
    assert table['cells'][0]['column_span'] == 2
    assert [c['text'] for c in table['cells']] == ['A', '1', '2']
