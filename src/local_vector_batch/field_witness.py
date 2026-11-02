"""Bounded source-image field readings, retained independently of native text.

A reader consensus is a correction candidate, never page or legal approval.
"""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import unicodedata

from .hashing import file_sha256
from .field_rules import _decode as decode

RULES = 'pdf2data.field-witness-rules.v1'
REQUEST = 'pdf2data.field-witness-request.v1'
READING = 'pdf2data.field-witness-reading.v1'
COMPARISON = 'pdf2data.field-witness-comparison.v1'
PROMPT = ('Transcribe all visible text exactly in its original script and reading order. '
          'Include every word in parentheses, dates, numbers, negation and qualifiers. '
          'Do not translate, summarize, infer, correct or omit text. Return only the transcription.')
SOURCE_FILES = ('original.pdf', 'source-receipt.json', 'l1-package.json', 'extraction/pages.jsonl',
                'layout.jsonl', 'extraction/tables.json', 'structure.json', 'quality.json')
FLAGS = {'source_quality_approval_granted': False, 'publication_eligible': False,
         'legal_approval': False, 'native_text_modified': False, 'whole_page_quality_changed': False}


def require(condition, message):
    if not condition: raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value): return sha256(canonical(value)).hexdigest()
def load(path): return decode(Path(path).read_bytes())
def write(path, value): Path(path).write_bytes(canonical(value) + b'\n')


def implementation():
    root = Path(__file__).parent
    return {name: file_sha256(root/name) for name in ('field_witness.py', 'field_rules.py', 'ollama_ocr_benchmark.py')}


def bounded_name(value):
    require(isinstance(value, str) and value.strip() == value and 0 < len(value) <= 128 and all(c.isalnum() or c in '._:-' for c in value), 'bounded field/region/reader identity required')
    return value


def validate_rules(rules):
    require(isinstance(rules, dict) and rules.get('schema_version') == RULES, 'unsupported field witness rules')
    bounded_name(rules.get('id')); bounded_name(rules.get('version'))
    require(type(rules.get('dpi', 300)) is int and 120 <= rules.get('dpi', 300) <= 360, 'render dpi must be 120..360')
    regions = rules.get('regions'); fields = rules.get('fields')
    require(isinstance(regions, list) and 1 <= len(regions) <= 16 and isinstance(fields, list) and 1 <= len(fields) <= 128, 'bounded regions and fields required')
    seen = set()
    for r in regions:
        require(isinstance(r, dict), 'region object required')
        rid = bounded_name(r.get('id')); require(rid not in seen, 'duplicate region ID'); seen.add(rid)
        box = r.get('bbox_pdf_points')
        require(type(r.get('page')) is int and r['page'] > 0 and isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in box), 'finite physical page rectangle required')
        require(0 <= box[0] < box[2] and 0 <= box[1] < box[3], 'invalid source rectangle')
    ids = set()
    for f in fields:
        require(isinstance(f, dict), 'field object required')
        fid = bounded_name(f.get('id')); require(fid not in ids, 'duplicate witness field ID'); ids.add(fid)
        bounded_name(f.get('native_field_id')); require(f.get('region_id') in seen, 'unknown witness region')
        if 'native_observation_id' in f: bounded_name(f['native_observation_id'])
        selector = f.get('selector'); require(isinstance(selector, dict) and selector.get('kind') in {'first_line', 'between'}, 'explicit literal selector required')
        if selector['kind'] == 'between':
            for key in ('prefix', 'suffix'):
                require(isinstance(selector.get(key), str) and 0 < len(selector[key]) <= 256, 'bounded nonempty selector literals required')
        tokens = f.get('required_tokens', [])
        require(isinstance(tokens, list) and len(tokens) <= 16 and all(isinstance(t, str) and 0 < len(t) <= 256 for t in tokens), 'bounded qualifier tokens required')
    return rules


