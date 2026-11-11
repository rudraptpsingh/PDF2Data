"""Source-bound repair for a visually reviewed embedded SymbolMT glyph.

The allowlist is deliberately restricted to an exact original and physical pages.
A private-use codepoint or font name is never sufficient on its own.
"""
import hashlib

_SOURCE = '246b1d36592d14da61eb3754ca25e129971cf487e842f87e5db80cd5cbb12591'
_FONT = '583ff499ccabfdb8ad8b00aa80af04cc925c432ed3b2cdb30c21153c8a2df6e8'
_CMAP = '71aedb36f4c09205228ba75048037e7c793956b6eef0334a1651651b63423c8c'
_CONTEXTS = {
    114: 'Supplementary ECR = (\uf044ECR)',
    115: '(\uf044ECR) = Difference between ECR',
}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def repair_reviewed_symbol(source_sha, page_number, selected, native_page, pypdf_page):
    """Return a source-anchored single-character repair or leave text unchanged.

    These exact original pages and glyph forms were compared with the rendered
    source. The page, unique selected context, native glyph geometry and embedded
    font and ToUnicode bytes must all match. No other U+F044 is normalized.
    """
    context = _CONTEXTS.get(page_number)
    if source_sha != _SOURCE or context is None or selected.count(context) != 1:
        return selected, []
    try:
        chars = [c for c in native_page.chars if c.get('text') == '\uf044']
        if len(chars) != 1 or chars[0].get('fontname') != 'SymbolMT':
            return selected, []
        matching = []
        def visitor(text, _cm, _tm, font, _size):
            if '\uf044' in text:
                matching.append((text, font))
        pypdf_page.extract_text(visitor_text=visitor)
        if len(matching) != 1 or matching[0][0] != '\uf044':
            return selected, []
        font = matching[0][1]
        if not font or font.get('/BaseFont') != '/SymbolMT' or font.get('/Encoding') != '/Identity-H':
            return selected, []
        desc = font['/DescendantFonts'][0].get_object()['/FontDescriptor'].get_object()
        if _digest(desc['/FontFile2'].get_object().get_data()) != _FONT:
            return selected, []
        if _digest(font['/ToUnicode'].get_object().get_data()) != _CMAP:
            return selected, []
        char = chars[0]
        box = [float(char[k]) for k in ('x0', 'top', 'x1', 'bottom')]
        expected = {114: [198.8, 133.892, 206.144, 145.892],
                    115: [100.62, 195.092, 107.964, 207.092]}[page_number]
        if any(abs(a - b) > .02 for a, b in zip(box, expected)):
            return selected, []
    except Exception:  # Unavailable or malformed font evidence must fail closed.
        return selected, []
    start = selected.index(context) + context.index('\uf044')
    proof = {'page': page_number, 'selected_start': start, 'selected_end': start + 1,
             'original_quote': '\uf044', 'replacement': 'Δ',
             'source_pdf_sha256': source_sha, 'source_bbox': box,
             'coordinate_system': 'PDF points, top-left', 'font_name': 'SymbolMT',
             'font_file_sha256': _FONT, 'to_unicode_sha256': _CMAP,
             'basis': 'visually_reviewed_exact_original_page_and_embedded_font',
             'legal_review': 'not_run'}
    return selected[:start] + 'Δ' + selected[start + 1:], [proof]
