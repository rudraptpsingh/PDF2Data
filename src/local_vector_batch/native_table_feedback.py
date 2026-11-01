#!/usr/bin/env python3
"""Consume exact native table-cell feedback without changing source packages.

Native tables are independently re-extracted by the selected immutable tool.
The resulting proof validates cell readings/geometry, not prose reading order,
table semantics, or publication. Human-origin labels remain receipt claims;
this local adapter does not authenticate an external reviewer identity.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .proof_identity import inspect_pdf2data_profile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def require_coherent_proof_modules(*modules):
    """Accept one actual package directory in both wheels and source checkouts."""
    package=Path(__file__).resolve().parent
    if any(Path(module.__file__).resolve().parent!=package for module in modules):
        raise ValueError('Mixed cached proof runtime modules are prohibited')


def capture_source(package, parser_root, page):
    """Fresh current-tool proof in process, with explicit original epoch binding.

    The origin is data provenance, never imported as executable proof code.
    Relative imports must all belong to this selected proof package. An opaque
    verification session may reuse earlier full parent validation only after
    live complete-artifact hashes and dependency/profile guards match.
    """
    package=Path(package).resolve();origin_profile=inspect_pdf2data_profile(parser_root)
    source_configuration=json.loads((package/'source-receipt.json').read_text())['configuration']
    if any(source_configuration.get(k)!=v for k,v in origin_profile['source_configuration_expected'].items()):
        raise ValueError('Original source extraction runtime/configuration differs')
    from . import legal_l1,l1_review,quality_gate
    from .native_verification_session import active_session
    require_coherent_proof_modules(legal_l1,l1_review,quality_gate)
    legal_l1.verify_document(package);source=package/'original.pdf';source_sha=sha(source.read_bytes())
    session=active_session()
    selected=session.page_row(package/'extraction/pages.jsonl',page)if session else next(row for row in read_rows(package/'extraction/pages.jsonl')if row['page']==page)
    preview=l1_review.preview_tables(source,page,source_sha,selected_page_text=selected['text'])
    tables=json.loads((package/'extraction/tables.json').read_text())
    quality=quality_gate.assess_package(package/'extraction',source,pages=[page],derive_ocr_source_raster=True)
    result={'source_pdf_sha256':source_sha,'page':page,'page_text_sha256':selected['text_sha256'],
            'tables':preview,'raw_tables':[t for t in tables if t['page']==page],'fresh_quality':quality}
    from .native_source_table_inventory import build_inventory_view
    view=build_inventory_view(result,parser_root)
    if view is not None:result['source_table_inventory_view']=view
    return result


def _geometry(table):
    return {key: ([round(value, 2) for value in table[key]] if key == 'bbox' else table[key])
            for key in ('bbox', 'table_geometry_sha256', 'row_count', 'column_count', 'unresolved_grid_slots')}


def _cells(table):
    return [{key: cell[key] for key in ('row', 'column', 'row_span', 'column_span', 'bbox', 'text')}
            for cell in table['cell_spans']]


def validate_feedback(snapshot, decisions, raster_reviews):
    """Pure validation. Any stale/ambiguous feedback rejects, never broadens proof."""
    source_sha, page = snapshot['source_pdf_sha256'], snapshot['page']
    tables = snapshot['tables']
    raw_tables = snapshot['raw_tables']
    if isinstance(tables,list)and isinstance(raw_tables,list)and snapshot.get('source_table_inventory_view')is not None:
        from .native_source_table_inventory import effective_source_tables
        raw_tables=effective_source_tables(snapshot)
    if not isinstance(decisions,list)or not isinstance(raster_reviews,dict):raise ValueError('Explicit feedback list and raster-review mapping required')
    if not isinstance(tables,list)or not isinstance(raw_tables,list)or len(tables)!=len(raw_tables):
        raise ValueError('Raw native table cardinality disagrees with independent re-extraction')
    # Empty is an observed absence, not a fabricated complete grid. The closed
    # region strategy must still independently prove all source prose and ink.
    if not tables and(decisions or raster_reviews):
        raise ValueError('Zero-table source cannot consume cell/raster feedback')
    all_cells = {}
    for table in tables:
        if (any(type(table.get(k))is not int or table[k]<1 for k in('row_count','column_count'))
                or not isinstance(table.get('cell_spans'),list)or not table['cell_spans']):
            raise ValueError('Native grid needs positive dimensions and actual source cell spans')
        candidates = [raw for raw in raw_tables if [round(v, 2) for v in raw['bbox']] == [round(v, 2) for v in table['bbox']]]
        if len(candidates) != 1:
            raise ValueError('Raw native table geometry is missing or ambiguous')
        raw = candidates[0]
        if _geometry(raw) != _geometry(table) or _cells(raw) != _cells(table):
            raise ValueError('Raw native table cells/geometry differ from source re-extraction')
        grid = raw.get('cells')
        if (not isinstance(grid, list) or len(grid) != table['row_count'] or
                any(not isinstance(row, list) or len(row) != table['column_count'] for row in grid) or
                any(grid[cell['row']][cell['column']] != cell['text'] for cell in table['cell_spans'])):
            raise ValueError('Independent raw grid differs from source cell spans')
        if raw.get('extraction_basis') != 'native_pdf_geometry' or table['unresolved_grid_slots']:
            raise ValueError('Native grid has unresolved geometry')
        occupied = set()
        for cell in table['cell_spans']:
            for r in range(cell['row'], cell['row'] + cell['row_span']):
                for c in range(cell['column'], cell['column'] + cell['column_span']):
                    if (r, c) in occupied:
                        raise ValueError('Native grid overlaps')
                    occupied.add((r, c))
            for key, value in [('selected_cell_sha256', cell['text'] or ''),
                               ('independent_cell_sha256', cell['independent_text'])]:
                if cell[key] != sha(value):
                    raise ValueError('Cell reading hash is inconsistent')
            if cell['id'] in all_cells:
                raise ValueError('Duplicate source cell identity')
            all_cells[cell['id']] = (table, cell)
        if occupied != {(r, c) for r in range(table['row_count']) for c in range(table['column_count'])}:
            raise ValueError('Native grid does not account for every source slot')
    # Exact duplicate retries are idempotent. Differing choices for one version
    # require a new explicit adjudication, rather than a last-write-wins waiver.
    unique = {}
    for decision in decisions:
        if decision.get('kind') != 'extraction_table_cell_feedback':
            raise ValueError('Unsupported feedback kind')
        if decision.get('source_pdf_sha256') != source_sha or decision.get('page') != page:
            raise ValueError('Feedback source/page is stale')
        identity = decision.get('cell_id')
        if identity not in all_cells:
            raise ValueError('Feedback cell/table does not belong to current source')
        table, cell = all_cells[identity]
        for key, expected in [('table_version_id', table['table_version_id']), ('row', cell['row']),
                              ('column', cell['column']), ('bbox', cell['bbox']),
                              ('selected_cell_sha256', cell['selected_cell_sha256']),
                              ('independent_cell_sha256', cell['independent_cell_sha256']),
                              ('selected_text', cell['text']), ('independent_text', cell['independent_text'])]:
            if decision.get(key) != expected:
                raise ValueError('Feedback cell version/readings changed: ' + key)
        if decision.get('decision') != 'selected' or decision.get('correction') not in (None, ''):
            raise ValueError('Feedback proposes a different reading; immutable corrected candidate required')
        if decision.get('promotes_page') is not False:
            raise ValueError('Feedback cannot approve a whole page')
        stable = {k: v for k, v in decision.items() if k != 'created_at'}
        if identity in unique and canonical(unique[identity][1]) != canonical(stable):
            raise ValueError('Conflicting duplicate cell feedback')
        unique[identity] = (decision, stable)
    results, jobs = [], []
    for table in tables:
        cells = []
        for cell in table['cell_spans']:
            result = {key: cell[key] for key in ('id', 'row', 'column', 'row_span', 'column_span', 'bbox',
                                                'selected_cell_sha256', 'independent_cell_sha256')}
            if cell.get('readers_agree') and not cell.get('glyph_audit', {}).get('issues') and not cell.get('independent_glyph_audit', {}).get('issues'):
                mapping=cell.get('independent_reader_mapping',{})
                result.update(status='validated', basis='source_bound_registered_reader_mapping'if mapping.get('status')=='source_proved'else'independent_pdfium_native_cell_agreement')
                if mapping.get('status')=='source_proved':result['independent_reader_mapping']=mapping
            elif cell.get('processed_cell_version'):
                from .native_cell_space_projection import validate_processed_cell_version
                projection=cell['processed_cell_version']
                if (not cell.get('glyph_audit',{}).get('issues')and not cell.get('independent_glyph_audit',{}).get('issues')and
                    validate_processed_cell_version(projection,cell,source_sha,page,snapshot['page_text_sha256'])):
                    result.update(status='validated',basis='source_bound_additive_processed_cell_projection',
                                  processed_cell_version=projection)
                else:result.update(status='held',basis='processed_cell_projection_binding_invalid')
            elif cell['id'] in unique:
                decision = unique[cell['id']][0]
                try:
                    note = json.loads(decision['note'])
                except (ValueError, KeyError, TypeError):
                    raise ValueError('Cell review needs structured source-raster provenance')
                if note.get('origin') not in {'human_user_current_thread', 'model_assisted_selected_original_cell_raster'}:
                    raise ValueError('Unsupported or missing review origin')
                if note['origin'] == 'human_user_current_thread' and not (note.get('question_id') and note.get('human_reply')):
                    raise ValueError('Human-origin claim lacks confirmation provenance')
                review = raster_reviews.get(cell['id'])
                if not review or review['render_sha256'] != note.get('render_sha256'):
                    raise ValueError('Source-raster feedback binding is missing or stale')
                expected_box = [max(0, cell['bbox'][0]-2), max(0, cell['bbox'][1]-2),
                                min(table['page_width'], cell['bbox'][2]+2), min(table['page_height'], cell['bbox'][3]+2)]
                if review['crop_source_bbox'] != expected_box or review['render_dpi'] != 240:
                    raise ValueError('Source-raster crop geometry/profile changed')
                result.update(status='validated', basis='source_bound_selected_cell_review',
                              review_origin=note['origin'], review_receipt_sha256=sha(canonical(decision)),
                              source_crop_sha256=review['render_sha256'])
                jobs.append({'cell_id': cell['id'], 'table_version_id': table['table_version_id'],
                             'status': 'validated', 'repair_state': 'source_revalidated',
                             'reading_changed': False, 'review_origin': note['origin'], 'promotes_page': False})
            else:
                result.update(status='held', basis='source_cell_disagreement_without_bound_review')
            cells.append(result)
        results.append({'table_version_id': table['table_version_id'], 'source_pdf_sha256': source_sha,
                        'page': page, **_geometry(table), 'cells': cells,
                        'status': 'validated' if all(c['status'] == 'validated' for c in cells) else 'held',
                        'proof_scope': 'source_cell_readings_and_native_grid_geometry_only',
                        'header_associations': 'candidate_only', 'legal_review': 'not_run',
                        'answer_publication_eligible': False})
    quality = snapshot['fresh_quality']['results'][0]
    return {'contract_version': 'powerlex.native-table-cell-revalidation.v1', 'source_pdf_sha256': source_sha,
            'page': page, 'page_text_sha256': snapshot['page_text_sha256'], 'tables': results, 'jobs': jobs,
            'status': 'validated' if all(t['status'] == 'validated' for t in results) else 'held',
            'fresh_page_quality': quality, 'residual_gates': [key for key, value in quality['components'].items() if value['state'] != 'pass'],
            'page_quality_cleared': False, 'legal_review': 'not_run', 'answer_publication_eligible': False,
            'source_text_modified': False, 'actual_model_inference_calls': 0, 'remote_operations': 0,
            **({'table_inventory':'fresh_and_raw_native_tables_both_empty','proof_scope':'absence_of_native_tables_only; complete_prose_and_raster_gates_still_required'}if not tables else{})}


def _revalidate_checked(package, parser_root, feedback, dossier, output, page=5):
    package, parser_root, feedback, dossier, output = map(Path, (package, parser_root, feedback, dossier, output))
    if output.exists():
        raise ValueError('Choose a new immutable output directory')
    profile = inspect_pdf2data_profile(parser_root)
    snapshot = capture_source(package, parser_root, page)
    source_config = json.loads((package/'source-receipt.json').read_text())['configuration']
    if any(source_config.get(k) != v for k, v in profile['source_configuration_expected'].items()):
        raise ValueError('Selected runtime differs from source package execution profile')
    reviews = json.loads(dossier.read_text())['decisions']
    # Re-render every supplied raster from exact PDF bytes; never trust an image hash alone.
    import pdfplumber
    from io import BytesIO
    checked = {}
    with pdfplumber.open(package/'original.pdf') as pdf:
        for review in reviews:
            receipt = review['tool_receipt']
            if receipt['source_pdf_sha256'] != snapshot['source_pdf_sha256'] or receipt['page'] != page:
                raise ValueError('Raster review source/page is stale')
            box = review['crop_source_bbox']
            # Exact recipe used by the review UI evidence, not an approximately
            # equivalent full-page raster crop with different pixel rounding.
            crop = pdf.pages[page-1].crop(tuple(box)).to_image(resolution=240).original
            data = BytesIO(); crop.save(data, format='PNG')
            if sha(data.getvalue()) != review['render_sha256'] or sha(Path(review['render_path']).read_bytes()) != review['render_sha256']:
                raise ValueError('Original source crop pixels disagree with reviewed image')
            checked[receipt['cell_id']] = review
    try:
        result = validate_feedback(snapshot, read_rows(feedback), checked)
    except ValueError as error:
        result = {'contract_version':'powerlex.native-table-cell-revalidation.v1', 'status':'rejected',
                  'reason':str(error), 'source_pdf_sha256':snapshot['source_pdf_sha256'], 'page':page,
                  'jobs':[{'status':'rejected','reason':str(error)}], 'page_quality_cleared':False,
                  'answer_publication_eligible':False, 'actual_model_inference_calls':0,'remote_operations':0}
    result['lineage'] = {'package_manifest_sha256':sha((package/'l1-package.json').read_bytes()),
                         'feedback_sha256':sha(feedback.read_bytes()), 'raster_dossier_sha256':sha(dossier.read_bytes()),
                         'consumer_sha256':sha(Path(__file__).read_bytes()), 'consumer_source_artifact':'consumer-source.py', 'parser_profile':profile,
                         'source_snapshot_sha256':sha(canonical(snapshot))}
    result['observed_at'] = datetime.now(timezone.utc).isoformat()
    result['signature'] = sha(canonical(result))
    output.mkdir(parents=True)
    (output/'consumer-source.py').write_bytes(Path(__file__).read_bytes())
    (output/'source-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
    (output/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for table in result.get('tables', []):
        (output/('table-'+sha(table['table_version_id'])[:16]+'.json')).write_text(json.dumps(table, ensure_ascii=False, indent=2)+'\n')
    (output/'repair-job-results.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in result['jobs']))
    return result


def revalidate(package, parser_root, feedback, dossier, output, page=5):
    """Every submitted repair receives a terminal receipt, including preflight."""
    package,parser_root,feedback,dossier,output=map(lambda p:Path(p).resolve(),(package,parser_root,feedback,dossier,output))
    if output.exists()or output.is_relative_to(package)or package.is_relative_to(output)or output.is_relative_to(parser_root)or parser_root.is_relative_to(output)or feedback.is_relative_to(output)or dossier.is_relative_to(output):
        raise ValueError('Choose a new immutable output outside parent, source runtime and inputs')
    try:
        return _revalidate_checked(package,parser_root,feedback,dossier,output,page)
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError)as error:
        if output.exists():raise
        # Integrity/preflight failure does not attest to successful source
        # capture. Record only bytes actually supplied, never approved pointers.
        inputs={}
        for key,path in [('package_manifest',Path(package)/'l1-package.json'),('feedback',feedback),('dossier',dossier)]:
            try:inputs[key+'_sha256']=sha(Path(path).read_bytes())
            except OSError:inputs[key+'_sha256']=None
        result={'contract_version':'powerlex.native-table-cell-revalidation.v1','status':'rejected',
                'failure_stage':'source_raster_profile_preflight','preflight_completed':False,
                'reason':type(error).__name__+': '+str(error),'submitted_page':page,
                'jobs':[{'status':'rejected','repair_state':'source_revalidation_rejected','promotes_page':False,
                         'reason':type(error).__name__+': '+str(error)}],
                'lineage':{**inputs,'consumer_sha256':sha(Path(__file__).read_bytes()),
                           'consumer_source_artifact':'consumer-source.py'},
                'page_quality_cleared':False,'answer_publication_eligible':False,
                'actual_model_inference_calls':0,'remote_operations':0}
        result['signature']=sha(canonical(result));output.mkdir(parents=True)
        (output/'consumer-source.py').write_bytes(Path(__file__).read_bytes())
        (output/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        (output/'repair-job-results.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n'for row in result['jobs']))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'parser-root', 'feedback', 'dossier', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--page', type=int, required=True)
    args = parser.parse_args()
    result = revalidate(args.package, args.parser_root, args.feedback, args.dossier, args.output, args.page)
    print(json.dumps({key: result.get(key) for key in ('status','signature','residual_gates','page_quality_cleared','answer_publication_eligible')}))
    sys.exit(0 if result['status'] == 'validated' else 3)