def select(text, selector):
    if selector['kind'] == 'first_line':
        offset = 0
        for line in text.splitlines(keepends=True):
            if line.strip():
                start = offset + len(line) - len(line.lstrip()); value = line.strip()
                return [dict(value_literal=value, char_start=start, char_length=len(value))]
            offset += len(line)
        return []
    found = []; cursor = 0; prefix, suffix = selector['prefix'], selector['suffix']
    while len(found) <= 128:
        a = text.find(prefix, cursor)
        if a < 0: break
        start = a + len(prefix); end = text.find(suffix, start)
        if end < 0: break
        value = text[start:end]; left = len(value) - len(value.lstrip()); value = value.strip()
        if value: found.append(dict(value_literal=value, char_start=start+left, char_length=len(value)))
        cursor = end + len(suffix)
    return found


def normalized(value): return ' '.join(unicodedata.normalize('NFC', value).split())


def render(package, region, dpi):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(Path(package)/'original.pdf') as pdf:
        require(region['page'] <= len(pdf), 'source region page unavailable')
        page = pdf[region['page']-1]
        try:
            width, height = page.get_size(); box = region['bbox_pdf_points']
            require(page.get_rotation() == 0, 'rotated field witness requires explicit frame mapping')
            require(box[2] <= width and box[3] <= height, 'region exceeds source page')
            require(math.ceil(width*dpi/72)*math.ceil(height*dpi/72) <= 40_000_000, 'source page exceeds render pixel budget')
            image = page.render(scale=dpi/72).to_pil().convert('RGB')
        finally: page.close()
    scale = dpi/72; pixels = [int(box[0]*scale), int(box[1]*scale), min(image.width, math.ceil(box[2]*scale)), min(image.height, math.ceil(box[3]*scale))]
    crop = image.crop(pixels); require(crop.width*crop.height <= 24_000_000, 'source region exceeds pixel budget')
    buffer = BytesIO(); crop.save(buffer, format='PNG')
    return buffer.getvalue(), pixels


def derive_request(package, field_bundle, rules):
    from .legal_l1 import verify_document
    from .field_rules import verify
    validate_rules(rules); verify_document(package); verify(field_bundle)
    fields_raw = (field_bundle/'fields.json').read_bytes(); fields = decode(fields_raw)
    require(fields.get('schema_version') == 'pdf2data.configured-fields.v2', 'native fields v2 required')
    require(fields['source_pdf_sha256'] == file_sha256(package/'original.pdf') and fields['source_package_sha256'] == file_sha256(package/'l1-package.json'), 'exact native PDF/package field scope required')
    before = {n: file_sha256(package/n) for n in SOURCE_FILES}
    regions = []; images = {}
    for r in rules['regions']:
        raw, pixels = render(package, r, rules.get('dpi', 300)); name = 'region-'+str(len(regions))+'.png'; images[name] = raw
        regions.append({**r, 'image_file': name, 'image_sha256': sha256(raw).hexdigest(), 'crop_bbox_pixels': pixels, 'render_dpi': rules.get('dpi', 300)})
    mapped = {r['id']: r for r in regions}; bindings = []
    for f in rules['fields']:
        matches = [(i, o) for i, o in enumerate(fields['observations']) if o['field_id'] == f['native_field_id'] and ('native_observation_id' not in f or o['id'] == f['native_observation_id'])]
        require(len(matches) == 1, 'witness field must select one exact native observation')
        i, observation = matches[0]; region = mapped[f['region_id']]
        require(observation['source_span']['page'] == region['page'], 'native field and region physical pages differ')
        geometry = (observation.get('geometry') or {}).get('bbox')
        if geometry:
            box = region['bbox_pdf_points']; require(box[0] <= geometry[0] and box[1] <= geometry[1] and box[2] >= geometry[2] and box[3] >= geometry[3], 'region clips native field geometry')
        bindings.append({**f, 'native_observation_id': observation['id'], 'native_field_handle': {'artifact_sha256': sha256(fields_raw).hexdigest(), 'json_pointer': '/observations/'+str(i)},
                         'native_value_literal': observation['value_literal'], 'native_source_span': observation['source_span'], 'native_geometry': observation.get('geometry'), 'native_source_quality': observation.get('source_quality')})
    require(before == {n: file_sha256(package/n) for n in SOURCE_FILES}, 'source changed during region preparation')
    return dict(schema_version=REQUEST, source_pdf_sha256=fields['source_pdf_sha256'], source_package_sha256=fields['source_package_sha256'],
                native_fields_sha256=sha256(fields_raw).hexdigest(), rules_sha256=digest(rules), regions=regions, fields=bindings, prompt=PROMPT,
                source_files_sha256=before, source_geometry_verification='native_bbox_containment_when_available', **FLAGS), images


