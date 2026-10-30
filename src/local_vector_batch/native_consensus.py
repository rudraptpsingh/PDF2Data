"""Independent PDFium reads of source-defined lines; no selected-text rewriting."""
from collections import Counter
from pathlib import Path
import re
import unicodedata


def _glyph_key(char):
    return (char.get('text',''),)+tuple(round(float(char[k]),5) for k in ('x0','top','x1','bottom'))


def read_source_lines(original: Path, source_page, physical_page: int, secondary_layout_text: str | None = None) -> dict:
    """Geometry orders source lines; PDFium independently reads their contents.

    Every non-space native glyph must occur once in the line geometry. Right-angle
    page rotation uses an explicit metadata inverse; translated frames remain held.
    Bounded reads are not obtained from selected-text strings or matching scores.
    """
    import pypdfium2 as pdfium
    result={'engine':'pdfium_source_lines','status':'unavailable','text':None,'lines':[],'glyph_coverage':False}
    if tuple(source_page.bbox[:2])!=(0,0):
        return {**result,'reason':'unsupported_page_coordinate_system'}
    line_rows=source_page.extract_text_lines(return_chars=True)
    expected=Counter(_glyph_key(c) for c in source_page.chars if c.get('text','').strip())
    actual=Counter(_glyph_key(c) for line in line_rows for c in line.get('chars',[]) if c.get('text','').strip())
    if not expected or actual!=expected:return {**result,'reason':'line_geometry_does_not_cover_every_native_glyph_once'}
    # Superscripts/subscripts can be emitted as separate overlapping text lines.
    # Merge only overlapping physical vertical bands, so a bounded read cannot
    # duplicate the same raised/lowered run in two adjacent source rectangles.
    merged=[]
    for row in sorted(line_rows,key=lambda r:(r['top'],r['x0'])):
        if merged and row['top']<merged[-1]['bottom']-.1:
            group=merged[-1]
            for key in ('x0','top'):group[key]=min(group[key],row[key])
            for key in ('x1','bottom'):group[key]=max(group[key],row[key])
            group['chars'].extend(row.get('chars',[]))
        else:merged.append({k:row[k] for k in ('x0','top','x1','bottom')}|{'chars':list(row.get('chars',[]))})
    with pdfium.PdfDocument(original) as doc:
        page=doc[physical_page-1]
        try:
            if abs(page.get_width()-source_page.width)>.5 or abs(page.get_height()-source_page.height)>.5:
                return {**result,'reason':'independent_page_dimensions_disagree'}
            text_page=page.get_textpage()
            try:
                lines=[]
                for line in merged:
                    box=[max(0,line['x0']-.3),max(0,line['top']-.3),min(source_page.width,line['x1']+.3),min(source_page.height,line['bottom']+.3)]
                    from .native_table_cell_spacing import read_source_box
                    text,source_rectangle=read_source_box(source_page,page,text_page,box)
                    native_line=''.join(c.get('text','') for c in sorted(line.get('chars',[]),key=lambda c:c['x0']))
                    corrected,repairs=repair_control_hyphens(text,native_line,secondary_layout_text)
                    lines.append({'bbox':box,'coordinate_system':'PDF points, top-left','text':corrected,'raw_text':text,'reader_mapping_repairs':repairs,'independent_source_rectangle':source_rectangle})
            finally:text_page.close()
        finally:page.close()
    return {**result,'status':'captured','text':'\n'.join(line['text'] for line in lines),'lines':lines,'glyph_coverage':True}


def agrees(selected: str, witness: str, *, exact_characters=False) -> bool:
    from .quality_gate import WORD,NUMBER,_normalized,_operators,load_policy
    a,b=_normalized(selected),_normalized(witness)
    if WORD.findall(a)!=WORD.findall(b) or NUMBER.findall(a)!=NUMBER.findall(b) or _operators(a,load_policy())!=_operators(b,load_policy()):return False
    if exact_characters and re.sub(r'\s+','',unicodedata.normalize('NFC',selected))!=re.sub(r'\s+','',unicodedata.normalize('NFC',witness)):return False
    return True


def choose_witness(selected: str, layout_text: str | None, source_lines: dict) -> tuple[str | None,str]:
    """Require exact independent consensus, never choose by a higher score."""
    if layout_text is not None and agrees(selected,layout_text):return layout_text,'pypdf_layout'
    other=source_lines.get('text')
    if source_lines.get('status')=='captured' and source_lines.get('glyph_coverage') is True and isinstance(other,str) and agrees(selected,other,exact_characters=True):
        return other,'pdfium_source_lines_exact_native_consensus'
    return layout_text,'pypdf_layout_disagreement_requires_repair'


