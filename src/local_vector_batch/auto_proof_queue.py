"""Resumable, current-hash source-crop repair; publication is adapter-owned."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
import json, re, shutil, sqlite3, time
from difflib import SequenceMatcher


def changed_source_lines(lines, independent, touched, *, max_lines=4):
    """Locate differing source lines across harmless OCR line wrapping.

    This proposes crops only: source readers and the whole-page gate decide
    whether any correction can be committed.
    """
    from .quality_gate import WORD, _normalized
    tokens, owners = [], []
    for index, line in enumerate(lines):
        words = WORD.findall(_normalized(line['text']))
        tokens.extend(words); owners.extend([index] * len(words))
    other = WORD.findall(_normalized(independent))
    selected = set()
    for operation, a, b, c, d in SequenceMatcher(None, tokens, other, autojunk=False).get_opcodes():
        if operation == 'equal': continue
        if a == b:
            if a == 0 or a == len(owners):
                raise ValueError('insertion is not anchored to a source line')
            if owners[a - 1] != owners[a]:
                # A short OCR token at a wrap boundary may belong to either
                # printed line. Propose both crops; the complete-line readers
                # and whole-page gate must resolve it from the source image.
                if not 1 <= d - c <= 2:
                    raise ValueError('boundary insertion exceeds source proof budget')
                selected.update((owners[a - 1], owners[a]))
            else:
                selected.add(owners[a])
        else:
            selected.update(owners[a:b])
    if len(selected) > max_lines or any(i in touched or i <= 0 for i in selected):
        raise ValueError('source line set is ambiguous, overlaps a marker, or exceeds the proof budget')
    return sorted(selected)


def _attempt(job, root, independent_path):
    from .region_ocr import (candidate_footnote_pairs, propose_footnote_marker_link,
                             corrected_footnote_text, source_bound_complete_line)
    from .repair_pipeline import _bound_independent_text
    from .footnote_repair import apply_footnote_marker_repair
    from .repair_commit import commit_page_repairs
    package, source, page_no = Path(job['package']), Path(job['source_pdf']), job['page']
    key = job['key']; folder = Path(root) / 'candidates' / key
    folder.mkdir(parents=True, exist_ok=True)
    current = next(x for x in _rows(package / 'pages.jsonl') if x['page'] == page_no)
    if current['text_sha256'] != job['text_sha256'] or current['status'] != 'review_required':
        return {'key': key, 'status': 'stale', 'page': page_no}
    independent = job['independent']
    sidecar = Path(job['review_sidecar']) if job.get('review_sidecar') else None
    if sidecar and sidecar.exists() and any(r.get('page') == page_no and r.get('source_pdf_sha256') == job['source_sha'] for r in _rows(sidecar)):
        return {'key': key, 'status': 'stale', 'reason': 'source-reviewed decision exists', 'page': page_no}
    independent_text = _bound_independent_text(independent, source, source_sha=job['source_sha'],
                        page=page_no, original_text_sha=job['text_sha256'],
                        candidate_engine=current['ocr']['engine'])
    pairs = candidate_footnote_pairs(current, independent_text)[:4]
    proof_dir = folder / 'proof'; proof_dir.mkdir(parents=True, exist_ok=True); receipts = []
    try:
        for body, note in pairs:
            proposal = propose_footnote_marker_link(package, source, page_no, independent, proof_dir, body, note)
            if proposal['status'] == 'candidate': receipts.append(proposal['receipt'])
        parsed_receipts = [json.loads(Path(p).read_text()) for p in receipts]
        if parsed_receipts:
            corrected, independent_corrected = corrected_footnote_text(current, independent_text, parsed_receipts)
        else:
            corrected, independent_corrected = current['ocr']['lines'], independent_text
        touched = {i for receipt in parsed_receipts for i in (receipt['body_line_index'], receipt['note_line_index'])}
        line_proofs=[]; line_paths=[]
        for a in changed_source_lines(corrected, independent_corrected, touched):
            proof = source_bound_complete_line(current, source, a, independent_text)
            path = proof_dir / f'p{page_no}-line-{a}.json'
            path.write_text(json.dumps(proof, ensure_ascii=False, indent=2))
            line_proofs.append(proof); line_paths.append(str(path))
        if not parsed_receipts and not line_proofs:
            return {'key': key, 'status': 'awaiting_evidence', 'reason': 'no independently proved correction', 'page': page_no}
        stage = folder / 'package'
        if stage.exists(): shutil.rmtree(stage)
        shutil.copytree(package, stage)
        outcome = apply_footnote_marker_repair(stage, parsed_receipts, folder / 'gate', line_proofs=line_proofs,
                                               independent_receipt=independent, source_pdf=source)
        candidate = {'page': page_no, 'source_pdf_sha256': job['source_sha'],
                     'original_text_sha256': job['text_sha256'], 'candidate_text_sha256': outcome['new_text_sha256'],
                     'status': 'ready_for_publication', 'candidate_package': str(stage),
                     'quality_receipt': str(folder / 'gate' / f'p{page_no}-composite-quality.json'),
                     'footnote_receipts': receipts, 'line_word_proofs': line_paths}
        (folder / 'commit-job.json').write_text(json.dumps(candidate, indent=2))
        committed = commit_page_repairs(package, source, [candidate], quality_output=folder / 'committed-quality.json',
                                        independent_cache=Path(independent_path),
                                        review_sidecar=sidecar)
        return {'key': key, 'status': 'committed_local', 'page': page_no, 'commit': committed,
                'candidate_package': str(stage), 'package': str(package), 'source_pdf': str(source)}
    except Exception as exc:
        return {'key': key, 'status': 'repair_required', 'page': page_no, 'reason': str(exc)[:500]}


def _rows(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def provisional_disposition(page, receipt):
    """Choose a display-only candidate, never evidence, when numerals agree."""
    selected = page.get('text', '')
    alternate = (receipt.get('independent') or {}).get('text', '')
    number = re.compile(r'\d+(?:[.,]\d+)*')
    if not selected.strip() or not alternate.strip():
        return {'candidate': None, 'reason': 'missing_text', 'verified': False, 'searchable': False}
    if number.findall(selected) != number.findall(alternate):
        return {'candidate': None, 'reason': 'numeric_disagreement', 'verified': False, 'searchable': False}
    operators = re.compile(r'[%≤≥<>±=]|\b(?:not less than|not more than|before|after|within)\b', re.I)
    if [x.casefold() for x in operators.findall(selected)] != [x.casefold() for x in operators.findall(alternate)]:
        return {'candidate': None, 'reason': 'operator_disagreement', 'verified': False, 'searchable': False}
    languages = (receipt.get('independent') or {}).get('recognition_languages', [])
    hindi = any('\u0900' <= c <= '\u097f' for c in selected)
    reason = 'selected_supports_devanagari' if hindi and not any(x in languages for x in ('hi', 'hin', 'devanagari')) else 'preserve_current_candidate'
    return {'candidate': 'selected', 'text_sha256': page['text_sha256'], 'reason': reason,
            'verified': False, 'searchable': False}


def run_auto_proof(manifest, independent, output, *, limit=10, workers=1, retry_unresolved=False):
    if not 1 <= workers <= 2 or not 1 <= limit <= 100:
        raise ValueError('workers must be 1..2; limit must be 1..100')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    receipts = {(r.get('source_pdf_sha256'), r.get('page'), r.get('selected_page_text_sha256')): r for r in _rows(independent)}
    db = sqlite3.connect(output / 'auto-proof.sqlite')
    db.execute('create table if not exists jobs (key text primary key, status text, result text, updated real)')
    jobs=[]; seen=set()
    for row in _rows(manifest):
        package=Path(row['package']); report=json.loads((package/'report.json').read_text()); source_sha=report['source_pdf_sha256']
        pages={p['page']:p for p in _rows(package/'pages.jsonl')}
        for page_no in row['pages']:
            page=pages.get(page_no)
            if not page or page.get('status')!='review_required': continue
            receipt=receipts.get((source_sha,page_no,page['text_sha256']))
            if not receipt: continue
            key=sha256(f"v1:{source_sha}:{page_no}:{page['text_sha256']}".encode()).hexdigest()
            prior = db.execute('select status from jobs where key=?',(key,)).fetchone()
            if key in seen or (prior and not (retry_unresolved and prior[0] in {'awaiting_evidence', 'repair_required'})): continue
            sidecar = row.get('review_sidecar')
            if not sidecar and package.parent.name == 'extracted' and package.parent.parent.name == 'pdf-backfill':
                sidecar = str(package.parent.parent / 'reviews' / (source_sha + '.jsonl'))
            seen.add(key); jobs.append({'key':key,'source_sha':source_sha,'page':page_no,'text_sha256':page['text_sha256'],
                                       'package':str(package),'source_pdf':row['source_pdf'],'independent':receipt,
                                       'review_sidecar':sidecar})
            if len(jobs)>=limit: break
        if len(jobs)>=limit: break
    counts={}; started=time.monotonic()
    (output/'progress.json').write_text(json.dumps({'planned':len(jobs),'completed':0,'counts':{},'elapsed_seconds':0,'status':'running'}))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(_attempt,j,str(output),str(independent)): j for j in jobs}
        for f in as_completed(futures):
            job = futures[f]
            try:
                result=f.result()
            except Exception as exc:
                result={'key':job['key'], 'page':job['page'], 'status':'repair_required',
                        'reason':f'worker_failed: {type(exc).__name__}: {exc}'[:500]}
            status=result['status']; counts[status]=counts.get(status,0)+1
            result.update({'source_pdf_sha256': job['source_sha'], 'original_text_sha256': job['text_sha256'],
                           'package': job['package'], 'source_pdf': job['source_pdf']})
            if status in {'repair_required', 'awaiting_evidence'}:
                try:
                    page = next(p for p in _rows(Path(job['package']) / 'pages.jsonl') if p['page'] == job['page'])
                    if page.get('text_sha256') == job['text_sha256']:
                        result['provisional'] = provisional_disposition(page, job['independent'])
                except (OSError, ValueError, StopIteration):
                    result['provisional'] = {'candidate': None, 'reason': 'current_page_unavailable', 'verified': False, 'searchable': False}
            db.execute('insert or replace into jobs values (?,?,?,?)',(result['key'],status,json.dumps(result),time.time()));db.commit()
            (output/'progress.json').write_text(json.dumps({'planned':len(jobs),'completed':sum(counts.values()),'counts':counts,'elapsed_seconds':round(time.monotonic()-started,2),'status':'running'}))
    db.close()
    (output/'progress.json').write_text(json.dumps({'planned':len(jobs),'completed':sum(counts.values()),'counts':counts,'elapsed_seconds':round(time.monotonic()-started,2),'status':'complete'}))
    return {'planned':len(jobs),'completed':sum(counts.values()),'counts':counts,'elapsed_seconds':round(time.monotonic()-started,2),
            'publication':'local guarded commit only; Cloudflare adapter required'}
