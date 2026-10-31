"""Local feedback controller over existing extraction, quality and staged repair.

An unknown reference is not zero error. Staged improvements never mutate source
packages, publish indexes or grant legal/source-visual approval.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from .hashing import file_sha256
from .quality_calibration import evaluate_calibration
from .quality_routes import matching_routes

SCHEMA='pdf2data.feedback-controller.v1'


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return sha256(canonical(value).encode('utf-8')).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def rows(path):
    with Path(path).open('r',encoding='utf-8',newline='\n')as stream:
        for line in stream:
            if line.strip(' \t\r\n'):yield json.loads(line)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


@contextmanager
def ledger(output):
    from filelock import FileLock
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    with FileLock(str(output/'controller.lock'),timeout=0):
        db=sqlite3.connect(output/'controller.sqlite');db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
              sha TEXT PRIMARY KEY, source TEXT NOT NULL, package TEXT,
              definition TEXT NOT NULL, state TEXT NOT NULL, last_signature TEXT,
              last_result TEXT, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, sha TEXT NOT NULL,
              signature TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback (
              key TEXT PRIMARY KEY, sha TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback_history (
              id TEXT PRIMARY KEY, target TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_aliases (
              sha TEXT NOT NULL, source TEXT NOT NULL, definition TEXT NOT NULL,
              PRIMARY KEY(sha,source));
            CREATE TABLE IF NOT EXISTS document_metrics (
              sha TEXT PRIMARY KEY, payload TEXT NOT NULL, measured INTEGER NOT NULL,
              reviewed_pages INTEGER NOT NULL,unlabelled_pages INTEGER NOT NULL,
              reviewed_cells INTEGER NOT NULL,machine_pages INTEGER NOT NULL,
              passed_pages INTEGER NOT NULL,unresolved_pages INTEGER NOT NULL,
              review_complete INTEGER NOT NULL,result_path TEXT,result_sha TEXT);
            ''')
            db.execute('INSERT OR IGNORE INTO source_aliases SELECT sha,source,definition FROM documents')
            # Migrate old pilot rows one at a time; do not materialize a corpus
            # of page/cell diagnostics merely to calculate progress.
            pending=db.execute('SELECT sha,last_result FROM documents WHERE last_result IS NOT NULL AND sha NOT IN(SELECT sha FROM document_metrics)')
            for row in pending:
                value=json.loads(row['last_result']);_save_metrics(db,row['sha'],value,None,None)
            db.commit()
            yield db,output
            db.commit()
        finally:db.close()


def initialize(manifest,output):
    manifest=Path(manifest).resolve();known=[]
    for row in rows(manifest):
        source=Path(row.get('source_pdf')or row.get('path')or '')
        if not source.is_absolute():source=manifest.parent/source
        source=source.resolve()
        if not source.is_file():raise ValueError('Missing original PDF: '+str(source))
        before=source.stat();actual=file_sha256(source);after=source.stat()
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise ValueError('Original changed during inventory')
        expected=row.get('source_pdf_sha256')or row.get('sha256')
        if expected and expected!=actual:raise ValueError('Original PDF hash differs from inventory receipt')
        package=Path(row['package'])if row.get('package')else None
        if package:
            if not package.is_absolute():package=manifest.parent/package
            package=package.resolve()
            if (package/'l1-package.json').is_file():package=package/'extraction'
            if Path(output).resolve().is_relative_to(package):raise ValueError('Controller output must be outside source packages')
        if Path(output).resolve()==source:raise ValueError('Controller output overlaps the original')
        definition={**row,'source_pdf':str(source),'package':str(package)if package else None}
        def resolve(value):
            path=Path(value)
            return str((path if path.is_absolute()else manifest.parent/path).resolve())
        for key in ('native_receipts','column_order_receipts','region_receipts','independent_receipts'):
            if key in definition:
                definition[key]={k:[resolve(p)for p in v]if isinstance(v,list)else resolve(v)for k,v in definition[key].items()}
        if definition.get('review_sidecar'):definition['review_sidecar']=resolve(definition['review_sidecar'])
        known.append((actual,source,package,definition))
    if not known:raise ValueError('Control inventory is empty')
    with ledger(output)as(db,base):
        added=0
        for actual,source,package,row in known:
            db.execute('INSERT OR IGNORE INTO source_aliases VALUES(?,?,?)',(actual,str(source),canonical(row)))
            old=db.execute('SELECT definition,package FROM documents WHERE sha=?',(actual,)).fetchone()
            if old:
                # Re-inventory cannot silently change a watched package/version.
                prior=json.loads(old['definition'])
                if str(package or '')!=str(prior.get('package')or ''):raise ValueError('Inventory package changed; use a new controller workspace')
                updated={**prior}
                for key in ('native_receipts','column_order_receipts','region_receipts','independent_receipts','review_sidecar'):
                    if key in row:updated[key]=row[key]
                if row.get('source_receipt_verified')is True:updated['source_receipt_verified']=True
                if updated!=prior:
                    next_state='source_binding_required'if updated.get('source_receipt_verified')is False else 'evidence_pending'
                    db.execute('UPDATE documents SET definition=?,state=?,last_signature=NULL,updated=?WHERE sha=?',(canonical(updated),next_state,now(),actual))
                continue
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                       (actual,str(source),str(package)if package else None,canonical(row),
                        'source_binding_required'if row.get('source_receipt_verified')is False else 'queued',None,None,now()));added+=1
        db.commit()
        return dict(schema_version=SCHEMA,output=str(base),documents=db.execute('SELECT count(*)FROM documents').fetchone()[0],
                    added_documents=added,existing_or_duplicate_rows=len(known)-added,
                    duplicate_sources=db.execute('SELECT count(*)FROM source_aliases').fetchone()[0]-db.execute('SELECT count(*)FROM documents').fetchone()[0],
                    source_content_reviewed=False,publication_eligible=False)


def add_feedback(output,reference_path):
    if not(Path(output)/'controller.sqlite').is_file():raise ValueError('Controller is not initialized')
    references=list(rows(reference_path));evaluate_calibration([],[r for r in references if r.get('scope')!='cell'])
    if any(r.get('scope')!='cell'and r.get('split')!='calibration'for r in references):
        raise ValueError('Holdout labels are evaluation-only; use pdf-quality --references, not control feedback')
    with ledger(output)as(db,base):
        prepared=[]
        for reference in references:
            doc=db.execute('SELECT sha,source,package FROM documents WHERE sha=?',(reference['source_pdf_sha256'],)).fetchone()
            if not doc or not doc['package']:raise ValueError('Feedback source has no watched extraction')
            if file_sha256(Path(doc['source']))!=doc['sha']:raise ValueError('Feedback original PDF changed')
            if (reference.get('scope')not in ('cell','whole_page')or reference.get('visual_evidence_kind')!='original_pdf'
                    or reference.get('visual_evidence_sha256')!=doc['sha']):
                raise ValueError('Feedback requires explicit scope and the hash-bound original PDF')
            current={p['page']:p for p in rows(Path(doc['package'])/'pages.jsonl')}
            page=current.get(reference['page'])
            if not page or page['text_sha256']!=reference['page_text_sha256']:raise ValueError('Feedback refers to a stale extraction version')
            if not isinstance(reference.get('reviewer'),str)or not reference['reviewer'].strip():raise ValueError('Feedback requires reviewer identity')
            identity={k:reference[k]for k in ('source_pdf_sha256','page','page_text_sha256')}
            if reference['scope']=='cell':
                box=reference.get('source_bbox')
                if not isinstance(reference.get('reference_text'),str)or not isinstance(box,list)or len(box)!=4 or any(type(v)not in(int,float)for v in box):
                    raise ValueError('Cell feedback requires exact reference_text and source_bbox in PDF points')
                tables=json.loads((Path(doc['package'])/'tables.json').read_text(encoding='utf-8'))
                cells=[c for t in tables if t['page']==reference['page']for c in t.get('cell_spans',[])if c.get('bbox')==box]
                if len(cells)!=1:raise ValueError('Cell feedback source box is absent or ambiguous')
                identity['source_bbox']=box
            key=digest(identity)
            old=db.execute('SELECT payload FROM feedback WHERE key=?',(key,)).fetchone()
            if old:
                db.execute('INSERT OR IGNORE INTO feedback_history VALUES(?,?,?,?)',(digest(json.loads(old[0])),key,old[0],now()))
            if old and json.loads(old[0])!=reference:
                if reference.get('supersedes_feedback_sha256')!=digest(json.loads(old[0]))or not reference.get('review_reason'):
                    raise ValueError('Changed feedback requires the prior feedback hash and an explicit review_reason')
            prepared.append((key,reference['source_pdf_sha256'],canonical(reference),now()))
        db.executemany('INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(key)DO UPDATE SET payload=excluded.payload,created=excluded.created',prepared)
        db.executemany('INSERT OR IGNORE INTO feedback_history VALUES(?,?,?,?)',
                       [(digest(json.loads(payload)),key,payload,created)for key,_,payload,created in prepared])
        db.executemany("UPDATE documents SET state='feedback_pending',last_signature=NULL,updated=?WHERE sha=?",
                       [(now(),sha)for sha in {r[1]for r in prepared}])
        return dict(schema_version=SCHEMA,feedback_added=len(prepared),publication_eligible=False,
                    feedback_effect='measurement_and_review_routing_not_source_or_legal_approval')


def residuals(report,references,tables=None):
    """Target is all required gates passing; unknown evidence stays explicit."""
    page_refs=[r for r in references if r.get('scope')!='cell']
    by_ref={(r['source_pdf_sha256'],r['page'],r['page_text_sha256']):r for r in page_refs}
    cell_refs=[r for r in references if r.get('scope')=='cell']
    page_results=[];failed=unknown=review_errors=labelled=reviewed_cells=0
    for page in report['results']:
        vector={}
        for name,component in page.get('components',{}).items():
            state=component.get('state','unknown')
            if state not in ('pass','fail','unknown'):raise ValueError('Unknown quality component state')
            error=0 if state=='pass'else 1 if state=='fail'else None
            vector[name]=dict(target='pass',observed=state,error=error)
            quantitative={}
            for metric in ('word_recall','word_precision','word_order'):
                if type(component.get(metric))in (float,int):quantitative[metric+'_loss']=1-component[metric]
            if type(component.get('unaccounted_ink_pixels'))is int:
                quantitative['unaccounted_ink_pixels']=component['unaccounted_ink_pixels']
            if quantitative:vector[name]['measured_residuals']=quantitative
            if isinstance(component.get('selected'),list)and isinstance(component.get('independent'),list):
                selected,independent=component['selected'],component['independent']
                mismatch=[]
                for tag,a,b,c,d in SequenceMatcher(None,selected,independent,autojunk=False).get_opcodes():
                    if tag!='equal':mismatch.append(dict(kind=tag,selected_range=[a,b],independent_range=[c,d],
                                                       selected_tokens=selected[a:b],independent_tokens=independent[c:d]))
                vector[name]['token_differences']=mismatch
                vector[name]['comparison_basis']='independent_reader_disagreement_not_ground_truth'
                vector[name]['same_token_multiset']=Counter(selected)==Counter(independent)
            failed+=state=='fail';unknown+=state=='unknown'
        key=(page['source_pdf_sha256'],page['page'],page['page_text_sha256']);reference=by_ref.get(key)
        reference_error=None
        if reference:
            labelled+=1;reference_error=int(not reference['acceptable']);review_errors+=reference_error
        cell_errors=[]
        for ref in cell_refs:
            if ref['source_pdf_sha256']!=page['source_pdf_sha256']or ref['page']!=page['page']:continue
            cells=[c for t in tables or []if t['page']==page['page']for c in t.get('cell_spans',[])if c.get('bbox')==ref['source_bbox']]
            error=int(cells[0].get('text')!=ref['reference_text'])if len(cells)==1 else None
            reviewed_cells+=len(cells)==1;review_errors+=error==1;unknown+=error is None
            cell_errors.append(dict(reference_id=digest(ref),source_bbox=ref['source_bbox'],expected=ref['reference_text'],
                observed=cells[0].get('text')if len(cells)==1 else None,error=error,
                scope='one_source_cell_not_whole_page',matching_basis='exact_unchanged_original_pdf_cell_box'))
        page_results.append(dict(page=page['page'],page_text_sha256=page['page_text_sha256'],
            gate_residuals=vector,source_review_error=reference_error,
            cell_reference_residuals=cell_errors,
            unresolved_visual_regions=page.get('unresolved_visual_regions',[]),
            suggested_actions=[dict(id=r['id'],action=r['action'])for r in matching_routes(page)],
            machine_outcome=page['outcome']))
    return dict(pages=page_results,loss_vector=[failed,review_errors,unknown],
                loss_order=['failed_required_gates','source_review_errors','unknown_evidence_checks'],
                source_reviewed_pages=labelled,source_review_unlabelled_pages=len(page_results)-labelled,
                source_reviewed_cells=reviewed_cells,
                calibration=evaluate_calibration(report['results'],page_refs),
                reference_policy='source_visual_labels_are_version_bound_unlabelled_error_is_unknown_not_zero')


def non_regressing(before,after):
    """No failed/unknown gate or known review error may worsen on any page."""
    old={p['page']:p for p in before['pages']};new={p['page']:p for p in after['pages']}
    if set(old)!=set(new):return False
    improved=False;rank={'pass':0,'unknown':1,'fail':2}
    for number,page in old.items():
        other=new[number]
        if set(page['gate_residuals'])!=set(other['gate_residuals']):return False
        for name,component in page['gate_residuals'].items():
            a=rank[component['observed']];b=rank[other['gate_residuals'][name]['observed']]
            if b>a:return False
            if a==2 and b==1:return False  # A known failure becoming unknown is not a repair.
            improved|=b<a
        a,b=page['source_review_error'],other['source_review_error']
        # A new text version does not inherit a previous visual review.
        if a is not None and b is None:return False
        if a==0 and b==1:return False
        improved|=a==1 and b==0
        prior_cells={r['reference_id']:r for r in page.get('cell_reference_residuals',[])}
        next_cells={r['reference_id']:r for r in other.get('cell_reference_residuals',[])}
        if set(prior_cells)!=set(next_cells):return False
        for identity,reference in prior_cells.items():
            a,b=reference['error'],next_cells[identity]['error']
            if a is not None and b is None or a==0 and b==1:return False
            improved|=b==0 and a!=0
    return improved


def command(arguments,folder,label):
    cmd=[sys.executable,'-m','local_vector_batch',*map(str,arguments)]
    write(folder/(label+'-command.json'),dict(argv=cmd))
    try:result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=600)
    except subprocess.TimeoutExpired as error:raise RuntimeError(label+' exceeded its 600 second command budget')from error
    (folder/(label+'-stdout.txt')).write_text(result.stdout,encoding='utf-8')
    (folder/(label+'-stderr.txt')).write_text(result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError(label+' failed; inspect the retained command logs')
    return json.loads(result.stdout)


def _assessment(package,source,folder,references):
    feedback=folder/'references.jsonl'
    feedback.write_text(''.join(canonical(r)+'\n'for r in references),encoding='utf-8')
    path=folder/'quality.json'
    args=['pdf-quality','--package',package,'--source-pdf',source,'--output',path,'--json']
    # Calibrate in the controller after source/version checks. References never
    # replace any of the source-raster/independent-reader quality components.
    command(args,folder,'quality')
    report=json.loads(path.read_text(encoding='utf-8'))
    pages={p['page']:p for p in rows(package/'pages.jsonl')}
    if len(report['results'])!=len(pages)or {p['page']for p in report['results']}!=set(pages):
        raise ValueError('Quality receipt does not cover every physical page')
    source_sha=file_sha256(source)
    if report.get('source_pdf_sha256')!=source_sha:raise ValueError('Quality receipt source identity changed')
    for page in report['results']:
        original=pages[page['page']]
        if (page['source_pdf_sha256']!=source_sha or page['page_text_sha256']!=original['text_sha256']
                or sha256(original['text'].encode('utf-8')).hexdigest()!=original['text_sha256']):
            raise ValueError('Quality receipt text version changed')
    tables=json.loads((package/'tables.json').read_text(encoding='utf-8'))if any(r.get('scope')=='cell'for r in references)else []
    return report,residuals(report,references,tables)


def _signature(source,package,definition,references,implementation,ocr,max_rounds):
    stamps=[]
    if package and package.is_dir():
        for path in sorted(p for p in package.rglob('*')if p.is_file()):
            s=path.stat();stamps.append((str(path.relative_to(package)),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
    proofs={}
    def bind(value):
        path=Path(value);proofs[str(path)]=file_sha256(path)if path.is_file()else None
    for key in ('native_receipts','column_order_receipts','region_receipts','independent_receipts'):
        for value in definition.get(key,{}).values():
            for path in value if isinstance(value,list)else [value]:bind(path)
    if definition.get('review_sidecar'):bind(definition['review_sidecar'])
    return digest(dict(implementation=implementation,source=file_sha256(source)if source.is_file()else None,
        package_manifest=file_sha256(package/'evidence-package.json')if package and(package/'evidence-package.json').is_file()else None,
        package_stamps=stamps,proofs=proofs,feedback=references,definition=definition,ocr=ocr,max_rounds=max_rounds))


def run(output,max_documents=10,max_rounds=2,ocr='off'):
    if not(Path(output)/'controller.sqlite').is_file():raise ValueError('Controller is not initialized')
    if not 1<=max_documents<=1000 or not 1<=max_rounds<=5 or ocr not in ('off','auto','vision','tesseract','rapidocr'):
        raise ValueError('Invalid control-loop work budget or OCR engine')
    with ledger(output)as(db,base):
        refs=[json.loads(r[0])for r in db.execute('SELECT payload FROM feedback ORDER BY key')]
        implementation=digest({p.name:file_sha256(p)for p in sorted(Path(__file__).parent.iterdir())if p.suffix in ('.py','.json')})
        documents=db.execute("SELECT sha,source,package,definition,state,last_signature FROM documents WHERE state!='source_binding_required' ORDER BY CASE WHEN state IN('feedback_pending','evidence_pending') THEN 0 WHEN state='queued' THEN 1 ELSE 2 END,sha").fetchall()
        completed=[];unchanged=0
        for document in documents:
            if len(completed)>=max_documents:break
            source=Path(document['source']);package=Path(document['package'])if document['package']else None
            if package and not(package/'evidence-package.json').is_file()and package.is_relative_to(base/'attempts'):
                package=None  # Preserve partial logs and retry extraction into a fresh attempt.
            references=[r for r in refs if r['source_pdf_sha256']==document['sha']]
            signature=_signature(source,package,json.loads(document['definition']),references,implementation,ocr,max_rounds)
            if document['last_signature']==signature:
                unchanged+=1;continue
            number=db.execute('SELECT count(*)FROM attempts').fetchone()[0]+1
            folder=base/'attempts'/f'{number:07d}-{document["sha"][:16]}-{uuid4().hex[:8]}';folder.mkdir(parents=True,exist_ok=False)
            db.execute("UPDATE documents SET state='processing',updated=?WHERE sha=?",(now(),document['sha']));db.commit()
            outcome=dict(source_pdf_sha256=document['sha'],source=str(source),rounds=[],state='needs_source_review',publication_eligible=False)
            try:
                if file_sha256(source)!=document['sha']:raise ValueError('Original PDF bytes changed')
                if package is None:
                    package=folder/'extraction'
                    command(['pdf-extract','--input',source,'--output',package,'--ocr',ocr,'--tables','--json'],folder,'parse')
                report,measurement=_assessment(package,source,folder,references)
                for round_no in range(max_rounds):
                    snapshot=dict(round=round_no,package=str(package),measurement=measurement,action='measure')
                    outcome['rounds'].append(snapshot)
                    if len(report['results'])==0 or report.get('package_outcome')=='source_blocked':
                        outcome['state']='source_blocked';snapshot['action']='repair_source_identity';break
                    if all(p['outcome']=='auto_accepted'for p in report['results'])and measurement['loss_vector']==[0,0,0]:
                        outcome['state']='extraction_checks_passed';snapshot['action']='retain_checked_extraction';break
                    if measurement['loss_vector'][1]:
                        snapshot['action']='resolve_source_review_feedback';outcome['state']='needs_source_review';break
                    if round_no==max_rounds-1:
                        snapshot['action']='continue_with_new_evidence_or_budget';outcome['state']='repair_budget_exhausted';break
                    work=folder/f'round-{round_no+1}';work.mkdir()
                    flagged=[p['page']for p in report['results']if p['outcome']=='repair_required']
                    if not flagged:break
                    manifest=work/'repairs.jsonl';definition=json.loads(document['definition'])
                    row={**definition,'source_pdf':str(source),'package':str(package),'quality_report':str(folder/'quality.json')if round_no==0 else str(previous_work/'quality.json'),'pages':flagged}
                    manifest.write_text(canonical(row)+'\n',encoding='utf-8')
                    repair=command(['pdf-repair-queue','--input',manifest,'--output',work/'repair','--workers','1','--limit','1','--json'],work,'repair')
                    snapshot['repair_result']=repair;snapshot['action']='staged_targeted_repair_and_full_document_recheck'
                    candidates=[r for r in repair['results']if r.get('status')=='ready_for_publication'and r.get('candidate_package')]
                    if not candidates:
                        snapshot['action']='needs_source_bound_layout_or_alternate_reader_evidence';break
                    candidate=Path(candidates[0]['candidate_package'])
                    candidate_report,candidate_measurement=_assessment(candidate,source,work,references)
                    snapshot['candidate_measurement']=candidate_measurement
                    if not non_regressing(measurement,candidate_measurement):
                        snapshot['action']='reject_regressing_or_unreviewed_candidate';break
                    package,report,measurement=candidate,candidate_report,candidate_measurement
                    previous_work=work
                outcome.update(package=str(package),measurement=measurement,
                    source_review_complete=measurement['source_review_unlabelled_pages']==0 and measurement['source_reviewed_pages']>0 and measurement['loss_vector'][1]==0)
                if file_sha256(source)!=document['sha']:raise ValueError('Original changed during controller evaluation')
            except Exception as error:
                outcome.update(state='source_blocked'if isinstance(error,(ValueError,FileNotFoundError))else 'processing_failed',error=str(error),source_review_complete=False)
            write(folder/'result.json',outcome)
            # Fingerprint the retained candidate, not the pre-repair package.
            if package and(package/'evidence-package.json').is_file():
                signature=_signature(source,package,json.loads(document['definition']),references,implementation,ocr,max_rounds)
            result_ref=dict(result_path=str(folder/'result.json'),result_sha256=file_sha256(folder/'result.json'),state=outcome['state'])
            db.execute('INSERT INTO attempts(sha,signature,result,created)VALUES(?,?,?,?)',(document['sha'],signature,canonical(result_ref),now()))
            db.execute('UPDATE documents SET package=?,state=?,last_signature=?,last_result=?,updated=?WHERE sha=?',
                (str(package)if package else None,outcome['state'],signature,canonical(result_ref),now(),document['sha']))
            _save_metrics(db,document['sha'],outcome,result_ref['result_path'],result_ref['result_sha256'])
            db.commit();completed.append(document['sha'])
            with (base/'events.jsonl').open('a',encoding='utf-8',newline='\n')as stream:
                stream.write(canonical(dict(event='control_document_completed',source_pdf_sha256=document['sha'],state=outcome['state'],at=now()))+'\n')
            write(base/'status.json',_status(db,base))
        summary=_status(db,base)
        write(base/'status.json',summary)
        return {**summary,'documents_processed_this_invocation':len(completed),'unchanged_documents_skipped':unchanged}


def _save_metrics(db,sha,outcome,path,artifact_sha):
    m=outcome.get('measurement',{})if outcome.get('state')not in ('source_blocked','processing_failed')else {}
    pages=m.get('pages',[])
    values=(sha,canonical(dict(loss_vector=m.get('loss_vector'),state=outcome.get('state'))),int(bool(m)),
        m.get('source_reviewed_pages',0),m.get('source_review_unlabelled_pages',0),m.get('source_reviewed_cells',0),
        len(pages),sum(p['machine_outcome']=='auto_accepted'for p in pages),sum(p['machine_outcome']!='auto_accepted'for p in pages),
        int(outcome.get('source_review_complete')is True),path,artifact_sha)
    db.execute('INSERT OR REPLACE INTO document_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',values)


def _status(db,base):
    states=dict(db.execute('SELECT state,count(*)FROM documents GROUP BY state').fetchall())
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name='document_metrics'").fetchone():
        return dict(schema_version=SCHEMA,states=states,documents=sum(states.values()),status='workspace_upgrade_required',
                    message='Run pdf-control run to migrate cached progress counters; no source files are changed',publication_eligible=False)
    metrics=db.execute('''SELECT COALESCE(sum(measured),0),COALESCE(sum(reviewed_pages),0),
        COALESCE(sum(unlabelled_pages),0),COALESCE(sum(reviewed_cells),0),COALESCE(sum(machine_pages),0),
        COALESCE(sum(passed_pages),0),COALESCE(sum(unresolved_pages),0),COALESCE(sum(review_complete),0)FROM document_metrics''').fetchone()
    return dict(schema_version=SCHEMA,output=str(base),documents=sum(states.values()),states=states,
        source_files=db.execute('SELECT count(*)FROM source_aliases').fetchone()[0],
        attempts=db.execute('SELECT count(*)FROM attempts').fetchone()[0],
        source_reviewed_pages=metrics[1],source_review_unlabelled_pages=metrics[2],source_reviewed_cells=metrics[3],
        source_review_complete_documents=metrics[7],quality_measured_documents=metrics[0],
        unmeasured_documents=sum(states.values())-metrics[0],
        review_page_counter_scope='latest_measurements_only_unmeasured_documents_have_unknown_page_review_coverage',
        machine_checked_pages=metrics[4],machine_passed_pages=metrics[5],unresolved_machine_pages=metrics[6],
        publication_eligible=False,legal_review_approved=False)


def status(output):
    base=Path(output).resolve();path=base/'controller.sqlite'
    if not path.is_file():raise ValueError('Controller is not initialized')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)as db:
        db.row_factory=sqlite3.Row
        return _status(db,base)


def tasks(output,limit=25,offset=0,include_unlabelled=False):
    """Read source-linked work, one hash-verified document snapshot at a time."""
    if not 1<=limit<=200 or type(offset)is not int or offset<0:raise ValueError('Invalid task page budget')
    base=Path(output).resolve();path=base/'controller.sqlite'
    if not path.is_file():raise ValueError('Controller is not initialized')
    result=[];seen=0
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)as db:
        db.row_factory=sqlite3.Row
        for row in db.execute('''SELECT d.sha,d.source,d.package,d.state,m.result_path,m.result_sha
            FROM documents d JOIN document_metrics m ON m.sha=d.sha
            WHERE m.measured=1 ORDER BY CASE WHEN d.state IN('feedback_pending','evidence_pending')THEN 0 ELSE 1 END,d.sha'''):
            if not row['result_path']:continue  # Pre-controller snapshots need one fresh measurement.
            artifact=Path(row['result_path'])
            if not artifact.resolve().is_relative_to(base)or file_sha256(artifact)!=row['result_sha']:
                raise ValueError('Controller result artifact changed')
            value=json.loads(artifact.read_text(encoding='utf-8'))
            for page in value.get('measurement',{}).get('pages',[]):
                cell_errors=[r for r in page.get('cell_reference_residuals',[])if r['error']!=0]
                if (page['machine_outcome']=='auto_accepted'and page['source_review_error']!=1 and not cell_errors
                        and not(include_unlabelled and page['source_review_error']is None)):continue
                if seen<offset:seen+=1;continue
                result.append(dict(source_pdf_sha256=row['sha'],source_pdf=row['source'],page=page['page'],
                    page_text_sha256=page['page_text_sha256'],package=row['package'],state=row['state'],
                    measurement_artifact=str(artifact),measurement_sha256=row['result_sha'],
                    gate_residuals=page['gate_residuals'],cell_differences=cell_errors,
                    source_review_error=page['source_review_error'],next_actions=page['suggested_actions'],
                    scope='last_measured_version_feedback_intake_rechecks_source_and_text',publication_eligible=False))
                if len(result)==limit:break
            if len(result)==limit:break
    return dict(schema_version='pdf2data.control-tasks.v1',tasks=result,returned=len(result),offset=offset,
                next_offset=offset+len(result),publication_eligible=False)
