"""Reuse exact source-glyph reader spacing for a single conserved prose line."""


def map_prose_reader(line,native_page,pdfium_page,text_page,raw,independent_layout):
    """No changed literals, cross-line ownership or fuzzy paragraph matching."""
    from .native_table_cell_spacing import map_cell_reader
    from .native_consensus import agrees
    compact=lambda text:''.join(c for c in text if not c.isspace())
    selected=line['text'];box=[line[k]for k in('x0','top','x1','bottom')]
    contexts=[row for row in independent_layout.splitlines()if compact(row)==compact(selected)]
    if compact(selected)!=compact(raw)or len(contexts)!=1:return None
    if compact(''.join(c['text']for c in line['chars']))!=compact(selected):return None
    # Prose has no estimated table-cell boundary; any alien or partial glyph
    # must keep its hold rather than invoke the cell-ownership recovery path.
    proof=map_cell_reader(native_page,pdfium_page,text_page,box,selected,raw,independent_layout,_allow_tight_ownership=False)
    if proof.get('status')!='source_proved'or not proof['repairs']or not agrees(selected,proof['mapped_text'],exact_characters=True):return None
    return proof
