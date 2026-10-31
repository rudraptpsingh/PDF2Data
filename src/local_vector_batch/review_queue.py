"""Local, non-promoting OCR disagreement review queue."""
from __future__ import annotations
from .jsonl import jsonl_physical_lines
import hashlib
import io
import json
import os
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote
from .repair_pipeline import _bound_independent_text, _manifest_path
from .review_disagreements import discrepancies, feedback_entries


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def proposal_for(row: dict, focus: dict) -> dict:
    proposal = row.get('proposal')
    if proposal is None:
        reason = ('Both OCR readings contain the same non-space equation symbols. Check the original for spacing.'
                  if focus.get('kind') == 'math_spacing' else
                  'No source-backed choice is available. Compare both readings with the original.')
        return {'choice': None, 'basis': 'needs_source_review', 'reason': reason}
    if (not isinstance(proposal, dict) or proposal.get('choice') not in ('selected', 'independent', 'neither') or
            proposal.get('basis') != 'source_observation' or not isinstance(proposal.get('reason'), str) or
            not 1 <= len(proposal['reason']) <= 400 or not isinstance(proposal.get('crop_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', proposal['crop_sha256']) or not focus.get('boxes')):
        raise ValueError('invalid source-backed proposal')
    return {key: proposal[key] for key in ('choice', 'basis', 'reason', 'crop_sha256')}


def proposal_hash(proposal: dict) -> str:
    return _sha(json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode())


def normalize_hindi_clause_wraps(text: str) -> str:
    """Join a standalone clause number to a long Hindi body line for display.

    Only whitespace changes. Short section headings and English headings stay
    on separate lines; original text and its hash are never changed.
    """
    pattern = re.compile(r'(?m)^([ \t]*\(\s*\d{1,3}\s*\))[ \t]*\n([ \t]*([^\n]+))')
    def replace(match: re.Match) -> str:
        line = match.group(3).strip()
        if len(line) < 60 or not re.search(r'[\u0900-\u097f]', line) or line.endswith((':', '：')):
            return match.group()
        return match.group(1) + ' ' + line
    return pattern.sub(replace, text)


def compare_lines(selected: str, independent: str) -> tuple[list[dict], list[dict]]:
    """Highlight token differences; fall back to lines for unusually long pages."""
    left = re.findall(r'\s+|\w+|[^\w\s]', selected)
    right = re.findall(r'\s+|\w+|[^\w\s]', independent)
    if len(left) + len(right) > 6000:
        left, right = selected.splitlines(keepends=True), independent.splitlines(keepends=True)
    a, b = [], []
    for operation, i, j, k, l in SequenceMatcher(None, left, right).get_opcodes():
        if i != j:
            a.append({'text': ''.join(left[i:j]), 'changed': operation != 'equal'})
        if k != l:
            b.append({'text': ''.join(right[k:l]), 'changed': operation != 'equal'})
    return a, b


def focus_segments(text: str, excerpt: str, span: list[int] | None) -> list[dict]:
    """Highlight the selected difference without changing the OCR text."""
    if not excerpt:
        return []
    if span is None:
        return [{'text': excerpt, 'changed': False}]
    starts = [match.start() for match in re.finditer(re.escape(excerpt), text)]
    candidates = [start for start in starts if start <= span[0] <= start + len(excerpt)]
    if len(candidates) != 1:
        return [{'text': excerpt, 'changed': False}]
    base = candidates[0]
    start = min(len(excerpt), max(0, span[0] - base))
    end = min(len(excerpt), max(start, span[1] - base))
    return ([{'text': excerpt[:start], 'changed': False}] +
            ([{'text': excerpt[start:end], 'changed': True}] if end > start else
             [{'text': '∅', 'changed': True, 'missing': True}]) +
            [{'text': excerpt[end:], 'changed': False}])


def empty_focused_queue(manifest: Path, index: int) -> dict | None:
    entries=[line for line in Path(manifest).read_text(encoding='utf-8').splitlines()if line.strip()]
    if entries:return None
    if type(index)is not int or index<0:raise ValueError('review item outside queue')
    return {'empty':True,'total':0,'scope':'focused_queue',
            'message':'No unresolved questions in this focused queue',
            'page_quality_approval':False,'legal_approval':False}


def validate_review_target(target: dict | None, tables: list[dict], source_sha: str, page: int) -> dict | None:
    """Bind an automatically queued question to the freshly read source cell."""
    if target is None:return None
    if not isinstance(target,dict)or target.get('source_pdf_sha256')!=source_sha or target.get('page')!=page:
        raise ValueError('queued cell source changed')
    matches=[(index,table,cell)for index,table in enumerate(tables)
             if table.get('table_version_id')==target.get('table_version_id')
             for cell in table.get('cell_spans',[])if cell.get('id')==target.get('cell_id')]
    if len(matches)!=1:raise ValueError('queued source cell is missing or ambiguous')
    index,table,cell=matches[0]
    if any(target.get(key)!=cell.get(key)for key in ('row','column','bbox','selected_cell_sha256','independent_cell_sha256')):
        raise ValueError('queued source cell geometry or readings changed')
    if target.get('table_geometry_sha256')!=table.get('table_geometry_sha256'):
        raise ValueError('queued source table geometry changed')
    return {'table_index':index,'cell_id':cell['id'],'row':cell['row'],'column':cell['column']}


def load_item(manifest: Path, index: int) -> dict:
    rows = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    if not 0 <= index < len(rows):
        raise ValueError('review item outside queue')
    row = rows[index]
    source = Path(row['source_pdf']).resolve()
    source_bytes = source.read_bytes()
    if _sha(source_bytes) != row['source_pdf_sha256']:
        raise ValueError('source PDF changed since queue was created')
    selected = Path(row['selected_text']).read_bytes().decode('utf-8')
    independent = json.loads(Path(row['independent_json']).read_text(encoding='utf-8'))
    alt = independent['text']
    if _sha(selected.encode()) != row['selected_text_sha256'] or _sha(alt.encode()) != row['independent_text_sha256']:
        raise ValueError('OCR text changed since queue was created')
    page = row['page']
    if type(page) is not int or page < 1:
        raise ValueError('invalid physical page')
    if not isinstance(row['prompt'], str) or len(row['prompt']) > 1000:
        raise ValueError('invalid review question')
    focus = row.get('focus') or {}
    if not isinstance(focus, dict):
        raise ValueError('invalid source focus')
    if focus.get('kind') not in (None, 'date', 'footnote_candidate', 'number', 'legal_operator', 'name_or_identifier_candidate', 'enumerator_candidate', 'math_spacing', 'math_operator', 'currency_unit_candidate', 'text_change', 'url_text', 'formatting'):
        raise ValueError('invalid disagreement type')
    boxes = focus.get('boxes', [])
    if not isinstance(boxes, list) or len(boxes) > 3:
        raise ValueError('invalid source focus boxes')
    for box in boxes:
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(x) not in (int, float) or not 0 <= x <= 1 for x in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError('invalid source focus box')
    for key, text in (('selected_excerpt', selected), ('independent_excerpt', alt)):
        excerpt = focus.get(key, '')
        if not isinstance(excerpt, str) or len(excerpt) > 600 or (excerpt and excerpt not in text):
            raise ValueError('focus excerpt is not in the OCR text')
    for key, text in (('selected_span', selected), ('independent_span', alt)):
        span = focus.get(key)
        if span is not None and (not isinstance(span, list) or len(span) != 2 or
                any(type(value) is not int for value in span) or not 0 <= span[0] <= span[1] <= len(text)):
            raise ValueError('invalid disagreement span')
    raw_left, raw_right = compare_lines(selected, alt)
    left, right = compare_lines(normalize_hindi_clause_wraps(selected), normalize_hindi_clause_wraps(alt))
    if focus.get('selected_span') is not None or focus.get('independent_span') is not None:
        focus_left = focus_segments(selected, focus.get('selected_excerpt', ''), focus.get('selected_span'))
        focus_right = focus_segments(alt, focus.get('independent_excerpt', ''), focus.get('independent_span'))
    else:
        focus_left, focus_right = compare_lines(focus.get('selected_excerpt', ''), focus.get('independent_excerpt', ''))
    source_url = row.get('source_url', '')
    if not isinstance(source_url, str) or (source_url and urlsplit(source_url).scheme not in ('http', 'https')):
        raise ValueError('invalid source URL')
    selected_meta = row.get('selected_meta') or {}
    if not isinstance(selected_meta, dict):
        raise ValueError('invalid selected OCR metadata')
    proposal = proposal_for(row, focus)
    table_previews = []
    if row.get('l1_package'):
        from .l1_review import preview_tables
        table_previews = preview_tables(source, page, row['source_pdf_sha256'])
    target=validate_review_target(row.get('target_cell'),table_previews,row['source_pdf_sha256'],page)
    return {'table_previews': table_previews, 'review_target':target, 'index': index, 'total': len(rows), 'title': row['title'], 'prompt': row['prompt'], 'source_url': source_url,
            'page_total_discrepancies': row.get('page_total_discrepancies'),
            'page_queued_discrepancies': row.get('page_queued_discrepancies'),
            'prompt_sha256': _sha(row['prompt'].encode()), 'selected_segments': left, 'independent_segments': right,
            'raw_selected_segments': raw_left, 'raw_independent_segments': raw_right,
            'focus_selected_segments': focus_left, 'focus_independent_segments': focus_right,
            'focus': focus, 'focus_sha256': _sha(json.dumps(focus, sort_keys=True, ensure_ascii=False).encode()),
            'proposal': proposal, 'proposal_sha256': proposal_hash(proposal),
            'source_pdf': str(source), 'source_pdf_sha256': row['source_pdf_sha256'], 'page': page,
            'selected_text': selected, 'selected_text_sha256': row['selected_text_sha256'],
            'independent_text': alt, 'independent_text_sha256': row['independent_text_sha256'],
            'hindi_detected': bool(re.search(r'[\u0900-\u097f]', selected)),
            'selected_meta': selected_meta,
            'independent_meta': {key: independent.get(key) for key in ('engine', 'recognition_model', 'recognition_languages')},
            'independent_engine': independent.get('engine', 'independent OCR')}


def render_page(item: dict, *, focus: bool = False) -> bytes:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(item['source_pdf'])
    try:
        if item['page'] > len(pdf):
            raise ValueError('physical page outside PDF')
        page = pdf[item['page'] - 1]
        try:
            image = page.render(scale=2 if focus else 1.5).to_pil().convert('RGB')
            boxes = item.get('focus', {}).get('boxes', []) if focus else []
            if boxes:
                from PIL import Image
                crops = [image.crop((round(x1 * image.width), round(y1 * image.height),
                                     round(x2 * image.width), round(y2 * image.height)))
                         for x1, y1, x2, y2 in boxes]
                combined = Image.new('RGB', (max(crop.width for crop in crops),
                                             sum(crop.height for crop in crops) + 16 * (len(crops) - 1)), 'white')
                top = 0
                for crop in crops:
                    combined.paste(crop, (0, top)); top += crop.height + 16
                from PIL import ImageOps
                image = ImageOps.expand(combined,border=12,fill='white')
            out = io.BytesIO()
            image.save(out, format='PNG')
            return out.getvalue()
        finally:
            page.close()
    finally:
        pdf.close()


def verify_proposal_source(item: dict) -> None:
    """The optional visual suggestion is tied to this exact rendered PDF crop."""
    proposal = item['proposal']
    if proposal['choice'] is not None and _sha(render_page(item, focus=True)) != proposal['crop_sha256']:
        raise ValueError('proposal source crop changed; review the source again')


def record_decision(manifest: Path, output: Path, request: dict) -> dict:
    if not isinstance(request, dict) or type(request.get('index')) is not int:
        raise ValueError('invalid review request')
    item = load_item(manifest, request['index'])
    keys = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256')
    if any(request.get(key) != item[key] for key in keys):
        raise ValueError('review changed; reload before saving')
    decision = request.get('decision')
    if decision not in {'selected', 'independent', 'neither', 'unclear'}:
        raise ValueError('choose a review decision')
    note = request.get('note', '')
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError('invalid note')
    proposal = item['proposal']
    if proposal['choice'] is not None and decision != proposal['choice'] and not note.strip():
        raise ValueError('add a reason when choosing a different reading')
    verify_proposal_source(item)
    # Feedback only: this is not a page approval, extraction override or index update.
    receipt = {'schema_version': 1, 'kind': 'ocr_disagreement_feedback',
               **{key: item[key] for key in keys}, 'decision': decision,
               'disagreement_kind': item['focus'].get('kind', 'unclassified'),
               'proposal_choice': proposal['choice'], 'proposal_basis': proposal['basis'],
               'note': note.strip(), 'created_at': datetime.now(timezone.utc).isoformat(),
               'promotes_page': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Multiple localhost UI processes may share a feedback file. File locking
    # keeps their JSONL writes intact without changing saved decisions.
    from filelock import FileLock
    with FileLock(str(output) + '.lock', timeout=10):
        with output.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def latest_feedback(output: Path, item: dict) -> dict | None:
    """Recover the latest matching feedback after a server restart."""
    keys = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256', 'proposal_sha256')
    if not output.is_file():
        return None
    latest = None
    with output.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if (row.get('kind') == 'ocr_disagreement_feedback' and
                    all(row.get(key) == item[key] for key in keys)):
                latest = {key: row.get(key) for key in ('decision', 'note', 'created_at')}
    return latest


def review_group(manifest: Path, index: int, feedback: Path | None = None) -> list[dict]:
    """List individually reviewable differences for the same PDF and page."""
    rows = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    if not 0 <= index < len(rows):
        raise ValueError('review item outside queue')
    base = rows[index]
    identity = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256')
    result = []
    for position, row in enumerate(rows):
        if any(row.get(key) != base.get(key) for key in identity):
            continue
        focus = row.get('focus') or {}
        if not isinstance(focus, dict) or not isinstance(row.get('prompt'), str):
            raise ValueError('invalid review group item')
        item = {key: row[key] for key in identity}
        item.update(prompt_sha256=_sha(row['prompt'].encode()),
                    focus_sha256=_sha(json.dumps(focus, sort_keys=True, ensure_ascii=False).encode()),
                    proposal_sha256=proposal_hash(proposal_for(row, focus)))
        saved = latest_feedback(feedback, item) if feedback else None
        result.append({'index': position, 'kind': focus.get('kind') or 'difference',
                       'prompt': row['prompt'], 'reviewed': bool(saved), 'current': position == index})
    return result


def attach_observations(manifest: Path, observations: Path, output: Path) -> dict:
    """Attach unverified visual suggestions only to the exact reviewed crop."""
    manifest, observations, output = Path(manifest).resolve(strict=True), Path(observations).resolve(strict=True), Path(output).resolve()
    if output.exists() or output in (manifest, observations):
        raise ValueError('choose a new proposal manifest')
    rows = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    evidence = [json.loads(line) for line in jsonl_physical_lines(observations.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    keys = ('source_pdf_sha256', 'page', 'selected_text_sha256', 'independent_text_sha256', 'prompt_sha256', 'focus_sha256')
    if not rows or len(rows) != len(evidence):
        raise ValueError('every queued difference needs one visual observation')
    by_key = {}
    for obs in evidence:
        identity = tuple(obs.get(key) for key in keys)
        if identity in by_key or obs.get('scope') != 'focused_difference_only' or obs.get('source_image_reviewed') is not True:
            raise ValueError('duplicate or unreviewed observation')
        by_key[identity] = obs
    prepared = []
    for index, row in enumerate(rows):
        item = load_item(manifest, index)
        identity = tuple(item[key] for key in keys)
        obs = by_key.pop(identity, None)
        if not obs or not item['focus'].get('boxes'):
            raise ValueError('observation is not bound to a focused queue difference')
        if _sha(render_page(item, focus=True)) != obs.get('focus_image_sha256'):
            raise ValueError('visual observation crop does not match the original PDF')
        proposal = {'choice': obs.get('choice'), 'basis': 'source_observation',
                    'reason': obs.get('reason'), 'crop_sha256': obs['focus_image_sha256']}
        proposal_for({'proposal': proposal}, item['focus'])
        prepared.append({**row, 'proposal': proposal})
    if by_key:
        raise ValueError('observation does not belong to the queue')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in prepared))
        stream.flush(); os.fsync(stream.fileno())
    return {'differences': len(prepared), 'suggestions': len(prepared), 'output': str(output),
            'status': 'unverified_suggestions_only'}


def filter_reviewed_focus(manifest: Path, reviewed: list[Path], output: Path) -> dict:
    """Remove only already-reviewed exact source/OCR/focus identities."""
    manifest, output = Path(manifest).resolve(strict=True), Path(output).resolve()
    if output.exists() or output == manifest:
        raise ValueError('choose a new review manifest')
    feedback, invalid = feedback_entries([Path(path) for path in reviewed])
    known = {(key[0], key[1], key[2], key[3], key[5]) for key in feedback if key[5] != 'legacy'}
    rows = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    kept, skipped = [], 0
    for index, row in enumerate(rows):
        item = load_item(manifest, index)
        identity = (item['source_pdf_sha256'], item['page'], item['selected_text_sha256'],
                    item['independent_text_sha256'], item['focus_sha256'])
        if identity in known:
            skipped += 1; continue
        verify_proposal_source(item)
        kept.append(row)
    if kept:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            stream.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in kept))
            stream.flush(); os.fsync(stream.fileno())
    return {'input_differences': len(rows), 'kept': len(kept), 'already_reviewed_focus': skipped,
            'invalid_feedback_entries': invalid, 'output': str(output) if kept else None}


