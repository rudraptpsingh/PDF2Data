"""Generate bounded table-cell questions from a source-bound held-page inventory.

Extraction origin and the fresh independent proof runtime are explicit.  This
queue does not approve pages; saved choices are consumed by the existing table
feedback consumer and receive immutable terminal results.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from .proof_identity import canonical, sha, inspect_pdf2data_profile


def rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def cell_key(source, page, table, cell):
    return sha(canonical([source, page, table['table_version_id'], cell['id'],
                          cell['selected_cell_sha256'], cell['independent_cell_sha256'], cell['bbox']]))


def feedback_key(row):
    return sha(canonical([row.get('source_pdf_sha256'), row.get('page'), row.get('table_version_id'),
                          row.get('cell_id'), row.get('selected_cell_sha256'),
                          row.get('independent_cell_sha256'), row.get('bbox')]))


def pattern(cell):
    a, b = cell['text'] or '', cell['independent_text']
    compact = lambda s: ''.join(c for c in s if not c.isspace())
    if not a.strip() or not b.strip(): return 'empty_reader_or_cell_boundary'
    if compact(a) == compact(b): return 'spacing_or_line_wrap'
    if compact(a) == compact(b.replace('\x02', '-')): return 'source_control_hyphen'
    return 'reading_order_or_content_conflict'


def ordered_candidates(candidates, scan_limit):
    """Sample sources before deep pages, then reserve room for multi-gate cells.

    Layout-only pages are first, but a bounded question run must not spend its
    entire scan on proven cells in a single long compendium.
    """
    depth=Counter();ranked=[]
    for sequence,row in enumerate(candidates):
        source=row['source_pdf_sha256'];ranked.append((depth[source],sequence,row));depth[source]+=1
    easy=[r for _,_,r in sorted(ranked)if r.get('only_layout_gate_remaining')]
    other=[r for _,_,r in sorted(ranked)if not r.get('only_layout_gate_remaining')]
    first=min(len(easy),max(1,scan_limit//2))if other else len(easy)
    return easy[:first]+other+easy[first:]


def _checked_page(row, origins):
    from .legal_l1 import verify_document
    package = Path(row.get('l1_package_path') or row.get('package') or row.get('l1_package')).resolve()
    verify_document(package)
    for key, path in [('base_manifest_sha256', package/'l1-package.json'),
                      ('base_quality_sha256', package/'quality.json')]:
        if row.get(key) and sha(path.read_bytes()) != row[key]: raise ValueError(key+' changed')
    source = package/'original.pdf'
    if sha(source.read_bytes()) != row['source_pdf_sha256']: raise ValueError('Original PDF changed')
    page = next(p for p in rows(package/'extraction/pages.jsonl') if p['page'] == row['page'])
    if page['text_sha256'] != row['page_text_sha256'] or sha(page['text']) != page['text_sha256']:
        raise ValueError('Selected page text changed')
    config = json.loads((package/'source-receipt.json').read_text())['configuration']
    matched = [p for p, profile in origins if all(config.get(k) == v for k, v in
                                                 profile['source_configuration_expected'].items())]
    if len(matched) != 1: raise ValueError('Missing or ambiguous original extraction runtime')
    quality = row.get('effective_quality')
    # The input inventory explicitly uses sorted-key JSON with default spaces.
    if quality is not None and (sha(json.dumps(quality,ensure_ascii=False,sort_keys=True)) != row.get('effective_quality_sha256') or
                               quality.get('page_text_sha256') != page['text_sha256'] or
                               quality.get('source_pdf_sha256') != row['source_pdf_sha256']):
        raise ValueError('Effective page-quality identity changed')
    return package, page, matched[0]


def _capture(package, origin, proof_root, page):
    code = ('import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]+"/src");'
            'from local_vector_batch.native_table_feedback import capture_source;'
            'print(json.dumps(capture_source(Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]))))')
    run = subprocess.run([sys.executable, '-B', '-c', code, str(proof_root), str(package), str(origin), str(page)],
                         capture_output=True, text=True, timeout=180)
    if run.returncode: raise ValueError('Fresh source capture failed: '+run.stderr[-600:])
    return json.loads(run.stdout)


def generate_queue(held_manifest, source_runtimes, proof_runtime, output, *, limit=8, scan_limit=80,
                   reviewed=(), category='native_table_layout', cache_dir=None):
    if type(limit) is not int or not 1 <= limit <= 100 or type(scan_limit) is not int or not 1 <= scan_limit <= 2000:
        raise ValueError('Bounded question/page limits required')
    output = Path(output).resolve()
    if output.exists(): raise ValueError('Choose a new immutable output directory')
    proof_runtime = Path(proof_runtime).resolve(); profile = inspect_pdf2data_profile(proof_runtime)
    origins = [(Path(p).resolve(), inspect_pdf2data_profile(p)) for p in source_runtimes]
    if not origins: raise ValueError('At least one explicit original source runtime required')
    all_rows = rows(held_manifest)
    candidates = [r for r in all_rows if r.get('category') == category]
    # Preserve input order within each priority; the source inventory remains immutable.
    candidates=ordered_candidates(candidates,scan_limit)
    answered = {feedback_key(r) for p in reviewed for r in rows(p)
                if r.get('kind') == 'extraction_table_cell_feedback' and
                r.get('decision') in {'selected','independent','corrected','unclear'}}
    cache = Path(cache_dir).resolve() if cache_dir else output/'source-cache'
    output.mkdir(parents=True); cache.mkdir(parents=True, exist_ok=True)
    (output/'review-manifest.jsonl').write_text('');(output/'queue-audit.jsonl').write_text('')
    audit, questions, counts, observed_patterns = [], [], Counter(), Counter()
    seen_pages, seen_cells = set(), set()
    for row in candidates:
        page_id = (row['source_pdf_sha256'], row['page'])
        if page_id in seen_pages: continue
        if len(seen_pages) >= scan_limit or len(questions) >= limit: break
        seen_pages.add(page_id)
        base = {'source_pdf_sha256': row['source_pdf_sha256'], 'page': row['page'],
                'source_id': row.get('source_id')}
        try:
            package, page, origin = _checked_page(row, origins)
            fingerprint = {'parent_manifest_sha256':sha((package/'l1-package.json').read_bytes()),
                'source_pdf_sha256':row['source_pdf_sha256'], 'page':row['page'],
                'page_text_sha256':page['text_sha256'], 'proof_profile_sha256':profile['profile_sha256'],
                'origin_profile_sha256':inspect_pdf2data_profile(origin)['profile_sha256']}
            key = sha(canonical(fingerprint)); cached = cache/(key+'.json')
            if cached.exists():
                saved = json.loads(cached.read_text())
                if saved['fingerprint'] != fingerprint or sha(canonical(saved['snapshot'])) != saved['snapshot_sha256']:
                    raise ValueError('Source cache fingerprint/content changed')
                snapshot = saved['snapshot']
            else:
                snapshot = _capture(package, origin, proof_runtime, row['page'])
                save(cached, {'fingerprint':fingerprint, 'snapshot':snapshot,
                              'snapshot_sha256':sha(canonical(snapshot))})
            if snapshot['source_pdf_sha256'] != row['source_pdf_sha256'] or snapshot['page_text_sha256'] != page['text_sha256']:
                raise ValueError('Fresh source capture identity mismatch')
            if len(snapshot['tables'])!=len(snapshot['raw_tables'])or any(t['unresolved_grid_slots']for t in snapshot['tables']):
                raise ValueError('Whole-page native table cardinality/grid is not consumable')
            source_questions = 0
            for table in snapshot['tables']:
                if table['unresolved_grid_slots']:
                    audit.append({**base,'status':'unreviewable','reason':'unresolved_native_grid_slots',
                                  'table_version_id':table['table_version_id']}); counts['unreviewable_grid'] += 1
                    continue
                for cell in table['cell_spans']:
                    key = cell_key(row['source_pdf_sha256'], row['page'], table, cell)
                    if key in seen_cells: continue
                    seen_cells.add(key)
                    detail = {**base,'table_version_id':table['table_version_id'],'cell_id':cell['id'],'focus_key':key}
                    if cell.get('readers_agree') and not cell.get('glyph_audit',{}).get('issues') and not cell.get('independent_glyph_audit',{}).get('issues'):
                        counts['already_proved_cells'] += 1
                        if cell.get('independent_reader_mapping',{}).get('status')=='source_proved':
                            audit.append({**detail,'status':'resolved','origin':'automatic_source_bound_reader_rule',
                                'rule_kinds':[r['kind']for r in cell['independent_reader_mapping']['repairs']],
                                'mapping_proof_sha256':sha(canonical(cell['independent_reader_mapping']))})
                            counts['automatically_resolved_cells']+=1
                        continue
                    label = pattern(cell); observed_patterns[label] += 1
                    if key in answered:
                        audit.append({**detail,'status':'resolved','reason':'exact_saved_feedback','pattern':label})
                        counts['resolved_cells'] += 1; continue
                    if len(questions) >= limit or source_questions>=2: continue
                    folder = output/('question-'+str(len(questions)+1).zfill(3)); folder.mkdir()
                    import pdfplumber
                    with pdfplumber.open(package/'original.pdf') as pdf:
                        box = [max(0,cell['bbox'][0]-2), max(0,cell['bbox'][1]-2),
                               min(table['page_width'],cell['bbox'][2]+2), min(table['page_height'],cell['bbox'][3]+2)]
                        image = pdf.pages[row['page']-1].crop(tuple(box)).to_image(resolution=240).original
                        data=BytesIO(); image.save(data,format='PNG'); raster=data.getvalue()
                    (folder/'source-cell.png').write_bytes(raster)
                    (folder/'selected.txt').write_text(cell['text'] or '')
                    save(folder/'independent.json', {'text':cell['independent_text'],'engine':'fresh_original_PDFium_cell'})
                    target = {**detail,'row':cell['row'],'column':cell['column'],'bbox':cell['bbox'],
                        'selected_cell_sha256':cell['selected_cell_sha256'],
                        'independent_cell_sha256':cell['independent_cell_sha256'],
                        'selected_text':cell['text'] or '', 'independent_text':cell['independent_text'],
                        'table_geometry_sha256':table['table_geometry_sha256'],
                        'independent_source_rectangle':cell.get('independent_source_rectangle'),
                        'render_path':str(folder/'source-cell.png'),'render_sha256':sha(raster),
                        'crop_source_bbox':box,'render_dpi':240,'pattern':label,
                        'source_runtime':str(origin),'proof_runtime':str(proof_runtime),
                        'source_fingerprint':fingerprint,'source_snapshot_path':str(cached)}
                    save(folder/'question.json',target)
                    question={'title':row.get('title') or row.get('source_id') or package.name,
                        'source_pdf':str(package/'original.pdf'),'source_pdf_sha256':row['source_pdf_sha256'],
                        'source_url':row.get('source_url',''),'page':row['page'],'l1_package':str(package),
                        'selected_text':str(folder/'selected.txt'),'selected_text_sha256':cell['selected_cell_sha256'],
                        'independent_json':str(folder/'independent.json'),'independent_text_sha256':cell['independent_cell_sha256'],
                        'prompt':'Inspect the marked original table cell. Which saved reading matches the printed cell? '
                                 'Use the table-cell controls; uncertainty keeps the cell held.',
                        'focus':{'kind':'formatting','selected_excerpt':(cell['text'] or '')[:600],
                            'independent_excerpt':cell['independent_text'][:600],
                            'boxes':[[box[0]/table['page_width'],box[1]/table['page_height'],
                                      box[2]/table['page_width'],box[3]/table['page_height']]]},
                        'selected_meta':{'engine':'native_pdf_geometry','question_kind':'table_cell'},
                        'target_cell':target,'question_id':key,'page_queued_discrepancies':1}
                    questions.append(question);source_questions += 1; counts['queued_cells'] += 1
                    audit.append({**detail,'status':'queued','pattern':label,'render_sha256':sha(raster)})
            if not source_questions:
                audit.append({**base,'status':'no_new_questions','reason':'all_cells_proved_resolved_or_grid_held'})
            counts['scanned_pages'] += 1
        except (OSError,ValueError,KeyError,TypeError,StopIteration,subprocess.SubprocessError) as error:
            audit.append({**base,'status':'unreviewable','reason':type(error).__name__+': '+str(error)[:800]})
            counts['failed_pages'] += 1
        # Durable progress survives interruption and never reports a failed page as queued.
        (output/'review-manifest.jsonl').write_text(''.join(canonical(q)+'\n' for q in questions))
        (output/'queue-audit.jsonl').write_text(''.join(canonical(a)+'\n' for a in audit))
    result={'contract_version':'pdf2data.auto-held-table-review.v1','status':'complete',
        'held_manifest_sha256':sha(Path(held_manifest).read_bytes()),'category':category,
        'category_pages_in_input':len(candidates),'layout_only_pages_in_input':sum(bool(r.get('only_layout_gate_remaining'))for r in candidates),
        'question_limit':limit,'page_scan_limit':scan_limit,'counts':dict(counts),
        'bounded_scanned_unproved_cell_patterns':dict(observed_patterns),
        'pattern_count_scope':'only scanned pages; not extrapolated to the whole held category',
        'proof_profile':profile,'source_runtimes':[str(p)for p,_ in origins],
        'manifest_path':str(output/'review-manifest.jsonl'),'audit_path':str(output/'queue-audit.jsonl'),
        'manifest_sha256':sha((output/'review-manifest.jsonl').read_bytes()),
        'feedback_contract':'extraction_table_cell_feedback','apply_api':'native_table_review_queue.consume_answers',
        'page_quality_cleared':False,'legal_review':'not_run','remote_operations':0,
        'actual_model_inference_calls':0,'completed_at':datetime.now(timezone.utc).isoformat()}
    save(output/'validation.json',result);return result


def consume_answers(manifest, feedback, output):
    """Consume actual saved choices together per page, then attempt all gates."""
    output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a new immutable output directory')
    output.mkdir(parents=True);questions=rows(manifest);saved=rows(feedback);jobs=[];groups={};consumed=0
    for q in questions:
        target=q['target_cell'];matching=[r for r in saved if feedback_key(r)==target['focus_key']]
        if not matching:continue
        consumed+=1
        stable={canonical({k:v for k,v in r.items()if k!='created_at'})for r in matching}
        if len(stable)!=1:
            jobs.append({'question_id':q['question_id'],'status':'rejected','reason':'conflicting_saved_choices'});continue
        actual=matching[-1]
        try:
            if inspect_pdf2data_profile(target['proof_runtime'])['profile_sha256']!=target['source_fingerprint']['proof_profile_sha256']:
                raise ValueError('proof_epoch_changed')
            if sha((Path(q['l1_package'])/'l1-package.json').read_bytes())!=target['source_fingerprint']['parent_manifest_sha256']:
                raise ValueError('parent_manifest_changed')
            if cell_key(q['source_pdf_sha256'],q['page'],target,{'id':target['cell_id'],**target})!=target['focus_key']:
                raise ValueError('question_focus_changed')
        except (OSError,ValueError,KeyError,TypeError)as error:
            jobs.append({'question_id':q['question_id'],'status':'rejected','reason':str(error)});continue
        key=(q['l1_package'],q['page'],target['source_runtime'],target['proof_runtime'])
        groups.setdefault(key,[]).append((q,actual))
    for index,(key,answers) in enumerate(groups.items()):
        package,page,origin,proof=key;folder=output/('page-'+str(index+1).zfill(3));folder.mkdir()
        receipts=[];reviews=[];originals=[]
        for q,actual in answers:
            target=q['target_cell'];receipt=dict(actual);originals.append(actual)
            # A local UI saved-choice claim, not authentication of a legal reviewer.
            receipt['note']=canonical({'origin':'human_user_current_thread','question_id':q['question_id'],
                'human_reply':actual.get('note')or actual['decision'],'render_sha256':target['render_sha256'],
                'original_feedback_sha256':sha(canonical(actual))})
            receipts.append(receipt)
            review={k:target[k]for k in ('render_path','render_sha256','crop_source_bbox','render_dpi')}
            review['tool_receipt']=receipt;reviews.append(review)
        save(folder/'original-saved-feedback.json',originals)
        (folder/'feedback.jsonl').write_text(''.join(canonical(r)+'\n'for r in receipts))
        save(folder/'dossier.json',{'decisions':reviews})
        code='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]+"/src")
from local_vector_batch.native_table_feedback import revalidate
from local_vector_batch.native_table_regions import build_version
package,origin,feedback,dossier,output=map(Path,sys.argv[2:7]);page=int(sys.argv[7])
cells=revalidate(package,origin,feedback,dossier,output/'cell-revalidation',page)
result={'cell_status':cells['status'],'reason':cells.get('reason'),'page_quality_cleared':False,'answer_publication_eligible':False}
if cells['status']=='validated':
 try:
  whole=build_version(package,origin,feedback,dossier,page,output/'whole-page-proof')
  result.update(page_proof_status=whole['status'],reason=whole.get('reason'),new_l1_package_path=whole.get('new_l1_package_path'),page_quality_cleared=whole['status']=='passed')
 except Exception as error:
  result.update(page_proof_status='rejected',reason=type(error).__name__+': '+str(error))
print(json.dumps(result))
'''
        args=[proof,package,origin,str(folder/'feedback.jsonl'),str(folder/'dossier.json'),str(folder),str(page)]
        try:
            run=subprocess.run([sys.executable,'-B','-c',code,*args],capture_output=True,text=True,timeout=360)
            if run.returncode:raise ValueError(run.stderr[-800:])
            result=json.loads(run.stdout)
            result.update(status='source_quality_cleared'if result['page_quality_cleared']else
                          'rejected'if result['cell_status']=='rejected'else'held',
                          question_ids=[q['question_id']for q,_ in answers],page=page,
                          cell_revalidation_path=str(folder/'cell-revalidation/validation.json'))
            save(folder/'terminal-result.json',result);jobs.append(result)
        except (OSError,ValueError,subprocess.SubprocessError)as error:
            result={'question_ids':[q['question_id']for q,_ in answers],'status':'rejected','reason':str(error)[:800],
                    'page_quality_cleared':False,'answer_publication_eligible':False}
            save(folder/'terminal-result.json',result);jobs.append(result)
    result={'contract_version':'pdf2data.auto-held-table-review-consumption.v1','jobs':jobs,
        'saved_answers_consumed':consumed,'pending_questions':len(questions)-consumed,
        'pages_source_quality_cleared':sum(j.get('page_quality_cleared')is True for j in jobs),
        'page_quality_cleared':False,'legal_review':'not_run','remote_operations':0,'actual_model_inference_calls':0}
    save(output/'validation.json',result);return result