@contextmanager
def output_stage(output, forbidden):
    output = Path(output).resolve()
    require(not output.exists() and all(not output.is_relative_to(p.resolve()) and not p.resolve().is_relative_to(output) for p in forbidden), 'new output outside immutable inputs required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.field-witness-', dir=output.parent) as temporary:
        stage = Path(temporary)/'bundle'; stage.mkdir()
        yield stage
        require(not output.exists(), 'output appeared during operation'); stage.rename(output)


def seal(stage, kind, metadata):
    files = {p.name: file_sha256(p) for p in stage.iterdir() if p.is_file()}
    write(stage/'manifest.json', dict(schema_version='pdf2data.field-witness-bundle.v1', kind=kind, files=files, implementation=implementation(), metadata=metadata, **FLAGS))


def prepare(package, field_bundle, rules_path, output):
    package, field_bundle = Path(package).resolve(), Path(field_bundle).resolve(); rules = load(rules_path)
    request, images = derive_request(package, field_bundle, rules)
    with output_stage(output, [package, field_bundle, Path(rules_path)]) as stage:
        write(stage/'rules.json', rules); write(stage/'request.json', request)
        for name, raw in images.items(): (stage/name).write_bytes(raw)
        seal(stage, 'request', dict(source_package=str(package), field_bundle=str(field_bundle)))
    return dict(schema_version=REQUEST, output=str(Path(output).resolve()), regions=len(images), fields=len(request['fields']), **FLAGS)


def checked_request(bundle):
    bundle = Path(bundle).resolve(); manifest = check_files(bundle, 'request'); meta = manifest['metadata']
    expected, images = derive_request(Path(meta['source_package']), Path(meta['field_bundle']), load(bundle/'rules.json'))
    require(canonical(load(bundle/'request.json')) == canonical(expected), 'source field request differs from native re-derivation')
    for name, raw in images.items(): require((bundle/name).read_bytes() == raw, 'source image differs from original PDF rerender')
    return expected


def check_files(bundle, kind=None):
    require(bundle.is_dir() and not any(p.is_symlink() or not p.is_file() for p in bundle.iterdir()), 'witness bundle must contain only regular files')
    manifest = load(bundle/'manifest.json')
    require(manifest.get('schema_version') == 'pdf2data.field-witness-bundle.v1' and (kind is None or manifest.get('kind') == kind), 'unsupported witness bundle')
    require(manifest.get('implementation') == implementation(), 'witness implementation changed')
    require(all(manifest.get(k) is v for k, v in FLAGS.items()), 'witness cannot grant approval or modify source')
    files = manifest.get('files'); require(isinstance(files, dict) and files, 'witness files missing')
    require(set(files) == {p.name for p in bundle.iterdir() if p.is_file() and p.name != 'manifest.json'}, 'witness file coverage differs')
    for name, expected in files.items():
        require(Path(name).name == name and file_sha256(bundle/name) == expected, 'witness artifact hash differs')
    return manifest


