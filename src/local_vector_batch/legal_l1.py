"""Immutable offline legal-source extraction (L1), without legal interpretation.

Reuses pdf-extract. Originals, raw representations, regions and exact children
are separate from candidates. No database, model or publication API is called.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse

from .pdf_ingest import extract_pdf
from .evidence_package import verify_package
from .provisions import select_provision, provision_children, validate_wrapper

SCHEMA = 'powerlex.legal-l1.v1'
FAMILIES = frozenset({'regulation', 'draft', 'notice', 'explanatory_memorandum',
    'judgment', 'interim_order', 'record_of_proceedings', 'approval', 'pleading',
    'registry_listing', 'contract', 'tender', 'technical_procedure', 'financial_evidence'})
ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
NUMBER = re.compile(r'(?m)^[ \t]*(\d{1,3})[.)][ \t]+(?=\S)')
PAGE_FOOTER = re.compile(r'^(?:Page\s+\d+\s+(?:of|/)\s+\d+|RoP in Petition\b|Petition No\..*Page)', re.I)
PROFILES = {
    'record_of_proceedings': [('petitioner',r'^Petitioner\s*:'), ('respondents',r'^Respondents?\s*:'),
        ('coram',r'^Coram\s*:'), ('appearances',r'^Parties Present\s*:'), ('proceedings',r'^Record of Proceedings\s*$')],
    'interim_order': [('caption',r'^IN THE MATTER OF\s*:'), ('opposing_parties',r'^VERSUS\s*$'),
        ('appearances',r'^Counsel on record'), ('order',r'^ORDER\s*$')],
    'judgment': [('caption',r'^IN THE MATTER OF\s*:'), ('opposing_parties',r'^VERSUS\s*$'),
        ('appearances',r'^Counsel'), ('judgment',r'^JUDGMENT\s*$')],
    'approval': [('petitioner',r'^Petitioner\s*:'), ('coram',r'^Coram\s*:'), ('order',r'^ORDER\s*$')],
    'draft': [('notification',r'^(?:DRAFT\s+)?NOTIFICATION\s*$'), ('schedule',r'^(?:Annexure|TABLE)\s')],
    'regulation': [('notification',r'^NOTIFICATION\s*$'), ('schedule',r'^(?:Annexure|TABLE)\s')],
    'notice': [('subject',r'^(?:Subject|Sub)\s*:'), ('notice',r'^(?:PUBLIC NOTICE|NOTICE)\s*$')],
    'explanatory_memorandum': [('reasons',r'^EXPLANATORY MEMORANDUM\s*$'), ('schedule',r'^(?:Annexure|TABLE)\s')],
    'pleading': [('prayer',r'^(?:PRAYER|RELIEF SOUGHT)\s*:?$'), ('affidavit',r'^AFFIDAVIT\s*$')],
    'registry_listing': [('listing',r'^(?:CAUSE LIST|LIST OF FINALS)\s*$')],
    'contract': [('parties',r'^BETWEEN\s*$'), ('schedule',r'^(?:SCHEDULE|ANNEXURE)\s')],
    'tender': [('scope',r'^(?:SCOPE OF WORK|NOTICE INVITING TENDER)\s*$')],
    'technical_procedure': [('scope',r'^(?:SCOPE|PROCEDURE)\s*$')],
    'financial_evidence': [('statement',r'^(?:STATEMENT|RECEIPT|INVOICE)\s')],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return digest(text.encode('utf-8'))


# Auxiliary Unicode receipts remain a distinct contract from legal UTF8 text.
from .lossless_text import (auxiliary_text_receipt,validate_auxiliary_text_receipts,
                            verify_invalid_auxiliary_readers,lossless_json_dumps)


def write_json(path: Path, value) -> None:
    path.write_text(lossless_json_dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def write_rows(path: Path, values: list[dict]) -> None:
    path.write_text(''.join(lossless_json_dumps(v, ensure_ascii=False)+'\n' for v in values), encoding='utf-8')


def read_manifest(path: Path) -> dict:
    """Identity is supplied, never guessed from filenames or a model summary."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(manifest,dict) or manifest.get('schema_version') != SCHEMA or not isinstance(manifest.get('sources'), list) or not manifest['sources']:
        raise ValueError('unsupported or empty L1 source manifest')
    seen = set()
    for source in manifest['sources']:
        if not isinstance(source,dict):raise ValueError('every source must be an object')
        identity = source.get('id')
        if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in seen:
            raise ValueError('unsafe or duplicate source identity')
        seen.add(identity)
        if source.get('family') not in FAMILIES:
            raise ValueError('unknown document family; declare a supported profile')
        if not isinstance(source.get('sha256'), str) or not HASH.fullmatch(source['sha256']):
            raise ValueError('every source requires an expected SHA256')
        if not isinstance(source.get('path'), str) or not source['path']:
            raise ValueError('source path required')
        original = Path(source['path'])
        source['path'] = str((path.parent/original).resolve() if not original.is_absolute() else original.resolve())
        url = source.get('url')
        if url is not None:
            if not isinstance(url,str):raise ValueError('source URL must be a string')
            parsed = urlparse(url)
            if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in url):
                raise ValueError('invalid source URL')
        if source.get('title') is not None and (not isinstance(source['title'],str) or not source['title'].strip() or len(source['title'])>240):
            raise ValueError('invalid title')
        expected = source.get('expected_pages')
        if expected is not None and (type(expected) is not int or expected < 1):
            raise ValueError('invalid expected page count')
    groups = manifest.get('families', [])
    if not isinstance(groups, list):
        raise ValueError('families must be a list')
    group_ids=set()
    for group in groups:
        if not isinstance(group,dict):raise ValueError('family group must be an object')
        gid=group.get('id'); required=group.get('required_source_ids')
        if not isinstance(gid,str) or not ID.fullmatch(gid) or gid in group_ids or not isinstance(required,list) or not required or any(not isinstance(x,str) or not ID.fullmatch(x) for x in required) or len(required)!=len(set(required)):
            raise ValueError('invalid family dependency inventory')
        group_ids.add(gid)
    return manifest


