#!/usr/bin/env python3
"""CPU-only scoped proof versions over immutable, verified L1 extraction caches.

The extraction keeps its original producer identity. Only selected held pages
receive a new source-line witness, raster proof and unchanged eight quality
gates. This is source quality evidence, never legal publication approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from importlib.metadata import version
import subprocess

def inspect_pdf2data_profile(parser_root):
    root=Path(parser_root).resolve(); source=root/'src/local_vector_batch'
    files=sorted(p for p in source.iterdir() if p.suffix in {'.py','.json'})
    expected={'pdfplumber':version('pdfplumber'),'pypdf':version('pypdf'),
              'implementation_sha256':file_sha(source/'legal_l1.py'),
              'extractor_sha256':file_sha(source/'pdf_ingest.py'),
              'package_code_sha256':digest(b''.join(p.name.encode()+p.read_bytes() for p in files))}
    profile={'contract_version':'powerlex.pdf2data-l1-profile.v1','parser_kind':'PDF2Data_legal_l1',
             'parser_root':str(root),'source_configuration_expected':expected,
             'source_files':{p.name:file_sha(p) for p in files},
             'source_quality_gate':'L1quality+readiness; never nested legacy embedding flag',
             'legal_review':'not_run','answer_publication_eligible':False}
    profile['profile_sha256']=digest(canonical(profile));return profile

CONTRACT = 'powerlex.l1-targeted-reproof.v1'
COMPONENTS = ('source_identity', 'text_content', 'language', 'source_render',
              'words_and_order', 'ordered_numbers', 'ordered_operators', 'layout')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def file_sha(path):
    return digest(Path(path).read_bytes())


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def runtime_modules(runtime):
    runtime = Path(runtime).resolve()
    receipt = read(runtime / 'runtime-receipt.json')
    if any(file_sha(runtime / name) != sha for name, sha in receipt['files'].items()):
        raise ValueError('Frozen proof runtime changed')
    sys.path.insert(0, str(runtime / 'src'))
    import local_vector_batch
    if Path(local_vector_batch.__file__).resolve().parent != runtime / 'src/local_vector_batch':
        raise ValueError('Wrong cached PDF2Data module loaded; use an isolated proof process')
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch.native_consensus import read_source_lines, choose_witness
    from local_vector_batch.quality_gate import assess_page, native_source_raster_evidence, load_policy
    return dict(verify_document=verify_document, read_source_lines=read_source_lines,
                choose_witness=choose_witness, assess_page=assess_page,
                native_source_raster_evidence=native_source_raster_evidence,
                policy=load_policy(), epoch=receipt)


def bind_parent(parent, runtime, modules, expected_manifest_sha256=None):
    parent = Path(parent).resolve()
    before = file_sha(parent / 'l1-package.json')
    if expected_manifest_sha256 and before != expected_manifest_sha256:
        raise ValueError('Explicit immutable parent manifest changed')
    verified = modules['verify_document'](parent)
    source = read(parent / 'source-receipt.json')
    profile = inspect_pdf2data_profile(runtime)
    # Reuse native reader artifacts only under the same actual dependencies.
    for name in ('pdfplumber', 'pypdf', 'extractor_sha256'):
        if source['configuration'].get(name) != profile['source_configuration_expected'][name]:
            raise ValueError('Cached extraction reader/dependency changed: ' + name)
    old_manifest = read(parent / 'l1-package.json')
    artifact_hashes = {name: file_sha(parent / name) for name in old_manifest['artifacts']}
    artifact_hashes['l1-package.json'] = before
    artifact_hashes.update({'extraction/' + name: file_sha(parent / 'extraction' / name)
                           for name in read(parent / 'extraction/evidence-package.json')['files']})
    artifact_hashes['extraction/evidence-package.json'] = file_sha(parent / 'extraction/evidence-package.json')
    return dict(path=parent, manifest_sha256=before, verified=verified,
                source=source, source_pdf_sha256=verified['source_pdf_sha256'],
                artifact_hashes=artifact_hashes, profile=profile,
                pages={p['page']: p for p in rows(parent / 'extraction/pages.jsonl')},
                layout={p['page']: p for p in rows(parent / 'layout.jsonl')},
                quality=read(parent / 'quality.json'),
                tables=read(parent / 'extraction/tables.json'))


def verify_parent_unchanged(bound):
    if any(file_sha(bound['path'] / name) != sha for name, sha in bound['artifact_hashes'].items()):
        raise ValueError('Immutable parent artifacts changed during proof')


def evaluate_page(bound, physical_page, native, modules):
    """Fresh source geometry and latest independent witness; selected text untouched."""
    started = time.perf_counter()
    page = bound['pages'][physical_page]
    raw = bound['layout'][physical_page]
    if page['method'] != 'pdf_text':
        raise ValueError('Targeted native reproof cannot stand in for OCR')
    if page['text_sha256'] != digest(page['text']) or raw['page_text_sha256'] != page['text_sha256']:
        raise ValueError('Cached selected page bytes changed')
    alternate = raw.get('secondary_layout_text_raw')
    if not isinstance(alternate, str) or raw.get('secondary_layout_text_sha256') != digest(alternate):
        raise ValueError('Complete independently captured pypdf layout witness is unavailable')
    fresh_glyphs = [{'text': c.get('text', ''),
                     'bbox': [float(c[k]) for k in ('x0', 'top', 'x1', 'bottom')],
                     'font': c.get('fontname'), 'size': float(c.get('size', 0))}
                    for c in native.chars]
    if fresh_glyphs != raw['glyphs'] or native.width != raw['width'] or native.height != raw['height']:
        raise ValueError('Cached glyph/layout geometry disagrees with fresh exact PDF page')
    source_lines = modules['read_source_lines'](bound['path'] / 'original.pdf', native,
                                                physical_page, secondary_layout_text=alternate)
    witness, basis = modules['choose_witness'](page['text'], alternate, source_lines)
    table_count = sum(t['page'] == physical_page for t in bound['tables'])
    geometry = modules['native_source_raster_evidence'](
        page, native, bound['source_pdf_sha256'], witness,
        policy=modules['policy'], table_count=table_count)
    quality = modules['assess_page'](
        page, source_pdf_sha256=bound['source_pdf_sha256'],
        package_pdf_sha256=bound['source_pdf_sha256'], independent_text=witness,
        source_geometry=geometry, table_count=table_count, policy=modules['policy'],
        source_pdf_path=bound['path'] / 'original.pdf')
    quality['policy_sha256'] = digest(json.dumps(modules['policy'], sort_keys=True, ensure_ascii=False))
    quality['source_url'] = read(bound['path'] / 'extraction/evidence-package.json')['document'].get('source_url')
    quality['independent_text_sha256'] = digest(witness) if witness is not None else None
    return dict(page=physical_page, page_text_sha256=page['text_sha256'],
                source_pdf_sha256=bound['source_pdf_sha256'],
                cached_pypdf_layout_sha256=raw['secondary_layout_text_sha256'],
                fresh_glyph_geometry_sha256=digest(canonical(fresh_glyphs)),
                pdfium_source_lines=source_lines, independent_text=witness,
                independent_basis=basis, source_geometry=geometry, quality=quality,
                selected_text_modified=False, legal_review='not_run',
                answer_publication_eligible=False,
                elapsed_seconds=round(time.perf_counter() - started, 6))


def _create_reproof(parent, runtime, pages, output, *, expected_manifest_sha256=None):
    started = time.perf_counter()
    output, runtime = Path(output).resolve(), Path(runtime).resolve()
    if output.exists():
        raise ValueError('Use a new immutable proof output directory')
    modules = runtime_modules(runtime)
    bound = bind_parent(parent, runtime, modules, expected_manifest_sha256)
    pages = sorted(set(pages))
    if not pages or any(n not in bound['pages'] for n in pages):
        raise ValueError('Explicit existing physical target pages required')
    before = {q['page']: q for q in bound['quality']['results']}
    if any(before[n]['outcome'] == 'auto_accepted' for n in pages):
        raise ValueError('Already passing pages are retained unchanged, not reclassified by this repair')
    import pdfplumber
    proofs = []
    with pdfplumber.open(bound['path'] / 'original.pdf') as source:
        for n in pages:
            native = source.pages[n - 1]
            proofs.append(evaluate_page(bound, n, native, modules))
            native.close()
    verify_parent_unchanged(bound)
    output.mkdir(parents=True)
    selected = {p['page']: p['quality'] for p in proofs}
    merged = {**bound['quality'], 'results': [selected.get(q['page'], q) for q in bound['quality']['results']]}
    merged['counts'] = {state: sum(q['outcome'] == state for q in merged['results'])
                        for state in ('auto_accepted', 'repair_required', 'source_blocked')}
    save(output / 'quality.json', merged)
    save(output / 'page-proofs.json', proofs)
    save(output / 'parent-bindings.json', {'parent_l1_package_path': str(bound['path']),
                                         'artifacts_sha256': bound['artifact_hashes'],
                                         'source_extraction_configuration': bound['source']['configuration']})
    receipt = {'contract_version': CONTRACT, 'parent_l1_package_path': str(bound['path']),
               'parent_manifest_sha256': bound['manifest_sha256'],
               'source_pdf_sha256': bound['source_pdf_sha256'],
               'operation': 'scoped_native_quality_reproof_over_verified_cached_extraction',
               'source_extraction_configuration': bound['source']['configuration'],
               'proof_runtime_path': str(runtime), 'proof_runtime_tree_sha256': modules['epoch']['tree_sha256'],
               'proof_profile': bound['profile'], 'proof_implementation_sha256': file_sha(__file__),
               'target_pages': pages, 'passing_pages_unchanged': True,
               'source_extraction_rerun': False, 'selected_text_modified': False,
               'newly_auto_accepted_pages': [p['page'] for p in proofs if p['quality']['outcome'] == 'auto_accepted'],
               'still_held_pages': [p['page'] for p in proofs if p['quality']['outcome'] != 'auto_accepted'],
               'artifacts_sha256': {name: file_sha(output / name) for name in
                                    ('quality.json', 'page-proofs.json', 'parent-bindings.json')},
               'elapsed_seconds': round(time.perf_counter() - started, 6),
               'legal_review': 'not_run', 'answer_publication_eligible': False,
               'new_model_calls': 0, 'remote_operations': 0}
    receipt['quality_repair_configuration']={
        'operation':'targeted_native_reproof','parent_manifest_sha256':bound['manifest_sha256'],
        'proof_runtime_tree_sha256':modules['epoch']['tree_sha256'],
        'proof_implementation_sha256':file_sha(__file__),'proof_profile':bound['profile']}
    save(output / 'reproof-version.json', receipt)
    return receipt


def _verify_reproof(output, runtime, *, fresh=True):
    """Independent candidate adapter; never relabel cached extraction as new L1."""
    started = time.perf_counter()
    output, runtime = Path(output).resolve(), Path(runtime).resolve()
    receipt = read(output / 'reproof-version.json')
    if receipt.get('strategy') == 'native_regions':
        runtime_modules(runtime) # Only explicitly selected, hash-verified frozen code.
        from local_vector_batch.native_region_reproof import verify_region_reproof
        return verify_region_reproof(output,runtime,fresh=fresh)
    if receipt.get('strategy') not in (None,'source_lines'):
        raise ValueError('Unknown closed targeted-reproof strategy')
    if receipt.get('contract_version') != CONTRACT or receipt.get('answer_publication_eligible') is not False:
        raise ValueError('Unsupported or promoted proof contract')
    if (receipt.get('source_extraction_rerun') is not False or
            receipt.get('selected_text_modified') is not False or
            receipt.get('passing_pages_unchanged') is not True or
            receipt.get('legal_review') != 'not_run' or
            set(receipt.get('artifacts_sha256', {})) != {'quality.json', 'page-proofs.json', 'parent-bindings.json'}):
        raise ValueError('Closed source-quality-only proof scope required')
    if receipt['proof_implementation_sha256'] != file_sha(__file__) or Path(receipt['proof_runtime_path']) != runtime:
        raise ValueError('Proof implementation/runtime changed')
    modules = runtime_modules(runtime)
    if receipt['proof_runtime_tree_sha256'] != modules['epoch']['tree_sha256']:
        raise ValueError('Current frozen proof profile differs')
    if any(file_sha(output / name) != sha for name, sha in receipt['artifacts_sha256'].items()):
        raise ValueError('Proof artifact changed')
    bound = bind_parent(receipt['parent_l1_package_path'], runtime, modules, receipt['parent_manifest_sha256'])
    expected_repair_configuration={
        'operation':'targeted_native_reproof','parent_manifest_sha256':bound['manifest_sha256'],
        'proof_runtime_tree_sha256':modules['epoch']['tree_sha256'],
        'proof_implementation_sha256':file_sha(__file__),'proof_profile':bound['profile']}
    if receipt.get('quality_repair_configuration')!=expected_repair_configuration:
        raise ValueError('Independent quality proof configuration changed')
    bindings = read(output / 'parent-bindings.json')
    if (bindings['artifacts_sha256'] != bound['artifact_hashes'] or
            bindings.get('parent_l1_package_path') != str(bound['path']) or
            bindings.get('source_extraction_configuration') != bound['source']['configuration'] or
            receipt.get('source_extraction_configuration') != bound['source']['configuration'] or
            receipt.get('proof_profile') != bound['profile'] or
            receipt['source_pdf_sha256'] != bound['source_pdf_sha256']):
        raise ValueError('Proof parent/source binding changed')
    proofs = read(output / 'page-proofs.json')
    target_pages = receipt['target_pages']
    if (not target_pages or any(type(n) is not int or n not in bound['pages'] for n in target_pages) or
            target_pages != sorted(set(target_pages)) or
            [p['page'] for p in proofs] != target_pages or
            any(bound['quality']['results'][n-1]['outcome'] == 'auto_accepted' for n in target_pages)):
        raise ValueError('Target page scope changed')
    if fresh:
        import pdfplumber
        with pdfplumber.open(bound['path'] / 'original.pdf') as source:
            for proof in proofs:
                native = source.pages[proof['page'] - 1]
                actual = evaluate_page(bound, proof['page'], native, modules)
                native.close()
                if {k: v for k, v in actual.items() if k != 'elapsed_seconds'} != {k: v for k, v in proof.items() if k != 'elapsed_seconds'}:
                    raise ValueError('Stored proof differs from fresh original-source reproof')
    expected = {p['page']: p['quality'] for p in proofs}
    merged = read(output / 'quality.json')
    expected_merged = {**bound['quality'], 'results': [expected.get(q['page'], q) for q in bound['quality']['results']]}
    expected_merged['counts'] = {state: sum(q['outcome'] == state for q in expected_merged['results'])
                               for state in ('auto_accepted', 'repair_required', 'source_blocked')}
    if (merged != expected_merged or
            receipt.get('newly_auto_accepted_pages') != [p['page'] for p in proofs if p['quality']['outcome'] == 'auto_accepted'] or
            receipt.get('still_held_pages') != [p['page'] for p in proofs if p['quality']['outcome'] != 'auto_accepted']):
        raise ValueError('Passing/non-target quality changed or targeted proof omitted')
    verify_parent_unchanged(bound)
    from local_vector_batch.legal_l1 import readiness_ledger
    virtual_readiness=readiness_ledger(read(bound['path']/'structure.json'),merged)
    return {'contract_version': 'powerlex.pdf2data-targeted-reproof-readiness.v1',
            'integrity': 'passed', 'parent_l1_package_path': str(bound['path']),
            'reproof_path': str(output), 'reproof_sha256': file_sha(output / 'reproof-version.json'),
            'source_pdf_sha256': bound['source_pdf_sha256'],
            'parent_integrity_verification': bound['verified'],
            'virtual_readiness': virtual_readiness,
            'quality_repair_configuration': receipt['quality_repair_configuration'],
            'source_extraction_configuration': bound['source']['configuration'],
            'proof_profile': bound['profile'], 'current_proof_profile_matches': True,
            'source_extraction_rerun': False, 'selected_text_modified': False,
            'fresh_original_reproof': fresh,
            'target_pages': [{'page': p['page'], 'page_text_sha256': p['page_text_sha256'],
                              'quality': p['quality'], 'embedding_candidate_allowed': fresh and
                              p['quality']['outcome'] == 'auto_accepted' and
                              all(p['quality']['components'].get(c, {}).get('state') == 'pass' for c in COMPONENTS)}
                             for p in proofs],
            'untargeted_quality_retained_with_original_producer_identity': True,
            'legal_review': 'not_run', 'answer_publication_eligible': False,
            'new_model_calls': 0, 'remote_operations': 0,
            'verification_elapsed_seconds': round(time.perf_counter() - started, 6)}


def _worker(operation, parameters):
    """Selected frozen runtime imports stay isolated from a caller's tool epoch."""
    run=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--worker'],
                       input=json.dumps({'operation':operation,'parameters':parameters}),
                       capture_output=True,text=True,timeout=600)
    if run.returncode:
        raise ValueError('Targeted source proof rejected: '+run.stderr.strip().splitlines()[-1])
    return json.loads(run.stdout)