def request_inputs(request_bundle):
    metadata = load(Path(request_bundle)/'manifest.json')['metadata']
    return [Path(request_bundle), Path(metadata['source_package']), Path(metadata['field_bundle'])]


def read_ollama(request_bundle, output, host, model, max_tokens=2048, timeout=240):
    from .ollama_ocr_benchmark import Reader
    import base64
    request_bundle = Path(request_bundle).resolve(); request = checked_request(request_bundle)
    require(type(max_tokens) is int and 128 <= max_tokens <= 4096 and type(timeout) is int and 1 <= timeout <= 600, 'bounded model token/time budgets required')
    require(isinstance(model, str) and 'cloud' not in model.lower(), 'explicit installed local vision model required')
    reader = Reader(host, model, timeout); details = reader.request('/api/show', {'model': model})
    require('vision' in details.get('capabilities', []) and not details.get('remote_host') and not details.get('remote_model'), 'local vision capability required; remote model metadata is disallowed')
    rows = []
    with output_stage(output, request_inputs(request_bundle)) as stage:
        for region in request['regions']:
            image = request_bundle/region['image_file']
            require(file_sha256(image) == region['image_sha256'], 'source image changed before inference')
            body = {'model': model, 'messages': [{'role': 'user', 'content': PROMPT, 'images': [base64.b64encode(image.read_bytes()).decode()]}],
                    'stream': False, 'think': False, 'options': {'temperature': 0, 'seed': 42, 'num_ctx': 8192, 'num_predict': max_tokens}, 'keep_alive': '2m'}
            reply = reader.request('/api/chat', body); text = reply.get('message', {}).get('content')
            require(reader.model_digest() == reader.identity['digest'], 'reader model digest changed')
            complete = reply.get('done') is True and reply.get('done_reason') == 'stop' and isinstance(text, str) and bool(text.strip())
            require(not isinstance(text, str) or len(text) <= 500000, 'source reading exceeds text budget')
            rows.append({'region_id': region['id'], 'image_sha256': region['image_sha256'], 'text': text if isinstance(text, str) else None,
                         'state': 'complete' if complete else 'incomplete', 'raw_response': reply,
                         'request_sha256': digest(body), 'source_text_provided': False})
        require(reader.model_digest() == reader.identity['digest'], 'reader identity changed after inference')
        require(canonical(checked_request(request_bundle)) == canonical(request), 'source request changed during inference')
        packet = dict(schema_version=READING, request_sha256=file_sha256(request_bundle/'request.json'), reader_id='ollama:'+model,
                      reader_fingerprint=digest({'engine': 'ollama', 'model_digest': reader.identity['digest']}),
                      reader_metadata=reader.identity, attestation='locally_executed_source_image_inference', prompt=PROMPT,
                      options=body['options'], regions=rows, source_text_provided=False, **FLAGS)
        write(stage/'reading.json', packet); seal(stage, 'reading', dict(request_bundle=str(request_bundle)))
    return dict(schema_version=READING, output=str(Path(output).resolve()), complete=sum(r['state']=='complete' for r in rows), incomplete=sum(r['state']!='complete' for r in rows), **FLAGS)


