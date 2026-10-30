"""Lossless invalid auxiliary-reader provenance; ordinary UTF8 hashes stay strict.

Malformed reader codepoints are escaped as JSON witnesses, never interpreted
as corrected legal text. This ports the previously source-validated DDC witness
contract into a reusable module; source extraction and proof epochs stay separate.
"""
import hashlib
import json
import re
from pathlib import Path


def digest(value):return hashlib.sha256(value).hexdigest()
def text_hash(text):return digest(text.encode('utf-8'))
_SURROGATE=re.compile('[\ud800-\udfff]')

def lossless_json_dumps(value,**options):
    """Keep valid Unicode bytes unchanged; escape only invalid scalar codepoints.

    JSON parsers combine adjacent high+low surrogate escapes. Reject those
    non-scalar Python pairs instead of silently changing the codepoint sequence.
    Existing invalid auxiliary witnesses use lone-surrogate escaped receipts.
    """
    options.setdefault('ensure_ascii',False)
    rendered=json.dumps(value,**options)
    matches=list(_SURROGATE.finditer(rendered))
    for a,b in zip(matches,matches[1:]):
        if a.end()==b.start()and 0xD800<=ord(a.group())<=0xDBFF and 0xDC00<=ord(b.group())<=0xDFFF:
            raise ValueError('Adjacent surrogate codepoints require explicit codepoint-array evidence')
    return _SURROGATE.sub(lambda m:'\\u%04x'%ord(m.group()),rendered)

def auxiliary_text_receipt(text, *, source_pdf_sha256: str, page: int, page_text_sha256: str) -> dict:
    """Lossless reader-codepoint identity, never a substitute legal text hash.

    A malformed auxiliary Unicode string remains escaped in layout JSON. It is
    unavailable as ordinary UTF-8 independent text, not silently corrected.
    """
    if text is not None and not isinstance(text,str):raise ValueError('auxiliary reader text must be string or None')
    invalid=[] if text is None else [{'offset':i,'codepoint':ord(c)} for i,c in enumerate(text) if 0xD800<=ord(c)<=0xDFFF]
    state='unavailable' if text is None else ('invalid_unicode_scalar_sequence' if invalid else 'valid_unicode_scalar_sequence')
    escaped=json.dumps(text,ensure_ascii=True,separators=(',',':')).encode('ascii')
    return {'state':state,'source_pdf_sha256':source_pdf_sha256,'page':page,'page_text_sha256':page_text_sha256,
            'python_codepoint_count':len(text) if text is not None else None,'invalid_scalar_positions':invalid,
            'escaped_json_sha256':digest(escaped),'digest_encoding':'JSON ensure_ascii=true, compact separators, ASCII; not legal-page UTF8 text',
            'utf8_text_sha256':text_hash(text) if text is not None and not invalid else None,
            'ordinary_utf8_text_available':text is not None and not invalid}


def validate_auxiliary_text_receipts(raw: dict, source_pdf_sha256: str) -> None:
    keys=('native_text_raw','secondary_text_raw','secondary_layout_text_raw')
    receipts=raw.get('auxiliary_reader_receipts')
    # Old valid packages remain verifiable; invalid outputs always require the
    # new explicit witness contract. Absence is never permission to normalize.
    has_invalid=any(any(0xD800<=ord(c)<=0xDFFF for c in (raw.get(key) or '')) for key in keys)
    if receipts is None:
        if has_invalid:raise ValueError('invalid auxiliary text lacks lossless reader receipt')
        return
    if not isinstance(receipts,dict) or set(receipts)!=set(keys):raise ValueError('auxiliary reader receipt coverage mismatch')
    for key in keys:
        expected=auxiliary_text_receipt(raw.get(key),source_pdf_sha256=source_pdf_sha256,page=raw['page'],page_text_sha256=raw['page_text_sha256'])
        if receipts[key]!=expected:raise ValueError('auxiliary reader codepoint/source identity mismatch')



def verify_invalid_auxiliary_readers(original: Path, raw: dict) -> None:
    """Re-read malformed auxiliary output from its exact original physical page.

    A caller cannot reseal a different invalid witness with self-consistent
    codepoint receipts. This narrow re-read is needed only for invalid readers.
    """
    invalid=[key for key,value in raw.get('auxiliary_reader_receipts',{}).items() if value['state']=='invalid_unicode_scalar_sequence']
    if not invalid:return
    from pypdf import PdfReader
    reader=PdfReader(original);page=reader.pages[raw['page']-1]
    for key in invalid:
        if key=='secondary_text_raw':observed=page.extract_text() or ''
        elif key=='secondary_layout_text_raw':observed=page.extract_text(extraction_mode='layout') or ''
        elif key=='native_text_raw':
            import pdfplumber
            with pdfplumber.open(original) as native:observed=native.pages[raw['page']-1].extract_text() or ''
        else:raise ValueError('unsupported invalid auxiliary reader')
        if observed!=raw[key]:raise ValueError('invalid auxiliary reader differs from exact original re-read')

