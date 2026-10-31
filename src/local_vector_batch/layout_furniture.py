"""Observed margin geometry and formatting; never rewrites source text or quality."""
from collections import Counter
from statistics import median
import re

PAGE_LABEL=re.compile(r'^(?:page\s*[|:]?\s*)?\d+(?:\s*(?:of|/)\s*\d+)?$',re.I)


def key(text):return re.sub(r'\d+','#',''.join(text.split()).casefold())


def margin_segments(layout):
    width,height=layout.get('width'),layout.get('height')
    if not width or not height:return []
    lines=[]
    for glyph in sorted(layout.get('glyphs',[]),key=lambda g:(g['bbox'][1],g['bbox'][0])):
        if not glyph.get('text','').strip():continue
        b=glyph['bbox']
        if not (b[3]<height*.12 or b[1]>height*.88):continue
        if not lines or abs(b[1]-lines[-1][0]['bbox'][1])>2:lines.append([])
        lines[-1].append(glyph)
    segments=[]
    for line in lines:
        groups=[]
        for glyph in sorted(line,key=lambda g:g['bbox'][0]):
            if not groups or glyph['bbox'][0]-groups[-1][-1]['bbox'][2]>max(14,2*glyph.get('size',8)):groups.append([])
            groups[-1].append(glyph)
        for group in groups:
            text='';last=None
            for g in group:
                if last and g['bbox'][0]-last['bbox'][2]>max(1,g.get('size',8)*.16):text+=' '
                text+=g['text'];last=g
            box=[min(g['bbox'][0] for g in group),min(g['bbox'][1] for g in group),max(g['bbox'][2] for g in group),max(g['bbox'][3] for g in group)]
            segments.append(dict(text=text,bbox=box,glyphs=group))
    return segments


def observed_margins(layouts):
    counts=Counter()
    for layout in layouts:
        counts.update(set(key(r['text']) for r in margin_segments(layout) if len(r['text'])>=8))
    return counts


def furniture_candidates(layout,regions,repeated,selected_text):
    width,height=layout.get('width'),layout.get('height')
    if not width or not height:return []
    tables=[r['bbox'] for r in regions if r['type']=='table']
    result=[]
    compact=''.join(selected_text.split())
    for segment in margin_segments(layout):
        text,box=segment['text'],segment['bbox'];top=box[3]<height*.12;bottom=box[1]>height*.88
        if any(min(box[2],b[2])>max(box[0],b[0]) and min(box[3],b[3])>max(box[1],b[1]) for b in tables):continue
        number=bool(PAGE_LABEL.fullmatch(text.strip())) and (
            bool(re.match(r'page\b',text,re.I)) or box[0]>width*.82 or abs((box[0]+box[2])/2-width/2)<width*.04)
        # A small page label must lie at an edge/centre margin, not in a table.
        support=number or repeated[key(text)]>=2 or any(r['type'] in ('header','footer') and r['bbox'][0]-2<=box[0]<=box[2]<=r['bbox'][2]+2 and r['bbox'][1]-2<=box[1]<=box[3]<=r['bbox'][3]+2 for r in regions)
        if not support or not (top or bottom):continue
        kind='page_number' if number else 'header' if top else 'footer'
        if any(r['type']==kind and ''.join(text.split()) in ''.join(r.get('text','').split()) and
               r['bbox'][0]-2<=box[0]<=box[2]<=r['bbox'][2]+2 and r['bbox'][1]-2<=box[1]<=box[3]<=r['bbox'][3]+2 for r in regions):continue
        if any(r['type']==kind and all(abs(a-b)<2 for a,b in zip(r['bbox'],box)) for r in regions):continue
        aligned=''.join(text.split()) in compact
        result.append(dict(kind=kind,text=text,bbox=box,parsed_text_aligned=aligned,
                           basis='native_margin_glyphs_and_printed_page_label' if number else 'native_margin_glyphs_and_repeated_or_existing_furniture',
                           font_names=sorted({g.get('font','') for g in segment['glyphs']})))
    return result


def formatting(regions,width,height):
    bodies=[r['bbox'] for r in regions if r['type'] in ('text','list') and r['bbox'][2]-r['bbox'][0]>width*.35 and r['bbox'][1]>height*.1 and r['bbox'][3]<height*.88]
    left=median(b[0] for b in bodies) if bodies else width*.1
    right=median(b[2] for b in bodies) if bodies else width*.9
    if right-left<width*.3:left,right=width*.1,width*.9
    for region in regions:
        a,b,c,d=region['bbox'];dl=a-left;dr=right-c;tol=max(3,width*.012)
        align='unresolved'
        if abs((a+c)-(left+right))<=tol*2 and dl>tol and dr>tol:align='center_candidate'
        elif abs(dr)<=tol and dl>tol*2 and c-a<(right-left)*.6:align='right_candidate'
        elif abs(dl)<=tol:align='left_or_justified_unresolved' if abs(dr)<=tol else 'left_candidate'
        elif dl>tol and dr>0:align='indented_candidate'
        region['formatting']=dict(alignment_candidate=align,left_indent_points=round(dl,2),right_indent_points=round(dr,2),
                                  page_bbox_points=region['bbox'],reference_body_margins_points=[round(left,2),round(right,2)],
                                  classification_verified=False)
