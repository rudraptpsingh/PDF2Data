"""Account for source-defined underlines without erasing their style evidence."""
from statistics import median


def source_underlines(page, *, table_count: int = 0) -> list[dict]:
    """Recognize thin native rectangles at the baseline of a text run.

    Do not classify table rules, strike-throughs, fraction bars, isolated dashes
    or arbitrary graphics. Keep the source rectangle and formatting semantics.
    """
    if table_count:return []
    chars=[c for c in page.chars if c.get('text','').strip()]
    result=[]
    for rect in getattr(page,'rects',[]) or []:
        x0,top,x1,bottom=[float(rect[k]) for k in ('x0','top','x1','bottom')]
        if not .2<=bottom-top<=2 or x1-x0<20:continue
        above=[c for c in chars if c['x1']>x0 and c['x0']<x1 and c['top']<top and abs(c['bottom']-bottom)<=2.5]
        if len(above)<3:continue
        size=median(c['bottom']-c['top'] for c in above)
        if size<=0 or not (top>=median(c['top']+.8*(c['bottom']-c['top']) for c in above)):continue
        if abs(min(c['x0'] for c in above)-x0)>size or abs(max(c['x1'] for c in above)-x1)>size:continue
        # A nearby lower operand makes this a possible fraction, not an underline.
        lower=[c for c in chars if bottom<c['top']<bottom+size]
        if lower and all(c['x0']>=x0-2 and c['x1']<=x1+2 for c in lower):continue
        result.append({'classification':'source_text_underline','source_bbox':[x0,top,x1,bottom],
            'coordinate_system':'PDF points, top-left','text_run':''.join(c['text'] for c in sorted(above,key=lambda c:c['x0'])),
            'formatting_semantics':'unresolved_retained_for_downstream_review'})
    return result