def import_reading(request_bundle, readings_path, metadata_path, output):
    """Import an independently retained external reading; execution is not attested."""
    request_bundle = Path(request_bundle).resolve(); request = checked_request(request_bundle)
    source_raw = Path(readings_path).read_bytes(); metadata_raw = Path(metadata_path).read_bytes()
    source = decode(source_raw); metadata = decode(metadata_raw)
    require(isinstance(metadata, dict) and isinstance(metadata.get('engine'), str) and metadata['engine'] and isinstance(metadata.get('version'), str) and metadata['version'], 'external reader engine/version required')
    rid = bounded_name(metadata.get('reader_id')); require(metadata.get('source_text_provided') is False, 'external reader must declare no supplied source text')
    require(isinstance(source, dict) and source.get('request_sha256') == file_sha256(request_bundle/'request.json') and isinstance(source.get('regions'), list), 'external reading request hash differs')
    known = {r['id']: r for r in request['regions']}; seen = set(); rows = []
    require(len(source['regions']) <= len(request['regions']), 'too many external regions')
    for r in source['regions']:
        require(isinstance(r, dict), 'external region object required')
        ident = r.get('region_id'); require(ident in known and ident not in seen, 'external region outside scope or duplicated'); seen.add(ident)
        require(r.get('image_sha256') == known[ident]['image_sha256'], 'external source image hash differs')
        text = r.get('text'); require(isinstance(text, str) and len(text) <= 500000, 'bounded external literal text required')
        require(type(r.get('complete')) is bool, 'explicit external completeness required')
        rows.append(dict(region_id=ident, image_sha256=r['image_sha256'], text=text,
                         state='complete' if r['complete'] and text.strip() else 'incomplete', source_text_provided=False))
    packet = dict(schema_version=READING, request_sha256=source['request_sha256'], reader_id=rid,
                  reader_fingerprint=digest({k:v for k,v in metadata.items() if k!='reader_id'}), reader_metadata=metadata,
                  attestation='operator_import_not_inference_verified', regions=rows, source_text_provided=False,
                  imported_reading_sha256=sha256(source_raw).hexdigest(), imported_metadata_sha256=sha256(metadata_raw).hexdigest(), **FLAGS)
    with output_stage(output, request_inputs(request_bundle)+[Path(readings_path), Path(metadata_path)]) as stage:
        (stage/'external-readings.json').write_bytes(source_raw); (stage/'external-metadata.json').write_bytes(metadata_raw)
        write(stage/'reading.json', packet); seal(stage, 'reading', dict(request_bundle=str(request_bundle)))
    return dict(schema_version=READING, output=str(Path(output).resolve()), regions=len(rows), attestation=packet['attestation'], **FLAGS)


def checked_reading(bundle, request_bundle, request):
    bundle = Path(bundle).resolve(); manifest = check_files(bundle, 'reading'); packet = load(bundle/'reading.json')
    require(Path(manifest['metadata']['request_bundle']).resolve() == request_bundle, 'reading request bundle scope differs')
    require(packet.get('schema_version') == READING and packet.get('request_sha256') == file_sha256(request_bundle/'request.json'), 'reading request identity differs')
    require(all(packet.get(k) is v for k,v in FLAGS.items()) and packet.get('source_text_provided') is False, 'reading cannot approve or alter sources')
    require(isinstance(packet.get('reader_id'), str) and packet['reader_id'] and isinstance(packet.get('reader_fingerprint'), str) and len(packet['reader_fingerprint']) == 64, 'reader identity required')
    known = {r['id']: r for r in request['regions']}; seen = set()
    for r in packet.get('regions', []):
        rid = r.get('region_id'); require(rid in known and rid not in seen, 'reading region missing or duplicated'); seen.add(rid)
        require(r.get('image_sha256') == known[rid]['image_sha256'] and r.get('state') in {'complete', 'incomplete'} and r.get('source_text_provided') is False, 'source reading shape differs')
        require(r.get('text') is None or isinstance(r['text'], str) and len(r['text']) <= 500000, 'reading literal exceeds bound')
        if packet.get('attestation') == 'locally_executed_source_image_inference':
            reply = r.get('raw_response', {}); text = reply.get('message', {}).get('content')
            complete = reply.get('done') is True and reply.get('done_reason') == 'stop' and isinstance(text, str) and bool(text.strip())
            require(r['text'] == (text if isinstance(text, str) else None) and (r['state']=='complete') == complete, 'reading differs from retained raw model reply')
    require(packet.get('attestation') in {'locally_executed_source_image_inference', 'operator_import_not_inference_verified'}, 'unknown reading attestation')
    if packet['attestation'] == 'operator_import_not_inference_verified':
        source = load(bundle/'external-readings.json'); metadata = load(bundle/'external-metadata.json')
        require(file_sha256(bundle/'external-readings.json') == packet['imported_reading_sha256'] and file_sha256(bundle/'external-metadata.json') == packet['imported_metadata_sha256'], 'external source pins differ')
        expected = [dict(region_id=r['region_id'], image_sha256=r['image_sha256'], text=r['text'], state='complete' if r['complete'] and r['text'].strip() else 'incomplete', source_text_provided=False) for r in source['regions']]
        require(canonical(expected) == canonical(packet['regions']) and metadata == packet['reader_metadata'] and packet['reader_id'] == metadata['reader_id'], 'imported reading differs from external sources')
        require(packet['reader_fingerprint'] == digest({k:v for k,v in metadata.items() if k!='reader_id'}), 'imported reader fingerprint differs')
    return packet