def create_reproof(parent, runtime, pages, output, *, expected_manifest_sha256=None):
    loaded=sys.modules.get('local_vector_batch')
    if loaded is None or Path(loaded.__file__).resolve().parent==Path(runtime).resolve()/'src/local_vector_batch':
        return _create_reproof(parent,runtime,pages,output,expected_manifest_sha256=expected_manifest_sha256)
    return _worker('create',dict(parent=str(Path(parent).resolve()),runtime=str(Path(runtime).resolve()),
                                pages=list(pages),output=str(Path(output).resolve()),
                                expected_manifest_sha256=expected_manifest_sha256))


def verify_reproof(output, runtime, *, fresh=True):
    loaded=sys.modules.get('local_vector_batch')
    if loaded is None or Path(loaded.__file__).resolve().parent==Path(runtime).resolve()/'src/local_vector_batch':
        return _verify_reproof(output,runtime,fresh=fresh)
    return _worker('verify',dict(output=str(Path(output).resolve()),runtime=str(Path(runtime).resolve()),fresh=fresh))


if __name__ == '__main__':
    if sys.argv[1:]==['--worker']:
        request=json.loads(sys.stdin.read());operation=request['operation'];parameters=request['parameters']
        if operation=='create':result=_create_reproof(**parameters)
        elif operation=='verify':result=_verify_reproof(**parameters)
        else:raise ValueError('Unknown closed targeted-proof operation')
        print(json.dumps(result));sys.exit(0)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--page',action='append',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(create_reproof(args.parent,args.runtime,args.page,args.output)))
