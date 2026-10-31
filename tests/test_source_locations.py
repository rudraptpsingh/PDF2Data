"""Geometry/provenance controls, not visual reading or legal approval."""
import hashlib
import json
from pathlib import Path
import pytest
from local_vector_batch.source_locations import export_source_locations
from local_vector_batch.cli import main


def digest(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def source(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject, NameObject, NumberObject
    path = tmp_path / 'original.pdf'
    writer = PdfWriter()
    first = writer.add_blank_page(width=600, height=800)
    first[NameObject('/CropBox')] = RectangleObject([10, 20, 510, 720])
    first[NameObject('/Rotate')] = NumberObject(90)
    first[NameObject('/UserUnit')] = NumberObject(2)
    writer.add_blank_page(width=400, height=500)
    writer.add_blank_page(width=400, height=500)
    writer.write(path)
    return path


def package(tmp_path, original, blank_text=''):
    root = tmp_path / 'package';extraction = root / 'extraction';extraction.mkdir(parents=True)
    # Page 2 is present with empty text; page 3 is absent from the cache entirely.
    text = 'वित्तीय वर्ष 2024-25 से 2028-29 — ₹ 42.52 🧾'
    rows = [{'page': 1, 'text': text, 'text_sha256': digest(text)},
            {'page': 2, 'text': blank_text, 'text_sha256': digest(blank_text)}]
    page_raw = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
    (extraction / 'pages.jsonl').write_bytes(page_raw.encode('utf-8'))
    pdf_sha = digest(original.read_bytes())
    (extraction / 'evidence-package.json').write_text(json.dumps({'document': {'pdf_sha256': pdf_sha},
        'files': {'pages.jsonl': {'sha256': digest(page_raw)}}}))
    quality = {'source_pdf_sha256': pdf_sha, 'results': [
        {'page': row['page'], 'page_text_sha256': row['text_sha256'], 'outcome': 'repair_required'} for row in rows]}
    raw = json.dumps(quality).encode();(root / 'quality.json').write_bytes(raw)
    (root / 'l1-package.json').write_text(json.dumps({'artifacts': {'original.pdf': pdf_sha, 'quality.json': digest(raw)}}))
    return root, text


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_held_empty_and_missing_extracted_pages_all_retained_with_exact_unicode(tmp_path):
    original = source(tmp_path);cached, text = package(tmp_path, original)
    before = original.read_bytes();out = tmp_path / 'locations.jsonl'
    result = export_source_locations(original, out, package=cached)
    rows = read(out)
    assert result['pages'] == 3 and result['withheld_pages'] == 0 and result['source_only_pages'] == 2
    assert result['cached_quality_held_pages'] == 2
    assert [row['physical_page'] for row in rows] == [1, 2, 3]
    assert rows[0]['cached_reading']['text'] == text and text in out.read_text(encoding='utf-8')
    assert rows[1]['cached_reading']['text'] == '' and rows[2]['cached_reading'] is None
    assert all(row['source_pdf_sha256'] == digest(before) for row in rows)
    assert all(row['source_review']['model_output_is_candidate_only'] is True and row['source_review']['status'] == 'not_run' for row in rows)
    assert all(row['source_quality_approval_granted'] is False and row['legal_answer_eligible'] is False for row in rows)
    assert original.read_bytes() == before


def test_raw_media_crop_rotation_and_normalized_region_remain_distinct(tmp_path):
    original = source(tmp_path);out = tmp_path / 'regions.jsonl'
    export_source_locations(original, out, bbox=[.1, .2, .8, .9])
    rows = read(out);geometry = rows[0]['geometry']
    assert geometry['media_box_raw_pdf_user_space'] == [0, 0, 600, 800]
    assert geometry['crop_box_raw_pdf_user_space'] == [10, 20, 510, 720]
    assert geometry['raw_rotation_degrees'] == 90 and geometry['user_unit_points'] == 2
    assert geometry['declared_rotated_crop_width_points'] == 1400 and geometry['declared_rotated_crop_height_points'] == 1000
    assert rows[1]['geometry']['crop_box_raw_pdf_user_space'] is None
    assert rows[0]['bbox_normalized'] == [.1, .2, .8, .9]
    assert rows[0]['coordinate_space'] == 'normalized_top_left_declared_crop_box_after_page_rotation'
    assert geometry['renderer_clipping_or_raster_geometry_verified'] is False
    another = tmp_path / 'same-region.jsonl';export_source_locations(original, another, bbox=[.1, .2, .8, .9])
    assert [r['source_location_id'] for r in rows] == [r['source_location_id'] for r in read(another)]
    other = tmp_path / 'whole-page.jsonl';export_source_locations(original, other)
    assert rows[0]['source_location_id'] != read(other)[0]['source_location_id']
    explicit = tmp_path / 'explicit-whole-page.jsonl';export_source_locations(original, explicit, bbox=[0., 0., 1., 1.])
    assert read(other)[0]['source_location_id'] == read(explicit)[0]['source_location_id']


def test_whitespace_reading_retained_exactly_but_not_advertised_as_searchable_text(tmp_path):
    original = source(tmp_path);whitespace = ' \n\t\u00a0'
    cached, _ = package(tmp_path, original, blank_text=whitespace);out = tmp_path / 'whitespace.jsonl'
    export_source_locations(original, out, package=cached)
    row = read(out)[1]
    assert row['availability'] == 'source_only'
    assert row['cached_reading']['text'] == whitespace and row['cached_reading']['text_sha256'] == digest(whitespace)


@pytest.mark.parametrize('box', [[0, 0, 2, 1], [0, 0, 0, 1], [0, .8, 1, .2], [0, 0, float('nan'), 1], [False, 0, 1, 1], [0, 1]])
def test_invalid_bbox_never_publishes_invented_location(tmp_path, box):
    original = source(tmp_path);out = tmp_path / 'bad.jsonl'
    with pytest.raises(ValueError, match='normalized bbox'):
        export_source_locations(original, out, bbox=box)
    assert not out.exists()


def test_wrong_pdf_cached_text_hash_and_existing_output_rejected(tmp_path):
    original = source(tmp_path);cached, _ = package(tmp_path, original);out = tmp_path / 'locations.jsonl'
    export_source_locations(original, out, package=cached)
    with pytest.raises(ValueError, match='new output'):
        export_source_locations(original, out)
    path = cached / 'extraction/evidence-package.json';manifest = json.loads(path.read_text())
    manifest['document']['pdf_sha256'] = '0' * 64;path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='another original PDF'):
        export_source_locations(original, tmp_path / 'wrong-source.jsonl', package=cached)
    manifest['document']['pdf_sha256'] = digest(original.read_bytes());path.write_text(json.dumps(manifest))
    (cached / 'extraction/pages.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='artifact hash'):
        export_source_locations(original, tmp_path / 'wrong-text.jsonl', package=cached)


def test_cli_exports_textless_original_without_extraction_or_model(tmp_path, capsys):
    original = source(tmp_path);out = tmp_path / 'cli.jsonl'
    assert main(['source-locations', '--input', str(original), '--output', str(out), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['pages'] == result['source_only_pages'] == 3
    assert result['model_calls'] == 0 and result['content_streams_extracted'] is False