def derive_comparison(request, readings, required_reader_ids):
    require(isinstance(required_reader_ids, list) and 2 <= len(required_reader_ids) <= 8 and all(isinstance(r, str) and r for r in required_reader_ids) and len(set(required_reader_ids)) == len(required_reader_ids), 'two to eight distinct required readers needed')
    require(2 <= len(readings) <= 8 and len({r['reader_id'] for r in readings}) == len(readings) and len({r['reader_fingerprint'] for r in readings}) == len(readings), 'distinct reader identities/fingerprints required')
    byid = {r['reader_id']: r for r in readings}; require(set(required_reader_ids) <= set(byid), 'required reader unavailable')
    rows = []
    for field in request['fields']:
        witnesses = []
        for rid in required_reader_ids:
            packet = byid[rid]; regions = [r for r in packet['regions'] if r['region_id'] == field['region_id']]
            values = select(regions[0]['text'], field['selector']) if len(regions)==1 and regions[0]['state']=='complete' and regions[0]['text'] else []
            valid = len(values)==1 and all(token in normalized(values[0]['value_literal']) for token in field.get('required_tokens', []))
            witnesses.append(dict(reader_id=rid, reading_sha256=digest(packet), region_id=field['region_id'], matches=values,
                                  unique_complete_qualified_reading=valid, attestation=packet['attestation']))
        agree = all(w['unique_complete_qualified_reading'] for w in witnesses) and len({normalized(w['matches'][0]['value_literal']) for w in witnesses}) == 1
        proposal = witnesses[0]['matches'][0]['value_literal'] if agree else None
        state = 'consensus_matches_native_literal' if agree and normalized(proposal)==normalized(field['native_value_literal']) else 'consensus_correction_candidate' if agree else 'reader_conflict_or_incomplete'
        rows.append(dict(id='field-witness-'+digest([request['source_pdf_sha256'], request['source_package_sha256'], field, witnesses])[:24],
                         field_id=field['id'], native_field_handle=field['native_field_handle'], native_observation_id=field['native_observation_id'],
                         native_value_literal=field['native_value_literal'], native_source_span=field['native_source_span'], native_geometry=field['native_geometry'],
                         native_source_quality=field['native_source_quality'], region_id=field['region_id'], proposed_literal=proposal, state=state,
                         witnesses=witnesses, selected_page_offset_assigned=False, **FLAGS))
    return dict(schema_version=COMPARISON, source_pdf_sha256=request['source_pdf_sha256'], source_package_sha256=request['source_package_sha256'],
                native_fields_sha256=request['native_fields_sha256'], request_sha256=digest(request), required_reader_ids=required_reader_ids,
                regions=request['regions'], fields=rows, counts={state:sum(r['state']==state for r in rows) for state in sorted({r['state'] for r in rows})},
                reader_independence_proved=False, inference_attestation_not_cryptographic=True, **FLAGS)