def generate_review_queue(manifest: Path, independent: Path, output: Path, *, limit: int = 100,
                          reviewed: list[Path] | None = None, all_differences: bool = False,
                          per_page: int = 64) -> dict:
    """Build a review queue for held pages from source-bound cached OCR receipts.

    Each queue gets a new directory so prior feedback and evidence cannot be
    overwritten. This prepares inspection only; it does not change packages.
    """
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('review queue limit must be 1..10000')
    if type(per_page) is not int or not 1 <= per_page <= 1000:
        raise ValueError('per-page review limit must be 1..1000')
    manifest, independent, output = Path(manifest).resolve(strict=True), Path(independent).resolve(strict=True), Path(output).resolve()
    if output.exists():
        raise ValueError('review output already exists; choose a new directory')
    inputs = [json.loads(line) for line in jsonl_physical_lines(manifest.read_text(encoding='utf-8')) if line.strip(' \t\r')]
    cache: dict[tuple[str, int], list[dict]] = {}
    with independent.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                cache.setdefault((row.get('source_pdf_sha256'), row.get('page')), []).append(row)
    plans, skipped, seen = [], {}, set()
    prior_feedback = feedback_entries([Path(p) for p in (reviewed or [])])[0]
    reviewed_keys = set(prior_feedback)
    def skip(reason: str):
        skipped[reason] = skipped.get(reason, 0) + 1
    for row in inputs:
        try:
            package = _manifest_path(row['package'], manifest)
            source = _manifest_path(row['source_pdf'], manifest)
        except (KeyError, TypeError):
            skip('invalid_manifest_row'); continue
        if output.is_relative_to(package) or output == source or output == manifest or output == independent:
            raise ValueError('review output must be separate from all inputs')
        try:
            doc = json.loads((package / 'evidence-package.json').read_text(encoding='utf-8'))['document']
            source_sha = doc['pdf_sha256']
            if _sha(source.read_bytes()) != source_sha:
                skip('source_hash_mismatch'); continue
            pages = {page['page']: page for page in (json.loads(line) for line in
                     jsonl_physical_lines((package / 'pages.jsonl').read_text(encoding='utf-8')) if line.strip(' \t\r'))}
        except (OSError, ValueError, KeyError, TypeError):
            skip('source_or_package_unavailable'); continue
        requested = row.get('pages') or list(pages)
        if not isinstance(requested, list) or any(type(p) is not int or p < 1 for p in requested):
            raise ValueError('manifest pages must be positive physical page numbers')
        quality = None
        if row.get('quality_report'):
            try:
                report = json.loads(_manifest_path(row['quality_report'], manifest).read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                skip('quality_report_unavailable'); continue
            if report.get('source_pdf_sha256') != source_sha:
                skip('quality_source_mismatch'); continue
            quality = {item['page']: item for item in report.get('results', [])}
        for number in requested:
            if len(plans) >= min(1000, max(100, limit * 4)):
                break
            page = pages.get(number)
            if not page:
                skip('page_missing'); continue
            key = (source_sha, number)
            if key in seen:
                skip('duplicate_page'); continue
            seen.add(key)
            text = page.get('text', '')
            text_sha = _sha(text.encode())
            if text_sha != page.get('text_sha256'):
                skip('page_hash_mismatch'); continue
            result = quality.get(number) if quality is not None else None
            if quality is not None and (not result or result.get('outcome') != 'repair_required' or result.get('page_text_sha256') != text_sha):
                skip('not_current_held_quality'); continue
            if quality is None and page.get('status') != 'review_required':
                skip('not_held'); continue
            matches = [r for r in cache.get(key, []) if r.get('selected_page_text_sha256') == text_sha]
            if len(matches) != 1:
                skip('missing_or_ambiguous_ocr_receipt'); continue
            receipt = matches[0]
            if not isinstance(receipt.get('independent'), dict):
                skip('unverified_ocr_receipt'); continue
            try:
                selected_engine = (receipt.get('selected') or receipt.get('vision') or {}).get('engine', page.get('method'))
                alt = _bound_independent_text(receipt, source, source_sha=source_sha, page=number,
                                              original_text_sha=text_sha, candidate_engine=selected_engine)
            except (ValueError, OSError, KeyError, IndexError):
                skip('unverified_ocr_receipt'); continue
            alternatives = discrepancies(text, alt, page, max_items=10000 if all_differences else 64,
                                         include_all=all_differences)
            if not alternatives:
                skip('no_focused_disagreement'); continue
            alt_sha = _sha(alt.encode())
            matching_feedback = [value for key, value in prior_feedback.items()
                                 if key[:4] == (source_sha, number, text_sha, alt_sha)]
            notes = ' '.join(str(value.get('note', '')) for value in matching_feedback).lower()
            requested_kinds = set()
            if re.search(r'\broman\b|\bnumeral\b|\bnumberal\b|\(iii\)', notes):
                requested_kinds.add('enumerator_candidate')
            if re.search(r'\bmath\w*\b|\bequation\b', notes) and re.search(r'\bspac\w*\b|\bwhitespace\b', notes):
                requested_kinds.add('math_spacing')
            explicit_kinds = set(requested_kinds)
            # A whole-page choice does not resolve individual differences.
            # Offer up to two different kinds from that same page as follow-ups.
            if matching_feedback and not requested_kinds:
                requested_kinds = set(list(dict.fromkeys(candidate['kind'] for candidate in alternatives))[:2])
            formula_and_units = matching_feedback and {'math_operator', 'currency_unit_candidate'} <= requested_kinds
            alternatives.sort(key=lambda candidate: (candidate['kind'] not in requested_kinds, candidate['priority'], candidate['offset']))
            picked = []
            for candidate in alternatives:
                prompt = {
                    'date': 'Which reading matches the date in the original?',
                    'footnote_candidate': 'Which reading matches this possible footnote marker in the original?',
                    'enumerator_candidate': 'Which reading captures this clause marker and its text in the original?',
                    'math_spacing': 'Which spacing matches the equation in the original? Check every operator as well.',
                    'math_operator': 'Which reading preserves the mathematical operator printed in the original?',
                    'currency_unit_candidate': 'Which reading preserves the currency symbol and unit printed in the original?',
                    'number': 'Which reading matches the number in the original?',
                    'legal_operator': 'Which reading matches this legal condition or operator in the original?',
                    'name_or_identifier_candidate': 'Which reading matches this name or identifier in the original?',
                    'text_change': 'Which reading matches this text in the original?',
                    'url_text': 'Which reading matches this address in the original?',
                    'formatting': 'Which line break matches the original? Check the words separately.',
                }[candidate['kind']]
                focus = candidate['focus']
                identity = (source_sha, number, text_sha, alt_sha, _sha(prompt.encode()),
                            _sha(json.dumps(focus, sort_keys=True, ensure_ascii=False).encode()),
                            proposal_hash(proposal_for({}, focus)))
                if identity in reviewed_keys:
                    skip('already_reviewed_difference'); continue
                same_kind_count = sum(item[0]['kind'] == candidate['kind'] for item in picked)
                if all_differences:
                    picked.append((candidate, prompt))
                elif not picked or (candidate['kind'] in requested_kinds and
                                  (same_kind_count == 0 or (formula_and_units and
                                   candidate['kind'] == 'currency_unit_candidate' and same_kind_count < 2))):
                    picked.append((candidate, prompt))
                have = {item[0]['kind'] for item in picked}
                if all_differences and len(picked) >= per_page:
                    break
                if not all_differences and (not requested_kinds or (have >= requested_kinds and not formula_and_units) or
                        (formula_and_units and have >= requested_kinds and
                         sum(item[0]['kind'] == 'currency_unit_candidate' for item in picked) >= 2)):
                    break
            if not picked:
                continue
            source_url = str(doc.get('source_url') or '')
            title = str(doc.get('title') or '')
            if not title or re.fullmatch(r'[0-9a-f]{32,64}', title, re.I):
                parsed_url = urlsplit(source_url)
                title = f"{parsed_url.hostname or 'Document'} · {unquote(Path(parsed_url.path).name or 'PDF')}"
            for candidate, prompt in picked:
                plans.append({'source_pdf': str(source), 'source_pdf_sha256': source_sha, 'page': number,
                              'title': title, 'source_url': source_url,
                              'prompt': prompt, 'focus': candidate['focus'],
                              'page_total_discrepancies': len(alternatives) if all_differences else None,
                              'priority': -2 if candidate['kind'] in explicit_kinds else
                                          -1 if matching_feedback else candidate['priority'],
                              'selected_meta': {key: (receipt.get('selected') or receipt.get('vision') or {}).get(key)
                                                for key in ('engine', 'recognition_model', 'recognition_languages')},
                              'selected': text, 'independent': receipt['independent'], 'independent_text': alt})
        if len(plans) >= min(1000, max(100, limit * 4)):
            break
    if not plans:
        return {'created': 0, 'skipped': skipped, 'output': None}
    if all_differences:
        group_priority = {}
        for item in plans:
            group = (item['source_pdf_sha256'], item['page'])
            group_priority[group] = min(group_priority.get(group, 99), item['priority'])
        plans.sort(key=lambda item: (group_priority[(item['source_pdf_sha256'], item['page'])],
                                     item['source_pdf_sha256'], item['page'], item['priority'],
                                     item['focus'].get('selected_span', [0])[0]))
    else:
        plans.sort(key=lambda item: (item['priority'], item['source_pdf_sha256'], item['page']))
    considered = len(plans)
    plans = plans[:limit]
    page_counts = {}
    for item in plans:
        group = (item['source_pdf_sha256'], item['page'])
        page_counts[group] = page_counts.get(group, 0) + 1
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for item in plans:
        stem = f"{item['source_pdf_sha256']}-p{item['page']}"
        selected_path, independent_path = output / f'{stem}-selected.txt', output / f'{stem}-independent.json'
        selected_path.write_text(item['selected'], encoding='utf-8')
        independent_path.write_text(json.dumps(item['independent'], ensure_ascii=False), encoding='utf-8')
        records.append({key: item[key] for key in ('source_pdf', 'source_pdf_sha256', 'page', 'title', 'prompt', 'source_url', 'focus', 'selected_meta', 'page_total_discrepancies')} |
                       {'page_queued_discrepancies': page_counts[(item['source_pdf_sha256'], item['page'])]} |
                       {'selected_text': str(selected_path), 'selected_text_sha256': _sha(item['selected'].encode()),
                        'independent_json': str(independent_path), 'independent_text_sha256': _sha(item['independent_text'].encode())})
    path = output / 'review-manifest.jsonl'
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
    return {'created': len(records), 'focused_candidates_considered': considered, 'skipped': skipped, 'output': str(path)}
