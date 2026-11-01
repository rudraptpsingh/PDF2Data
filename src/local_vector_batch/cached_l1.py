"""Admit a pinned legacy reading without extracting or approving its content."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile

from .evidence_package import verify_package
from .hashing import file_sha256
from .legal_l1 import (SCHEMA, FAMILIES, HASH, ID, digest, readiness_ledger,
                       rows, structure_document, validate_structure, write_json, write_rows)

OPERATION = 'pdf2data.cached-reading-admission.v1'
MISSING = ['native_glyphs_and_character_geometry', 'independent_raw_readers',
           'current_l1_source_quality_proof', 'reviewed_reading_order']
DERIVED = {'source-receipt.json', 'layout.jsonl', 'structure.json', 'validation.json',
           'quality.json', 'repair-queue.jsonl', 'readiness.json', 'cached-reading.json'}


def inventory(root):
    """Seal every retained file, including optional historical repair evidence."""
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Cached packages must contain regular files/directories only')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_sha256(path)
    return result


def derive(extraction, config, files):
    pages = rows(extraction/'pages.jsonl')
    legacy = json.loads((extraction/'evidence-package.json').read_text(encoding='utf-8'))
    sha = config['source_pdf_sha256']
    layout = []
    results = []
    jobs = []
    for page in pages:
        dimensions = [page.get('pdf_width'), page.get('pdf_height')]
        known = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in dimensions)
        layout.append(dict(page=page['page'], page_text_sha256=page['text_sha256'],
            width=dimensions[0] if known else None, height=dimensions[1] if known else None,
            geometry_status='cached_page_dimensions_only' if known else 'unavailable',
            coordinate_system='PDF points, top-left' if known else None,
            glyphs=[], selected_method=page['method'],
            capture_basis='unchanged_cached_reading_no_new_source_extraction', missing_facets=MISSING))
        outcome = 'repair_required' if page['text'] else 'source_blocked'
        result = dict(page=page['page'], source_pdf_sha256=sha, page_text_sha256=page['text_sha256'],
            outcome=outcome, failed_components=['cached_reading_requires_l1_source_proof'],
            missing_evidence=MISSING, legacy_page_status=page['status'],
            legacy_selected_method=page['method'], source_quality_assessed=False)
        results.append(result)
        jobs.append(dict(page=page['page'], source_pdf_sha256=sha, page_text_sha256=page['text_sha256'],
            status='awaiting_source_proof', failed_components=result['failed_components'],
            missing_evidence=MISSING, routes=[],
            mutation_policy='Keep this reading immutable; source correction or reproof requires a new package.'))
    quality = dict(schema_version=OPERATION, source_pdf_sha256=sha, pages_assessed=len(pages),
        source_verified=True, source_quality_assessed=False,
        assessment_basis='legacy_integrity_only_not_current_l1_source_quality',
        counts={s: sum(r['outcome'] == s for r in results)
                for s in ('auto_accepted', 'repair_required', 'source_blocked')}, results=results)
    structure = structure_document(pages, sha, config['family'], layout=layout,
        tables=json.loads((extraction/'tables.json').read_text(encoding='utf-8')))
    checked = validate_structure(structure, pages, sha)
    source = dict(id=config['source_id'], sha256=sha, family=config['family'],
                  title=legacy['document']['title'], url=legacy['document'].get('source_url'))
    admission = dict(schema_version=OPERATION, configuration=config, legacy_files=files,
        legacy_evidence_package_sha256=files['evidence-package.json'],
        selected_reading_sha256=digest(json.dumps(
            {'source_pdf_sha256':sha, 'legacy_files':files}, sort_keys=True).encode()),
        cached_reading_modified=False, fresh_extraction_run=False, source_quality_approval_granted=False,
        missing_facets=MISSING, original_page_geometry='retained_when_cached_not_rederived',
        table_geometry='unchanged_in_extraction/tables.json', publication_eligible=False)
    return {'cached-reading.json':admission, 'source-receipt.json':dict(declared=source,
        actual_sha256=sha, physical_pages=len(pages), acquisition='verified_legacy_cached_reading',
        url_identity='retained_legacy_declaration_not_fetched',
        issuer_role_date_status='unresolved_requires_source_review', configuration=config),
        'layout.jsonl':layout, 'structure.json':structure, 'quality.json':quality,
        'readiness.json':readiness_ledger(structure, quality), 'repair-queue.jsonl':jobs,
        'validation.json':dict(schema_version=SCHEMA, structure=checked,
            operation=OPERATION, missing_facets=MISSING, source_quality_approval_granted=False,
            database_access='none', publication_eligible=False)}


def verify(output):
    output = Path(output)
    receipt = json.loads((output/'l1-package.json').read_text(encoding='utf-8'))
    if receipt.get('schema_version') != SCHEMA or receipt.get('operation') != OPERATION:
        raise ValueError('Unknown cached-reading admission schema')
    expected = {'original.pdf', *DERIVED}
    if set(receipt.get('artifacts', {})) != expected:
        raise ValueError('Incomplete cached-reading L1 artifact manifest')
    for name, sha in receipt['artifacts'].items():
        if file_sha256(output/name) != sha:
            raise ValueError('Cached-reading artifact hash mismatch: '+name)
    admission = json.loads((output/'cached-reading.json').read_text(encoding='utf-8'))
    config = admission['configuration']
    validate_config(config)
    if file_sha256(output/'original.pdf') != config['source_pdf_sha256']:
        raise ValueError('Cached-reading original PDF hash mismatch')
    extraction = output/'extraction'
    files = inventory(extraction)
    if files != admission['legacy_files'] or files.get('evidence-package.json') != config['legacy_package_sha256']:
        raise ValueError('Cached-reading legacy files differ from pinned inventory')
    verified = verify_package(extraction, source_pdf=output/'original.pdf')
    derived = derive(extraction, config, files)
    for name, value in derived.items():
        actual = rows(output/name) if name.endswith('.jsonl') else json.loads((output/name).read_text(encoding='utf-8'))
        if actual != value:
            raise ValueError('Cached-reading derivation mismatch: '+name)
    if receipt.get('request_sha256') != digest(json.dumps(config, sort_keys=True).encode()):
        raise ValueError('Cached-reading request identity mismatch')
    return dict(integrity='passed', source_id=config['source_id'], source_pdf_sha256=config['source_pdf_sha256'],
        pages=verified['pages'], structure=derived['validation.json']['structure'],
        operation=OPERATION, cached_reading_modified=False, fresh_extraction_run=False,
        legacy_page_text_readiness=verified['page_text_readiness'], page_text_readiness='source_proof_required',
        quality_counts=derived['quality.json']['counts'], content_ready=False,
        missing_facets=MISSING, publication_eligible=False, legal_review='not_run')


def validate_config(config):
    if set(config) != {'operation','source_id','family','source_pdf_sha256','legacy_package_sha256','producer'} or config['operation'] != OPERATION:
        raise ValueError('Invalid cached-reading configuration')
    producer = config['producer']
    if not isinstance(producer, dict) or set(producer) != {'name','version','implementation_sha256'} or producer['name'] != 'PDF2Data' or not isinstance(producer['version'], str) or not producer['version']:
        raise ValueError('Invalid cached-reading producer provenance')
    if not isinstance(producer['implementation_sha256'], str) or not HASH.fullmatch(producer['implementation_sha256']):
        raise ValueError('Invalid cached-reading implementation identity')
    if not isinstance(config['source_id'],str) or not ID.fullmatch(config['source_id']) or config['family'] not in FAMILIES:
        raise ValueError('Declare a safe source identity and supported family')
    if any(not isinstance(config[k],str) or not HASH.fullmatch(config[k])
           for k in ('source_pdf_sha256','legacy_package_sha256')):
        raise ValueError('Pin original PDF and legacy manifest SHA256')


def admit(package, source_pdf, source_sha256, package_sha256, source_id, family, output):
    from . import __version__
    package, source_pdf, output = (Path(p).resolve() for p in (package, source_pdf, output))
    config = dict(operation=OPERATION, source_id=source_id, family=family,
                  source_pdf_sha256=source_sha256, legacy_package_sha256=package_sha256,
                  producer=dict(name='PDF2Data', version=__version__, implementation_sha256=file_sha256(Path(__file__))))
    validate_config(config)
    if output.exists() or output.is_relative_to(package) or package.is_relative_to(output) or source_pdf.is_relative_to(output):
        raise ValueError('Use a new immutable output outside the legacy package and original')
    if file_sha256(source_pdf) != source_sha256 or file_sha256(package/'evidence-package.json') != package_sha256:
        raise ValueError('Original or legacy package differs from expected SHA256')
    verify_package(package, source_pdf=source_pdf)
    files = inventory(package)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+output.name+'-', dir=output.parent) as temp:
        stage = Path(temp)/'package'
        stage.mkdir()
        shutil.copyfile(source_pdf, stage/'original.pdf')
        shutil.copytree(package, stage/'extraction', symlinks=True)
        if inventory(stage/'extraction') != files or inventory(package) != files:
            raise ValueError('Legacy package changed during admission')
        for name, value in derive(stage/'extraction', config, files).items():
            (write_rows if name.endswith('.jsonl') else write_json)(stage/name, value)
        write_json(stage/'l1-package.json', dict(schema_version=SCHEMA, operation=OPERATION,
            request_sha256=digest(json.dumps(config, sort_keys=True).encode()),
            artifacts={name:file_sha256(stage/name) for name in sorted({'original.pdf', *DERIVED})}))
        result = verify(stage)
        if inventory(package) != files or file_sha256(source_pdf) != source_sha256:
            raise ValueError('Original or legacy package changed during admission')
        stage.rename(output)
    return result