def family_inventory(manifest: dict) -> list[dict]:
    supplied = {s['id'] for s in manifest['sources']}
    return [{'id':group['id'], 'required_source_ids':group['required_source_ids'],
             'missing_source_ids':sorted(set(group['required_source_ids'])-supplied),
             'status':'members_supplied' if set(group['required_source_ids'])<=supplied else 'incomplete',
             'legal_currentness':'unresolved'} for group in manifest.get('families',[])]


def capture_layout(original: Path, pages: list[dict]) -> list[dict]:
    """Keep both raw engines and native glyphs. Alignment is an observation only.

    Equal blocks map glyph-stream characters to selected text; unmatched or
    reordered ranges remain explicit. No ASCII normalization or string cap.
    """
    import pdfplumber
    from pypdf import PdfReader
    from .pdf_ingest import _open_pdf_with_page_recovery
    source_pdf_sha=digest(original.read_bytes())
    secondary = PdfReader(original).pages
    result=[]
    with _open_pdf_with_page_recovery(original, secondary, []) as pdf:
        if len(pdf.pages)!=len(pages) or len(secondary)!=len(pages):
            raise ValueError('raw layout page coverage differs from extraction')
        for page, selected, alternate in zip(pdf.pages, pages, secondary):
            errors=[]
            def safe_text(call):
                try:return call() or ''
                except Exception as error:
                    errors.append(type(error).__name__);return None
            native=safe_text(page.extract_text)
            independent=safe_text(alternate.extract_text)
            independent_layout=safe_text(lambda:alternate.extract_text(extraction_mode='layout'))
            from .native_consensus import read_source_lines,choose_witness
            try:source_lines=read_source_lines(original,page,selected['page'],secondary_layout_text=independent_layout)
            except Exception as error:source_lines={'status':'unavailable','text':None,'glyph_coverage':False,'reason':type(error).__name__}
            witness,witness_basis=choose_witness(selected['text'],independent_layout,source_lines) if selected['method']=='pdf_text' else (independent_layout,'pypdf_layout_for_ocr_comparison_only')
            if witness is not None and any(0xD800<=ord(c)<=0xDFFF for c in witness):
                witness=None;witness_basis='invalid_auxiliary_reader_witness_excluded'
            reader_receipts={key:auxiliary_text_receipt(value,source_pdf_sha256=source_pdf_sha,page=selected['page'],page_text_sha256=selected['text_sha256']) for key,value in {'native_text_raw':native,'secondary_text_raw':independent,'secondary_layout_text_raw':independent_layout}.items()}
            errors.extend('invalid_unicode_auxiliary_reader:'+key for key,receipt in reader_receipts.items()if receipt['state']=='invalid_unicode_scalar_sequence')
            glyphs=[]
            for c in page.chars:
                glyphs.append({'text':c.get('text',''), 'bbox':[float(c[k]) for k in ('x0','top','x1','bottom')],
                               'font':c.get('fontname'), 'size':float(c.get('size',0))})
            glyph_text=''.join(c['text'] for c in glyphs)
            opcodes=SequenceMatcher(None,glyph_text,selected['text'],autojunk=False).get_opcodes()
            mapping=[{'kind':tag,'glyph_start':a,'glyph_end':b,'selected_start':c,'selected_end':d} for tag,a,b,c,d in opcodes]
            result.append({'page':selected['page'], 'page_text_sha256':selected['text_sha256'],
                'coordinate_system':'PDF points, top-left', 'width':float(page.width),'height':float(page.height),
                'native_text_raw':native, 'secondary_text_raw':independent,
                'secondary_layout_text_raw':independent_layout,
                'secondary_layout_text_sha256':reader_receipts['secondary_layout_text_raw']['utf8_text_sha256'],
                'auxiliary_reader_receipts':reader_receipts,
                'quality_independent_basis':witness_basis,
                'quality_independent_text':witness,
                'quality_independent_text_sha256':text_hash(witness) if witness is not None else None,
                'pdfium_source_lines':source_lines,
                'native_text_sha256':reader_receipts['native_text_raw']['utf8_text_sha256'],
                'secondary_text_sha256':reader_receipts['secondary_text_raw']['utf8_text_sha256'],
                'glyph_stream_text':glyph_text,'glyphs':glyphs,'alignment':mapping,
                'alignment_status':'observation_only_not_reviewed_reading_order',
                'extraction_errors':errors,
                'nontext_objects':{k:len(getattr(page,k,[]) or []) for k in ('images','curves','rects','lines')},
                'source_vectors':{kind:[{'bbox':[float(shape[k]) for k in ('x0','top','x1','bottom')]} for shape in (getattr(page,kind,[]) or [])] for kind in ('rects','lines')},
                'selected_method':selected['method'],
                'geometry_status':'native_geometry_not_ocr_geometry' if selected['method']!='pdf_text' else 'native_glyphs_retained'})
    return result