def repair_source_lines(original: Path, source_page, physical_page: int, selected: str, layout_text: str) -> tuple[str,list[dict]]:
    """Repair only uniquely source-anchored lines supported by two native engines.

    Reordering raised/lowered glyphs is allowed only when every non-space source
    character is conserved and independently ordered PDFium/pypdf content agrees.
    Ambiguous/repeated lines, whole tables and unsupported corrections are skipped.
    """
    native_rows=source_page.extract_text_lines(return_chars=True)
    witness=read_source_lines(original,source_page,physical_page)
    if witness.get('status')!='captured':return selected,[]
    flat='';positions=[]
    for i,char in enumerate(layout_text):
        if not char.isspace():flat+=char;positions.append(i)
    repairs=[]
    for row in witness['lines']:
        box=row['bbox'];text=row['text'].replace('\r\n','\n').strip()
        # A multiline bounded read usually covers a wrapped table cell. Do not
        # flatten that into a legal clause or pretend to have proved its grid.
        if not text or '\n' in text or '\r' in text:continue
        members=[r for r in native_rows if r['top']>=box[1]-.01 and r['bottom']<=box[3]+.01 and r['x0']>=box[0]-.01 and r['x1']<=box[2]+.01]
        group='\n'.join(r['text'] for r in members)
        group_matches=list(re.finditer(r'(?m)^'+re.escape(group)+r'$',selected)) if group else []
        intervals=[(group_matches[0].start(),group_matches[0].end())] if len(group_matches)==1 else []
        for r in ([] if intervals else members):
            value=r['text']
            starts=[m.start() for m in re.finditer(re.escape(value),selected)]
            if len(starts)!=1:intervals=[];break
            intervals.append((starts[0],starts[0]+len(value)))
        if not intervals:continue
        intervals.sort();lo=intervals[0][0];hi=intervals[-1][1]
        if any(selected[a[1]:b[0]].strip() for a,b in zip(intervals,intervals[1:])):continue
        old=selected[lo:hi]
        if agrees(old,text,exact_characters=True):continue
        if Counter(c for c in old if not c.isspace())!=Counter(c for c in text if not c.isspace()):continue
        needle=''.join(text.split())
        if not needle or flat.count(needle)!=1:continue
        offset=flat.index(needle);start=positions[offset];stop=positions[offset+len(needle)-1]+1
        quote=layout_text[start:stop]
        if not agrees(text,quote,exact_characters=True):continue
        if any(lo<r['selected_end'] and hi>r['selected_start'] for r in repairs):continue
        repairs.append({'page':physical_page,'selected_start':lo,'selected_end':hi,'original_quote':old,
            'replacement':text,'source_bbox':box,'coordinate_system':'PDF points, top-left',
            'independent_engine':'pypdf_layout','independent_start':start,'independent_end':stop,
            'independent_quote':quote,'reading_engine':'pdfium_source_lines','basis':'two_native_readers_plus_exact_source_character_conservation',
            'legal_review':'not_run'})
    result=selected
    for repair in sorted(repairs,key=lambda r:r['selected_start'],reverse=True):
        result=result[:repair['selected_start']]+repair['replacement']+result[repair['selected_end']:]
    return result,repairs


def repair_control_hyphens(raw: str, native_glyph_text: str, secondary_layout_text: str | None) -> tuple[str,list[dict]]:
    """Repair PDFium's U+0002 hyphen mapping only with two source witnesses.

    All other source characters must match the source-ordered glyphs exactly;
    the entire corrected physical line must occur uniquely in independent
    pypdf layout output. This never changes selected text or invents a word.
    """
    if '\x02' not in raw or secondary_layout_text is None:return raw,[]
    compact=lambda value:re.sub(r'\s+','',unicodedata.normalize('NFC',value))
    candidate=raw.replace('\x02','-')
    key=compact(candidate)
    if not key or compact(native_glyph_text)!=key or compact(secondary_layout_text).count(key)!=1:return raw,[]
    return candidate,[{'kind':'source_bound_pdfium_control_hyphen_mapping','raw_codepoint':'U+0002','replacement':'-','count':raw.count('\x02'),
        'basis':'exact_source_ordered_native_glyph_line_and_unique_independent_pypdf_layout_line','raw_text':raw,'corrected_text':candidate}]