def compare(request_bundle, reading_bundles, required_reader_ids, output):
    request_bundle = Path(request_bundle).resolve(); request = checked_request(request_bundle)
    bundles = [Path(p).resolve() for p in reading_bundles]; require(len(set(bundles)) == len(bundles), 'duplicate reading bundle')
    readings = [checked_reading(p, request_bundle, request) for p in bundles]
    result = derive_comparison(request, readings, required_reader_ids)
    with output_stage(output, request_inputs(request_bundle)+bundles) as stage:
        write(stage/'comparison.json', result); seal(stage, 'comparison', dict(request_bundle=str(request_bundle), reading_bundles=[str(p) for p in bundles], required_reader_ids=required_reader_ids))
    return dict(schema_version=COMPARISON, output=str(Path(output).resolve()), counts=result['counts'], **FLAGS)


def verify(bundle):
    bundle = Path(bundle).resolve(); manifest = check_files(bundle); kind = manifest['kind']; meta = manifest['metadata']
    require(kind in {'request', 'reading', 'comparison'}, 'unknown field witness bundle kind')
    if kind == 'request': request = checked_request(bundle)
    else:
        request_bundle = Path(meta['request_bundle']).resolve(); request = checked_request(request_bundle)
        if kind == 'reading': checked_reading(bundle, request_bundle, request)
        else:
            readings = [checked_reading(Path(p).resolve(), request_bundle, request) for p in meta['reading_bundles']]
            expected = derive_comparison(request, readings, meta['required_reader_ids'])
            require(canonical(load(bundle/'comparison.json')) == canonical(expected), 'field comparison differs from source-bound replay')
    return dict(schema_version='pdf2data.field-witness-verification.v1', valid=True, kind=kind, source_pdf_sha256=request['source_pdf_sha256'], **FLAGS)


def configure(parser):
    from argparse import ArgumentParser
    sub = parser.add_subparsers(dest='witness_operation', required=True)
    prep = sub.add_parser('prepare')
    for name in ('package', 'fields-bundle', 'rules', 'output'): prep.add_argument('--'+name, type=Path, required=True)
    read = sub.add_parser('ollama')
    for name in ('request', 'output'): read.add_argument('--'+name, type=Path, required=True)
    read.add_argument('--host', required=True); read.add_argument('--model', required=True); read.add_argument('--max-tokens', type=int, default=2048); read.add_argument('--timeout', type=int, default=240)
    imp = sub.add_parser('import')
    for name in ('request', 'readings', 'metadata', 'output'): imp.add_argument('--'+name, type=Path, required=True)
    comp = sub.add_parser('compare'); comp.add_argument('--request', type=Path, required=True); comp.add_argument('--reading', type=Path, action='append', required=True); comp.add_argument('--required-reader', action='append', required=True); comp.add_argument('--output', type=Path, required=True)
    ver = sub.add_parser('verify'); ver.add_argument('--bundle', type=Path, required=True)
    native = sub.add_parser('native-raster',help='derive source-object/raster accounting only; keep transcription/language/layout and page holds')
    native.add_argument('--request',type=Path,required=True);native.add_argument('--output',type=Path,required=True)
    native_ver = sub.add_parser('verify-native-raster');native_ver.add_argument('--bundle',type=Path,required=True)
    for item in (prep, read, imp, comp, ver, native, native_ver): item.add_argument('--json', action='store_true')


def execute(args):
    op = args.witness_operation
    if op == 'prepare': return prepare(args.package, args.fields_bundle, args.rules, args.output)
    if op == 'ollama': return read_ollama(args.request, args.output, args.host, args.model, args.max_tokens, args.timeout)
    if op == 'import': return import_reading(args.request, args.readings, args.metadata, args.output)
    if op == 'compare': return compare(args.request, args.reading, args.required_reader, args.output)
    if op == 'native-raster':
        from .native_scoped_raster import build
        return build(args.request,args.output)
    if op == 'verify-native-raster':
        from .native_scoped_raster import verify as verify_native
        return verify_native(args.bundle)
    return verify(args.bundle)
