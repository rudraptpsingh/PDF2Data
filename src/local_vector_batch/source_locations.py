"""Export physical PDF locations without requiring usable extracted text.

Reads page dictionaries, not page content streams. Cached extraction/quality is
optional, hash-bound provenance and is never reclassified as model/legal review.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import tempfile

SCHEMA = 'pdf2data.source-location.v1'
COORDINATES = 'normalized_top_left_declared_crop_box_after_page_rotation'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(raw):
    return sha256(raw.encode('utf-8') if isinstance(raw, str) else raw).hexdigest()


def normalized_bbox(value):
    if (len(value) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in value)
            or not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1)):
        raise ValueError('normalized bbox must be finite x0 y0 x1 y1 within 0..1, with positive area')
    return [float(n) for n in value]


def _cached(package, pdf_sha, page_count):
    if package is None:
        return {}, {}, None
    package = Path(package).resolve(strict=True)
    extraction = package / 'extraction' if (package / 'l1-package.json').is_file() else package
    manifest_path = extraction / 'evidence-package.json'
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get('document', {}).get('pdf_sha256') != pdf_sha:
        raise ValueError('cached extraction belongs to another original PDF')
    page_path = extraction / 'pages.jsonl'
    page_raw = page_path.read_bytes()
    page_digest = _digest(page_raw)
    if page_digest != manifest.get('files', {}).get('pages.jsonl', {}).get('sha256'):
        raise ValueError('cached pages artifact hash mismatch')
    pages = {}
    for ordinal, line in enumerate(page_raw.decode('utf-8').splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        number, text = row.get('page'), row.get('text')
        if (type(number) is not int or not 1 <= number <= page_count or number in pages
                or not isinstance(text, str) or row.get('text_sha256') != _digest(text)):
            raise ValueError('invalid cached physical page, Unicode text or text hash')
        pages[number] = {'text': text, 'text_sha256': row['text_sha256'],
                         'artifact_path': str(page_path), 'artifact_sha256': page_digest,
                         'record_index': ordinal, 'text_field': 'text'}
    quality = {}
    l1_path = package / 'l1-package.json'
    if l1_path.is_file():
        l1 = json.loads(l1_path.read_bytes())
        raw = (package / 'quality.json').read_bytes()
        quality_digest = _digest(raw)
        if l1.get('artifacts', {}).get('original.pdf') != pdf_sha or l1.get('artifacts', {}).get('quality.json') != quality_digest:
            raise ValueError('cached original/quality artifact hash mismatch')
        observed = json.loads(raw)
        if observed.get('source_pdf_sha256') != pdf_sha:
            raise ValueError('cached quality source identity mismatch')
        for row in observed.get('results', []):
            number = row.get('page')
            if type(number) is not int or not 1 <= number <= page_count or number in quality:
                raise ValueError('invalid cached quality physical page')
            if row.get('source_pdf_sha256', pdf_sha) != pdf_sha:
                raise ValueError('cached quality physical source identity mismatch')
            if number in pages and row.get('page_text_sha256') != pages[number]['text_sha256']:
                raise ValueError('cached quality text identity mismatch')
            quality[number] = {'outcome': row.get('outcome'), 'observation': row,
                               'artifact_path': str(package / 'quality.json'), 'artifact_sha256': quality_digest,
                               'basis': 'cached_observation_not_fresh_quality_reproof'}
    return pages, quality, {'path': str(manifest_path), 'sha256': _digest(manifest_raw),
                            'scope': 'source_identity_and_selected_cached_artifacts_only_not_full_package_verification'}


def export_source_locations(source_pdf: Path, output: Path, *, package: Path | None = None,
                            bbox=(0, 0, 1, 1)):
    """One JSONL record per original physical page; no OCR/model/extraction call."""
    from pypdf import PdfReader
    bbox = normalized_bbox(bbox)
    source_pdf, output = Path(source_pdf).resolve(strict=True), Path(output).resolve()
    if output.exists() or output == source_pdf or (package and output.is_relative_to(Path(package).resolve())):
        raise ValueError('use a new output outside the immutable PDF/package')
    original_stat = source_pdf.stat()
    original = source_pdf.read_bytes()
    source_sha = _digest(original)
    reader = PdfReader(BytesIO(original))
    if reader.is_encrypted:
        raise ValueError('encrypted PDF requires an explicit decrypted source; no page is silently omitted')
    pages, quality, context = _cached(package, source_sha, len(reader.pages))
    records = []
    for number, page in enumerate(reader.pages, 1):
        media = [float(n) for n in page.mediabox]
        raw_crop = page.get('/CropBox')
        crop = [float(n) for n in raw_crop] if raw_crop is not None else list(media)
        rotation = page.get('/Rotate', 0)
        unit = float(page.get('/UserUnit', 1))
        if (len(media) != 4 or len(crop) != 4 or any(not math.isfinite(n) for n in media + crop)
                or crop[2] <= crop[0] or crop[3] <= crop[1] or not math.isfinite(unit) or unit <= 0
                or not isinstance(rotation, int) or rotation % 90):
            raise ValueError(f'unsupported source geometry on physical page {number}; no source location invented')
        width, height = (crop[2] - crop[0]) * unit, (crop[3] - crop[1]) * unit
        if rotation % 180:
            width, height = height, width
        descriptor = {'schema_version': SCHEMA, 'source_pdf_sha256': source_sha,
                      'physical_page': number, 'coordinate_space': COORDINATES, 'bbox_normalized': bbox}
        reading = pages.get(number)
        records.append({**descriptor, 'source_location_id': _digest(_json(descriptor)),
            'original_pdf_path': str(source_pdf), 'original_pdf_sha256': source_sha,
            'geometry': {'media_box_raw_pdf_user_space': media, 'crop_box_raw_pdf_user_space': crop if raw_crop is not None else None,
                         'declared_crop_box_raw_pdf_user_space': crop, 'raw_rotation_degrees': int(rotation),
                         'effective_rotation_degrees': int(rotation % 360), 'user_unit_points': unit,
                         'declared_rotated_crop_width_points': width, 'declared_rotated_crop_height_points': height,
                         'raw_coordinate_space': 'unrotated_PDF_user_space_bottom_left',
                         'renderer_clipping_or_raster_geometry_verified': False},
            'availability': 'text_available' if reading and reading['text'] else 'source_only',
            'cached_reading': reading, 'cached_quality': quality.get(number), 'cached_package': context,
            'source_review': {'status': 'not_run', 'model_output_is_candidate_only': True,
                              'model_review_is_source_quality_approval': False},
            'source_quality_approval_granted': False, 'legal_answer_eligible': False, 'publication_eligible': False})
    if source_pdf.stat() != original_stat:
        raise ValueError('original PDF changed during location export')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pdf2data-source-locations-', dir=output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for record in records:
                stream.write(_json(record) + '\n')
        os.link(name, output)  # exclusive publication; preserve immutable outputs
    finally:
        Path(name).unlink(missing_ok=True)
    return {'schema_version': SCHEMA, 'pages': len(records), 'withheld_pages': 0,
            'source_only_pages': sum(r['availability'] == 'source_only' for r in records),
            'cached_quality_held_pages': sum(r['cached_quality'] is not None and r['cached_quality']['outcome'] != 'auto_accepted' for r in records),
            'source_pdf_sha256': source_sha, 'output': str(output), 'sha256': _digest(output.read_bytes()),
            'content_streams_extracted': False, 'model_calls': 0, 'source_quality_approval_granted': False,
            'legal_answer_eligible': False, 'publication_eligible': False}