def anchor(page: dict, lo: int, hi: int) -> dict:
    from .provisions import span
    return span(page,lo,hi)


def unit_version_id(pdf_sha: str, family: str, label: str, spans: list[dict]) -> str:
    payload={'schema':SCHEMA,'source_pdf_sha256':pdf_sha,'family':family,'label':label,'spans':spans}
    return f"pdf-{pdf_sha[:24]}:unit:{digest(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode())[:24]}"


def structure_document(pages: list[dict], pdf_sha: str, family: str, *, width=1000) -> dict:
    """Complete lexical navigation, not a claim of complete legal boundaries."""
    if family not in FAMILIES:raise ValueError('unknown document family')
    # Repeated long margin text is retained separately, never deleted.
    first_lines=Counter()
    for p in pages:
        first_lines.update(set(line.strip() for line in p['text'].splitlines()[:2] if len(line.strip())>20))
    repeated={line for line,count in first_lines.items() if count>=max(2,(len(pages)+1)//2)}
    numbered_footers=bool(len(pages)>1) and all(p['text'].splitlines() and p['text'].splitlines()[-1].strip()==str(p['page']) for p in pages)
    regions=[];body=[];family_regions=[]
    for p in pages:
        text=p['text'];lines=list(re.finditer(r'[^\n]*(?:\n|$)',text))
        lines=[m for m in lines if m.end()>m.start()]
        lo=0;hi=len(text)
        for m in lines[:2]:
            if m.start()==lo and m.group().strip() in repeated:
                regions.append({'kind':'repeated_header_candidate','span':anchor(p,m.start(),m.end())});lo=m.end()
            else:break
        tail=lines[-3:]
        for m in tail:
            if PAGE_FOOTER.match(m.group().strip()) or (numbered_footers and m==lines[-1] and m.group().strip()==str(p['page'])):
                hi=m.start();break
        if hi<lo:hi=lo
        if hi>lo:
            b={'kind':'body_candidate','span':anchor(p,lo,hi)};regions.append(b);body.append((p,lo,hi))
        if hi<len(text):regions.append({'kind':'footer_candidate','span':anchor(p,hi,len(text))})
        markers=[]
        for role,pattern in PROFILES[family]:
            markers.extend((m.start(),m.end(),role) for m in re.finditer(pattern,text,re.M|re.I))
        markers.sort()
        for i,(start,end,role) in enumerate(markers):
            stop=markers[i+1][0] if i+1<len(markers) else hi
            # Bound candidate blocks by another explicit label too, e.g. Date of Hearing.
            label=re.search(r'(?m)^\s*[A-Za-z][A-Za-z ]{1,35}\s*:',text[end:stop])
            if label:stop=end+label.start()
            if stop>start:
                family_regions.append({'role_candidate':role,'span':anchor(p,start,stop), 'status':'review_required',
                    'warning':'Region is navigation, not verified party identity, legal status or operative effect.'})
    units=[];current=[];label='preamble';sequence=0
    def flush():
        nonlocal current,sequence
        if current:
            unit=select_provision(f'pdf-{pdf_sha[:24]}:unit:{sequence}',label,pages,pdf_sha,current)
            unit['id']=unit_version_id(pdf_sha,family,label,unit['spans'])
            unit['family']=family;unit['review_reasons']=['lexical_numbering_may_be_caption_or_quoted_text','boundaries_and_semantics_require_review']
            units.append(unit);sequence+=1;current=[]
    for p,lo,hi in body:
        starts=list(NUMBER.finditer(p['text'],lo,hi))
        cursor=lo
        for m in starts:
            if m.start()>cursor:current.append({'page':p['page'],'char_start':cursor,'char_length':m.start()-cursor})
            flush();label=m.group(1);cursor=m.start()
        if hi>cursor:current.append({'page':p['page'],'char_start':cursor,'char_length':hi-cursor})
    flush()
    children=[child for unit in units for child in provision_children(unit,pages,width=width)]
    return {'schema_version':SCHEMA,'source_pdf_sha256':pdf_sha,'declared_family':family,
        'status':'candidate_only','child_width':width,'regions':regions,'family_regions':family_regions,
        'units':units,'children':children,'legal_status':'unresolved','verified_claims':0,
        'coverage_scope':'all selected page text; visual completeness and legal boundaries require independent review'}


def validate_structure(structure: dict, pages: list[dict], pdf_sha: str) -> dict:
    by_page={p['page']:p for p in pages}
    if structure.get('schema_version')!=SCHEMA or structure.get('source_pdf_sha256')!=pdf_sha or structure.get('declared_family') not in FAMILIES or structure.get('status')!='candidate_only' or structure.get('verified_claims')!=0 or structure.get('legal_status')!='unresolved':
        raise ValueError('invalid or promoted L1 structure')
    def check(a):
        p=by_page.get(a.get('page'))
        if p is None or a!=anchor(p,a['char_start'],a['char_start']+a['char_length']):raise ValueError('inexact L1 source anchor')
    by_region={n:[] for n in by_page};by_body={n:[] for n in by_page}
    for r in structure['regions']:
        if r['kind'] not in {'body_candidate','footer_candidate','repeated_header_candidate'}:raise ValueError('invalid region')
        a=r['span'];check(a);by_region[a['page']].append((a['char_start'],a['char_start']+a['char_length']))
        if r['kind']=='body_candidate':by_body[a['page']].append((a['char_start'],a['char_start']+a['char_length']))
    def partition(intervals,start,end):
        cursor=start
        for lo,hi in sorted(intervals):
            if lo!=cursor or hi<=lo:raise ValueError('text region gap or overlap')
            cursor=hi
        if cursor!=end:raise ValueError('text region clipping')
    for n,p in by_page.items():partition(by_region[n],0,len(p['text']))
    unit_ids=set();unit_spans={n:[] for n in by_page}
    for unit in structure['units']:
        if unit['id'] in unit_ids or unit['boundary_state']!='candidate' or unit['semantic_state']!='unresolved' or unit['review_receipt_id'] is not None:raise ValueError('duplicate or promoted L1 unit')
        if unit['id']!=unit_version_id(pdf_sha,structure['declared_family'],unit['label'],unit['spans']):raise ValueError('unit version identity mismatch')
        unit_ids.add(unit['id']);validate_wrapper(unit,pages,pdf_sha)
        for a in unit['spans']:unit_spans[a['page']].append((a['char_start'],a['char_start']+a['char_length']))
    for n,expected in by_body.items():
        # Current region builder has at most one body interval per page.
        if expected:partition(unit_spans[n],expected[0][0],expected[-1][1])
        elif unit_spans[n]:raise ValueError('unit body on empty page')
    children=structure['children'];expected_children=[c for u in structure['units'] for c in provision_children(u,pages,width=structure.get('child_width',1000))]
    if children!=expected_children:raise ValueError('child coverage, parent or literal span mismatch')
    for region in structure['family_regions']:
        check(region['span'])
        if region.get('status')!='review_required':raise ValueError('family role candidate promoted')
    return {'physical_pages':len(pages),'selected_characters':sum(len(p['text']) for p in pages),
            'regions':len(structure['regions']),'candidate_units':len(unit_ids),'exact_children':len(children),
            'selected_text_partition':'passed','body_unit_partition':'passed','exact_anchors':'passed',
            'legal_review':'not_run','verified_claims':0}


def readiness_ledger(structure: dict, quality: dict) -> dict:
    outcomes={r['page']:r['outcome'] for r in quality['results']}
    units=[]
    for unit in structure['units']:
        physical_pages=sorted({a['page'] for a in unit['spans']})
        units.append({'unit_id':unit['id'],'pages':physical_pages,
            'source_quality':'passed' if all(outcomes[n]=='auto_accepted' for n in physical_pages) else 'repair_required',
            'boundary_review':'candidate','legal_review':'not_run','eligible_for_legal_assertion':False})
    return {'schema_version':SCHEMA,'units':units,'publication_eligible':False,
        'consumer_rule':'Do not use legacy embedding readiness alone; require this L1 source-quality ledger plus separate legal review/publication gates.'}


def verify_document(output: Path) -> dict:
    from .native_verification_session import active_session
    session=active_session()
    return session.verify(output,_verify_document_uncached)if session is not None else _verify_document_uncached(output)


def _verify_document_uncached(output: Path) -> dict:
    output=Path(output)
    receipt=json.loads((output/'l1-package.json').read_text())
    if receipt.get('schema_version')!=SCHEMA:raise ValueError('unknown L1 schema')
    required={'original.pdf','source-receipt.json','layout.jsonl','structure.json','validation.json','quality.json','repair-queue.jsonl','readiness.json'}
    if 'vision-evidence.json' in receipt.get('artifacts',{}):required.add('vision-evidence.json')
    if set(receipt.get('artifacts',{}))!=required:raise ValueError('incomplete L1 artifact manifest')
    for name,sha in receipt['artifacts'].items():
        if digest((output/name).read_bytes())!=sha:raise ValueError(f'L1 artifact hash mismatch: {name}')
    source=json.loads((output/'source-receipt.json').read_text());original=output/'original.pdf'
    pdf_sha=digest(original.read_bytes())
    if source['actual_sha256']!=pdf_sha or source['declared']['sha256']!=pdf_sha:raise ValueError('L1 original identity mismatch')
    verified=verify_package(output/'extraction',source_pdf=original)
    pages=rows(output/'extraction/pages.jsonl');layout=rows(output/'layout.jsonl')
    if len(layout)!=len(pages):raise ValueError('layout page count mismatch')
    for page,raw in zip(pages,layout):
        if raw['page']!=page['page'] or raw['page_text_sha256']!=page['text_sha256'] or ''.join(g['text'] for g in raw['glyphs'])!=raw['glyph_stream_text']:
            raise ValueError('layout source identity mismatch')
        if 'quality_independent_text' in raw and raw.get('quality_independent_text_sha256')!=(text_hash(raw['quality_independent_text']) if raw['quality_independent_text'] is not None else None):
            raise ValueError('independent native witness hash mismatch')
        validate_auxiliary_text_receipts(raw,pdf_sha)
        verify_invalid_auxiliary_readers(original,raw)
        if raw.get('secondary_layout_text_sha256')!=auxiliary_text_receipt(raw.get('secondary_layout_text_raw'),source_pdf_sha256=pdf_sha,page=raw['page'],page_text_sha256=raw['page_text_sha256'])['utf8_text_sha256']:
            raise ValueError('raw layout-mode text hash mismatch')
        for glyph in raw['glyphs']:
            box=glyph.get('bbox')
            if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or box[0]>box[2] or box[1]>box[3]:
                raise ValueError('invalid native glyph geometry')
        for key in ('native','secondary'):
            text=raw[f'{key}_text_raw']
            if raw[f'{key}_text_sha256']!=auxiliary_text_receipt(text,source_pdf_sha256=pdf_sha,page=raw['page'],page_text_sha256=raw['page_text_sha256'])['utf8_text_sha256']:raise ValueError('raw text hash mismatch')
        a=b=0
        for op in raw['alignment']:
            if op['glyph_start']!=a or op['selected_start']!=b:raise ValueError('alignment gap')
            a=op['glyph_end'];b=op['selected_end']
            if op['kind']=='equal' and raw['glyph_stream_text'][op['glyph_start']:a]!=page['text'][op['selected_start']:b]:raise ValueError('false alignment')
        if (a,b)!=(len(raw['glyph_stream_text']),len(page['text'])):raise ValueError('alignment clipping')
    structure=json.loads((output/'structure.json').read_text())
    checked=validate_structure(structure,pages,pdf_sha)
    if 'vision-evidence.json' in receipt['artifacts']:
        from .l1_qwen import verify_vision_evidence
        verify_vision_evidence(output,pages,pdf_sha)
    quality=json.loads((output/'quality.json').read_text())
    if quality.get('source_pdf_sha256')!=pdf_sha or quality.get('pages_assessed')!=len(pages) or quality.get('source_verified') is not True:
        raise ValueError('quality receipt lacks complete source-bound assessment')
    if [r.get('page') for r in quality['results']]!=list(range(1,len(pages)+1)):
        raise ValueError('quality page coverage mismatch')
    for result,page in zip(quality['results'],pages):
        if result.get('source_pdf_sha256')!=pdf_sha or result.get('page_text_sha256')!=page['text_sha256'] or result.get('outcome') not in {'auto_accepted','repair_required','source_blocked'}:
            raise ValueError('quality page identity mismatch')
    counts={outcome:sum(r['outcome']==outcome for r in quality['results']) for outcome in ('auto_accepted','repair_required','source_blocked')}
    if quality.get('counts')!=counts:raise ValueError('quality outcome counts mismatch')
    saved_readiness=json.loads((output/'readiness.json').read_text())
    # Older v1 packages used 'held' for the same source-quality outcome.
    for unit in saved_readiness.get('units',[]):
        if unit.get('source_quality')=='held':unit['source_quality']='repair_required'
    if saved_readiness!=readiness_ledger(structure,quality):
        raise ValueError('unit readiness ledger mismatch or unsupported promotion')
    repair_queue=rows(output/'repair-queue.jsonl')
    held={r['page'] for r in quality['results'] if r['outcome']!='auto_accepted'}
    if {r['page'] for r in repair_queue}!=held or len(repair_queue)!=len(held):
        raise ValueError('repair queue omits or duplicates a held page')
    for job in repair_queue:
        page=pages[job['page']-1]
        if job['source_pdf_sha256']!=pdf_sha or job['page_text_sha256']!=page['text_sha256'] or job['status']!='awaiting_source_proof':
            raise ValueError('repair job lost source identity')
    stored=json.loads((output/'validation.json').read_text())
    if stored['structure']!=checked:raise ValueError('L1 validation readback mismatch')
    return {'integrity':'passed','source_id':source['declared']['id'],'source_pdf_sha256':pdf_sha,
            'pages':verified['pages'],'structure':checked,'page_text_readiness':verified['page_text_readiness'],
            'quality_counts':quality['counts'],
            'content_ready':quality['counts']['auto_accepted']==len(pages),
            'publication_eligible':False,'legal_review':'not_run'}


def extract_document(source: dict, output: Path, *, ocr='off', language='eng', resume=False) -> dict:
    """Build in a temporary sibling, verify, then atomically publish once."""
    output=Path(output);original=Path(source['path'])
    if not original.is_file() or original.read_bytes()[:5]!=b'%PDF-':raise ValueError('source is not a readable PDF')
    sha=digest(original.read_bytes())
    if sha!=source['sha256']:raise ValueError('source SHA256 mismatch; quarantine before extraction')
    config={'schema':SCHEMA,'ocr':ocr,'language':language,'tables':True,
        'implementation_sha256':digest(Path(__file__).read_bytes()),
        'pdfplumber':version('pdfplumber'),'pypdf':version('pypdf'),
        'extractor_sha256':digest(Path(__file__).with_name('pdf_ingest.py').read_bytes()),
        'package_code_sha256':digest(b''.join(p.name.encode()+p.read_bytes() for p in sorted(Path(__file__).parent.iterdir()) if p.suffix in {'.py','.json'}))}
    request={'source':source,'config':config}
    request_sha=digest(json.dumps(request,sort_keys=True).encode())
    if output.exists():
        if not resume:raise ValueError('output already exists; explicit resume required')
        previous=json.loads((output/'l1-package.json').read_text())
        if previous['request_sha256']!=request_sha:raise ValueError('source/config changed; use a new immutable output path')
        return {**verify_document(output),'reused':True}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-',dir=output.parent) as temporary:
        stage=Path(temporary)/'package';stage.mkdir()
        shutil.copyfile(original,stage/'original.pdf')
        # Check the captured bytes too, eliminating source-change TOCTOU.
        if digest((stage/'original.pdf').read_bytes())!=sha:raise ValueError('source changed during acquisition')
        report=extract_pdf(stage/'original.pdf',stage/'extraction',title=source.get('title') or source['id'],source_url=source.get('url'),ocr=ocr,ocr_language=language,tables=True,native_consensus=True)
        if source.get('expected_pages') is not None and source['expected_pages']!=report['pages']:raise ValueError('expected physical page count mismatch')
        pages=rows(stage/'extraction/pages.jsonl')
        layout=capture_layout(stage/'original.pdf',pages);write_rows(stage/'layout.jsonl',layout)
        structure=structure_document(pages,sha,source['family']);write_json(stage/'structure.json',structure)
        checked=validate_structure(structure,pages,sha)
        from .quality_gate import assess_package
        quality=assess_package(stage/'extraction',stage/'original.pdf',derive_ocr_source_raster=True,
            independent_by_page={p['page']:p['quality_independent_text'] for p in layout if p['quality_independent_text'] is not None},
            derive_independent_native=False)
        quality['independent_extraction_mode']='pypdf_layout_or_exact_source_line_native_consensus'
        quality['independent_basis_by_page']={str(p['page']):p['quality_independent_basis'] for p in layout}
        write_json(stage/'quality.json',quality)
        write_json(stage/'readiness.json',readiness_ledger(structure,quality))
        from .quality_routes import matching_routes
        jobs=[]
        for result in quality.get('results',[]):
            if result['outcome']=='auto_accepted':continue
            jobs.append({'page':result['page'],'source_pdf_sha256':sha,
                'page_text_sha256':result['page_text_sha256'],'status':'awaiting_source_proof',
                'failed_components':result.get('failed_components',[]),
                'missing_evidence':result.get('missing_evidence',[]),
                'routes':[{'id':rule['id'],'action':rule['action']} for rule in matching_routes(result)],
                'mutation_policy':'Repair a new extraction version; never rewrite this immutable L1 package.'})
        write_rows(stage/'repair-queue.jsonl',jobs)
        write_json(stage/'source-receipt.json',{'declared':source,'actual_sha256':sha,'physical_pages':report['pages'],
            'acquisition':'captured_local_file','url_identity':'declared_not_fetched_in_this_run',
            'issuer_role_date_status':'unresolved_requires_source_review','configuration':config})
        write_json(stage/'validation.json',{'schema_version':SCHEMA,'structure':checked,
            'page_readiness':report['page_status_counts'],'raw_capture_errors':[{'page':p['page'],'errors':p['extraction_errors']} for p in layout if p['extraction_errors']],
            'unreviewed':['legal roles/dates/status','reading order','table cells/units/formulas','operative boundaries and later treatment'],
            'database_access':'none','publication_eligible':False})
        names=['original.pdf','source-receipt.json','layout.jsonl','structure.json','validation.json','quality.json','repair-queue.jsonl','readiness.json']
        write_json(stage/'l1-package.json',{'schema_version':SCHEMA,'request_sha256':request_sha,
            'artifacts':{name:digest((stage/name).read_bytes()) for name in names}})
        result=verify_document(stage)
        # A target created concurrently cannot be replaced (nonempty directory).
        stage.rename(output)
    return {**result,'reused':False}


def run(manifest_path: Path, output: Path, *, ocr='off', language='eng', resume=False) -> dict:
    manifest=read_manifest(manifest_path);output=Path(output)
    results=[]
    for source in manifest['sources']:
        try:
            result=extract_document(source,output/source['id'],ocr=ocr,language=language,resume=resume)
            results.append({'id':source['id'],'status':'passed',**result})
        except Exception as error:
            results.append({'id':source['id'],'status':'failed','error':f'{type(error).__name__}: {error}'})
    inventory=family_inventory(manifest)
    report={'schema_version':SCHEMA,'scope':'offline_L1_no_database_or_legal_promotion',
        'documents':results,'family_inventory':inventory,'passed':sum(r['status']=='passed' for r in results),
        'failed':sum(r['status']=='failed' for r in results),
        'content_ready_documents':sum(r.get('content_ready',False) for r in results),
        'all_required_members_supplied':all(not g['missing_source_ids'] for g in inventory),
        'publication_eligible':False,'legal_review':'not_run'}
    output.mkdir(parents=True,exist_ok=True)
    write_json(output/'run-report.json',report)
    return report
