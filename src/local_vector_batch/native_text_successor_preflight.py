"""Fail-closed preflight for the exact DSM native-reading successor.

This operation does not create an L1 package or grant quality. It only binds an
existing producer parent, targeted extraction and independently replayed text
candidates, and checks that unrelated evidence is unchanged.
"""
from hashlib import sha256
from pathlib import Path
import json

SOURCE='a39c0efc43709b039d6bf3cc34bb45415150eeb9ca299d789c7622bf16788fc7'
PAGE=13


def _sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def _unchanged_pages(before, after, selected=PAGE):
    a={p['page']:p for p in before};b={p['page']:p for p in after}
    if len(a)!=len(before) or len(b)!=len(after) or set(a)!=set(b) or selected not in a:
        raise ValueError('physical page inventory differs')
    if any(a[n]!=b[n] for n in a if n!=selected):
        raise ValueError('unselected page changed')
    return a,b


def inspect(parent, extraction, *, parent_manifest_sha256, extraction_manifest_sha256):
    """Check inputs and replay candidate; does not write or manufacture a successor."""
    import pdfplumber
    from pypdf import PdfReader
    from .legal_l1 import verify_document
    from .evidence_package import verify_package
    from .native_consensus import repair_source_lines
    from .native_dsm_header_candidate import propose
    from .pdf_repair import _page_rows
    parent=Path(parent).resolve();extraction=Path(extraction).resolve()
    if _sha(parent/'l1-package.json')!=parent_manifest_sha256 or _sha(extraction/'evidence-package.json')!=extraction_manifest_sha256:
        raise ValueError('parent or target manifest changed')
    if _sha(parent/'original.pdf')!=SOURCE:
        raise ValueError('original differs from exact DSM source')
    parent_verification=verify_document(parent)
    target_verification=verify_package(extraction,source_pdf=parent/'original.pdf')
    if parent_verification['source_pdf_sha256']!=SOURCE:
        raise ValueError('parent verification source mismatch')
    before,after=_unchanged_pages(_rows(parent/'extraction/pages.jsonl'),_rows(extraction/'pages.jsonl'))
    for name in ('embedding-input.jsonl','fact-candidates.jsonl','structure-candidates.jsonl','tables.json'):
        a=_page_rows(parent/'extraction',name);b=_page_rows(extraction,name)
        if any(a.get(n,[])!=b.get(n,[]) for n in before if n!=PAGE):
            raise ValueError('unselected native records changed: '+name)
        if name=='tables.json' and a!=b:
            raise ValueError('raw native table candidates changed')
    source=parent/'original.pdf';layout=PdfReader(source).pages[PAGE-1].extract_text(extraction_mode='layout') or ''
    with pdfplumber.open(source) as pdf:
        text,repairs=repair_source_lines(source,pdf.pages[PAGE-1],PAGE,before[PAGE]['text'],layout)
    selected=after[PAGE]
    if text!=selected['text'] or repairs!=selected.get('native_line_repairs') or len(repairs)!=8:
        raise ValueError('targeted source-line repair differs from fresh source replay')
    candidate=propose(source,PAGE,text)
    quality_bytes=(parent/'quality.json').read_bytes()
    quality=json.loads(quality_bytes)
    if quality['results'][PAGE-1]['page_text_sha256']!=before[PAGE]['text_sha256']:
        raise ValueError('parent quality/page binding differs')
    qbytes=json.dumps([q for q in quality['results'] if q['page']!=PAGE],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    if (_sha(parent/'l1-package.json')!=parent_manifest_sha256 or _sha(extraction/'evidence-package.json')!=extraction_manifest_sha256
            or _sha(source)!=SOURCE or (parent/'quality.json').read_bytes()!=quality_bytes):
        raise ValueError('bound inputs changed during preflight')
    verify_package(extraction,source_pdf=source)
    return {'schema_version':'pdf2data.native-text-successor-preflight.v1','source_pdf_sha256':SOURCE,
        'parent_manifest_sha256':parent_manifest_sha256,'target_extraction_manifest_sha256':extraction_manifest_sha256,
        'page':PAGE,'old_page_sha256':before[PAGE]['text_sha256'],'formula_page_sha256':selected['text_sha256'],
        'header_candidate_page_sha256':candidate['candidate_page_sha256'],'source_line_repairs':len(repairs),
        'unselected_page_count':len(before)-1,'unselected_quality_records_sha256':sha256(qbytes).hexdigest(),
        'raw_tables_unchanged':True,'candidate':candidate,'status':'preflight_only_no_successor',
        'parent_quality_modified':False,'source_quality_approval_granted':False,'publication_eligible':False}
