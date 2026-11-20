"""Conservative source-bound paint-stack visibility for native PDF glyphs.

Only complete glyph boxes covered by a union of strictly later, opaque,
rectangular fills are excluded from the ADDITIVE visible-glyph candidate. Raw
text/glyphs, clipping contexts and drawings remain preserved. Partial paint,
transparency and unsupported content remain held, never guessed invisible.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def rules():
    return json.loads(Path(__file__).with_name('native_paint_visibility_rules.json').read_text())


def intersect(a, b):
    for box in (a,b):
        if len(box)!=4 or any(not math.isfinite(x)for x in box) or box[0]>box[2]or box[1]>box[3]:
            raise ValueError('Invalid source geometry')
    box = [max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])]
    return box if box[0] < box[2] and box[1] < box[3] else None


def subtract(box, cover):
    """Exact rectangle partition; no rounded gap-filling tolerance."""
    overlap = intersect(box, cover)
    if overlap is None: return [box]
    x0,y0,x1,y1 = box; a,b,c,d = overlap
    pieces = [[x0,y0,a,y1],[c,y0,x1,y1],[a,y0,c,b],[a,d,c,y1]]
    return [p for p in pieces if p[0] < p[2] and p[1] < p[3]]


def coverage(box, fills):
    remaining = [box]
    used = []
    for fill in fills:
        if any(intersect(piece,fill['effective_bbox']) for piece in remaining):
            used.append(fill['seqno'])
            remaining = [p for piece in remaining for p in subtract(piece,fill['effective_bbox'])]
        if not remaining: break
    return {'complete': not remaining, 'remaining_rectangles': remaining, 'cover_seqnos': used}


def opaque_fills(drawings, policy):
    """Resolve rectangular clipping and transparency groups from actual stack."""
    active = {}; accepted = []; unsupported = []
    for drawing in drawings:
        level = drawing['level']
        active = {depth:value for depth,value in active.items() if depth < level}
        kind = drawing['type']
        if kind in ('clip','group'):
            active[level] = drawing
            continue
        if kind not in ('f','fs'):
            continue
        reason = None
        if drawing.get('fill_opacity') != policy['required_fill_opacity']:
            reason = 'nonopaque_fill'
        elif drawing.get('layer'):
            reason = 'optional_content_layer'
        elif not drawing.get('rectangular_path'):
            reason = 'nonrectangular_path'
        elif (not isinstance(drawing.get('fill'),(list,tuple)) or len(drawing['fill'])!=3 or
              any(not math.isfinite(v)or not 0<=v<=1 for v in drawing['fill'])):
            reason = 'unsupported_fill_color'
        effective = drawing.get('rect')
        contexts = []
        for context in active.values():
            contexts.append(context)
            if context.get('layer'):
                reason = 'optional_content_context'
            if context['type'] == 'group':
                if (context.get('opacity') != policy['required_group_opacity'] or
                    context.get('blendmode') not in policy['allowed_group_blend_modes'] or
                    context.get('knockout') is not False):
                    reason = 'unsupported_transparency_group'
            elif not context.get('rectangular_path'):
                reason = 'nonrectangular_clip'
            else:
                effective = intersect(effective,context['rect']) if effective else None
        if reason or effective is None:
            unsupported.append({'seqno': drawing.get('seqno'), 'reason': reason or 'empty_effective_clip'})
            continue
        accepted.append({'seqno': drawing['seqno'], 'effective_bbox': effective,
                         'source_bbox': drawing['rect'], 'fill': drawing['fill'],
                         'fill_opacity': drawing['fill_opacity'], 'clip_and_group_context': contexts})
    return accepted, unsupported


def classify_glyphs(glyphs, fills, policy, *, use_spatial_index=True):
    from .source_rectangle_index import SourceRectangleIndex
    index=SourceRectangleIndex([f['effective_bbox']for f in fills])if use_spatial_index else None
    result = []
    for glyph in glyphs:
        candidates=index.candidates(glyph.get('proof_bbox',glyph['bbox']))if index is not None else range(len(fills))
        later = [fills[i]for i in candidates if fills[i]['seqno'] > glyph['seqno']]
        # Font metric boxes include unpainted descender space. A source text
        # paint's complete ink bounds can prove a later underline does not
        # touch any glyph in that paint, without inventing tighter per-character
        # bounds or removing any visible glyph. Only the actual fill-text paint
        # log is accepted; overlapping paint remains subject to normal proof.
        disjoint=[]
        if glyph.get('source_text_paint_kind')=='fill-text':
            ink=glyph.get('source_text_paint_bbox')
            if not isinstance(ink,list)or len(ink)!=4:
                raise ValueError('Complete source text paint bounds required')
            retained=[]
            for fill in later:
                if intersect(ink,fill['effective_bbox'])is None:disjoint.append(fill['seqno'])
                else:retained.append(fill)
            later=retained
        proof = coverage(glyph.get('proof_bbox',glyph['bbox']),later)
        supported = (glyph['type'] in policy['allowed_text_paint_types'] and glyph['opacity'] == 1.0 and
                     not glyph['layer'] and glyph['direction'] == [1.0,0.0] and glyph['wmode'] == 0)
        if glyph['text'].isspace():
            # Keep raw whitespace unchanged. Its advance box is not painted ink.
            state = 'retained_raw_whitespace'
        elif not supported:
            state = 'held_unsupported_text_paint'
        elif proof['complete'] and glyph.get('ink_bounds_basis') != 'single_character_paint_bbox':
            state = 'held_unproved_individual_ink_bounds'
        elif proof['complete']:
            state = 'fully_occluded'
        elif proof['cover_seqnos']:
            state = 'held_partial_occlusion'
        else:
            state = 'retained_visible_or_unproven'
        result.append({**glyph, 'visibility_state': state, 'coverage': proof,
                       'source_ink_disjoint_later_fill_seqnos':disjoint,
                       'excluded_from_visible_candidate': state == 'fully_occluded'})
    return result


READER = 'pdfium'
_PAINT_OPS = {b'f', b'F', b'f*', b'S', b's', b'B', b'B*', b'b', b'b*'}
_SHOW_OPS = {b'Tj', b'TJ', b"'", b'"'}


def _soft_mask_guard(page):
    # A soft mask can make an apparently opaque fill translucent without a
    # scalar opacity in the paint trace. Fail closed, even for unused masks.
    seen=set()
    def inspect_resources(resources):
        resources=resources.get_object()
        if id(resources) in seen:return
        seen.add(id(resources))
        states=resources.get('/ExtGState',{})
        if hasattr(states,'get_object'):states=states.get_object()
        for state in states.values():
            state=state.get_object()
            if state.get('/SMask','/None') != '/None':raise ValueError('Soft-mask paint visibility is unsupported')
        objects=resources.get('/XObject',{})
        if hasattr(objects,'get_object'):objects=objects.get_object()
        for obj in objects.values():
            obj=obj.get_object()
            if obj.get('/SMask') is not None:raise ValueError('Image soft-mask visibility is unsupported')
            if obj.get('/Resources') is not None:inspect_resources(obj['/Resources'])
    inspect_resources(page.get('/Resources',{}))


def _content_events(reader, page):
    """Paint events in content-stream order, as the stream itself declares them.

    pdfium exposes paint order and geometry but not two facts the visibility
    rules were written against: which show operators share one BT..ET text
    paint, and whether a filled path was a single ``re`` rectangle. Both are
    read from the operators. Returns (text_blocks, single_re_paths, vertical).
    """
    from pypdf.generic import ContentStream
    blocks=[];paths=[];vertical=[False];counter=[0];stack=set()
    def fonts_vertical(resources):
        for font in (resources.get('/Font') or {}).values():
            encoding=font.get_object().get('/Encoding')
            if isinstance(encoding,str) and encoding.endswith('-V'):vertical[0]=True
    def walk(stream,resources):
        resources=resources.get_object() if hasattr(resources,'get_object') else (resources or {})
        fonts_vertical(resources)
        construction=[]
        for operands,op in ContentStream(stream,reader).operations:
            if op==b'BT':counter[0]+=1
            elif op in _SHOW_OPS:
                strings=operands[0] if op==b'TJ' else operands[-1:]
                if any(not isinstance(s,(int,float)) and len(s) for s in strings):blocks.append(counter[0])
            elif op in (b're',b'm',b'l',b'c',b'v',b'y',b'h'):construction.append(op)
            elif op in _PAINT_OPS:
                # ``h`` after ``re`` closes an already closed subpath: a no-op.
                paths.append([o for o in construction if o!=b'h']==[b're'] and construction[0]==b're')
                construction=[]
            elif op==b'n':construction=[]
            elif op==b'Do':
                xobject=(resources.get('/XObject') or {}).get(operands[0])
                xobject=xobject.get_object() if xobject is not None else None
                if xobject is not None and xobject.get('/Subtype')=='/Form' and id(xobject) not in stack:
                    stack.add(id(xobject))
                    counter[0]+=1  # a form never continues the caller's text paint
                    walk(xobject,xobject.get('/Resources',resources))
                    stack.discard(id(xobject))
    contents=page.get_contents()
    if contents is not None:walk(contents,page.get('/Resources',{}))
    return blocks,paths,vertical[0]


def _pdfium_objects(raw,page_handle):
    """Leaf paint objects depth-first, with their page matrix and inherited state."""
    import ctypes
    def matrix_of(obj):
        m=raw.FS_MATRIX();raw.FPDFPageObj_GetMatrix(obj,ctypes.byref(m));return (m.a,m.b,m.c,m.d,m.e,m.f)
    def compose(m,n):  # apply m, then n
        a,b,c,d,e,f=m;A,B,C,D,E,F=n
        return (a*A+b*C,a*B+b*D,c*A+d*C,c*B+d*D,e*A+f*C+E,e*B+f*D+F)
    def marks(obj):
        names=[]
        for i in range(raw.FPDFPageObj_CountMarks(obj)):
            mark=raw.FPDFPageObj_GetMark(obj,i);size=ctypes.c_ulong()
            raw.FPDFPageObjMark_GetName(mark,None,0,ctypes.byref(size))
            buffer=ctypes.create_string_buffer(size.value)
            raw.FPDFPageObjMark_GetName(mark,ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ushort)),size.value,ctypes.byref(size))
            names.append(buffer.raw[:max(0,size.value-2)].decode('utf-16-le'))
        return names
    out=[]
    def walk(count,get,parent,layer,groups):
        for i in range(count):
            obj=get(i);kind=raw.FPDFPageObj_GetType(obj)
            own=matrix_of(obj)
            obj_layer=layer or ('OC' if 'OC' in marks(obj) else '')
            if kind==raw.FPDF_PAGEOBJ_FORM:
                group=groups+([{'transparent':bool(raw.FPDFPageObj_HasTransparency(obj))}])
                walk(raw.FPDFFormObj_CountObjects(obj),lambda j,o=obj:raw.FPDFFormObj_GetObject(o,j),
                     compose(own,parent),obj_layer,group)
            else:
                # Leaf objects inside a form carry form-space matrices.
                out.append({'handle':obj,'kind':kind,'matrix':own,'page_matrix':parent,'layer':obj_layer,'groups':groups})
    identity=(1.0,0.0,0.0,1.0,0.0,0.0)
    walk(raw.FPDFPage_CountObjects(page_handle),lambda i:raw.FPDFPage_GetObject(page_handle,i),identity,'',[])
    return out


def capture(source, page_number):
    """Glyphs, paths, clips and groups of one page in paint order (pdfium).

    The output keeps the contract the visibility rules were written against:
    top-left page coordinates, a glyph ``seqno`` equal to its text paint's
    index in ``paint_bbox_log``, one text paint per BT..ET block, and drawing
    rows whose ``level`` nests the clip and transparency contexts in force.
    """
    import ctypes, math
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from pypdf import PdfReader
    source=Path(source)
    reader=PdfReader(source);source_page=reader.pages[page_number-1]
    _soft_mask_guard(source_page)
    blocks,single_re,vertical=_content_events(reader,source_page)
    document=pdfium.PdfDocument(source)
    try:
        page=document[page_number-1]
        if raw.FPDFPage_GetRotation(page.raw) or int(source_page.get('/Rotate',0) or 0)%360:
            raise ValueError('Rotated page visibility is not supported by this rule')
        left,bottom,right,top=page.get_cropbox()
        def point(x,y):return (x-left,top-y)
        def box(x0,y0,x1,y1):
            (a,b),(c,d)=point(x0,y0),point(x1,y1)
            return [min(a,c),min(b,d),max(a,c),max(b,d)]
        def apply(m,x,y):return (m[0]*x+m[2]*y+m[4],m[1]*x+m[3]*y+m[5])
        def compose(m,n):
            a,b,c,d,e,f=m;A,B,C,D,E,F=n
            return (a*A+b*C,a*B+b*D,c*A+d*C,c*B+d*D,e*A+f*C+E,e*B+f*D+F)
        def rgba(getter,obj):
            r,g,b,a=(ctypes.c_uint() for _ in range(4))
            if not getter(obj,*(ctypes.byref(v) for v in (r,g,b,a))):return None,None
            return [r.value/255,g.value/255,b.value/255],a.value/255
        def rectangle(points):
            """Axis-aligned rectangle from transformed polygon corners, else None."""
            if len(points)==5 and all(abs(p-q)<1e-9 for p,q in zip(points[0],points[4])):points=points[:4]
            if len(points)!=4:return None
            xs=sorted({round(p[0],9) for p in points});ys=sorted({round(p[1],9) for p in points})
            if len(xs)!=2 or len(ys)!=2:return None
            for p,q in zip(points,points[1:]+points[:1]):
                if round(p[0],9)!=round(q[0],9) and round(p[1],9)!=round(q[1],9):return None
            return box(xs[0],ys[0],xs[1],ys[1])
        def segments(count,get):
            points=[];curved=False;subpaths=0;closed=False
            for j in range(count):
                seg=get(j);x=ctypes.c_float();y=ctypes.c_float()
                raw.FPDFPathSegment_GetPoint(seg,ctypes.byref(x),ctypes.byref(y))
                kind=raw.FPDFPathSegment_GetType(seg)
                if kind==raw.FPDF_SEGMENT_MOVETO:subpaths+=1
                if kind==raw.FPDF_SEGMENT_BEZIERTO:curved=True
                closed=closed or bool(raw.FPDFPathSegment_GetClose(seg))
                points.append((x.value,y.value))
            return points,curved,subpaths,closed
        objects=_pdfium_objects(raw,page.raw)
        log=[];drawings=[{'type':'group','level':0,'seqno':None,'layer':'','fill_opacity':None,'opacity':1.0,
                          'isolated':True,'knockout':False,'blendmode':'Normal','rect':box(left,bottom,right,top),
                          'rectangular_path':False,'fill':None}]
        texts=[o for o in objects if o['kind']==raw.FPDF_PAGEOBJ_TEXT]
        paths=[o for o in objects if o['kind']==raw.FPDF_PAGEOBJ_PATH]
        # Operator facts align only when both sides saw the same paints;
        # otherwise fall back to the conservative reading (coarser text
        # paints, no path proved to be a single rectangle).
        aligned_text=len(blocks)==len(texts);aligned_paths=len(single_re)==len(paths)
        text_index={};path_index={}
        for i,o in enumerate(texts):text_index[id(o)]=i
        for i,o in enumerate(paths):path_index[id(o)]=i
        handle_of=lambda h:ctypes.cast(h,ctypes.c_void_p).value
        text_paint={};previous_block=None;previous_was_text=False;last_text_contexts=None
        def contexts_for(o):
            rows=[]
            for g in o['groups']:
                if g['transparent']:
                    rows.append({'type':'group','opacity':None,'blendmode':'transparency','knockout':None,
                                 'isolated':None,'rect':None,'rectangular_path':False,'layer':o['layer']})
            if raw.FPDFPageObj_HasTransparency(o['handle']) and o['kind']==raw.FPDF_PAGEOBJ_PATH:
                _,alpha=rgba(raw.FPDFPageObj_GetFillColor,o['handle'])
                if alpha==1.0:  # opaque fill colour, yet blended or masked
                    rows.append({'type':'group','opacity':None,'blendmode':'transparency','knockout':None,
                                 'isolated':None,'rect':None,'rectangular_path':False,'layer':o['layer']})
            clip=raw.FPDFPageObj_GetClipPath(o['handle'])
            if clip:
                for p in range(raw.FPDFClipPath_CountPaths(clip)):
                    n=raw.FPDFClipPath_CountPathSegments(clip,p)
                    if n<=0:
                        rows.append({'type':'clip','rect':None,'rectangular_path':False,'layer':o['layer']});continue
                    pts,curved,subpaths,_=segments(n,lambda j,c=clip,p=p:raw.FPDFClipPath_GetPathSegment(c,p,j))
                    rect=None if curved or subpaths!=1 else rectangle(pts)
                    if rect is None and pts:
                        xs=[x for x,_ in pts];ys=[y for _,y in pts]
                        bounds=box(min(xs),min(ys),max(xs),max(ys))
                    rows.append({'type':'clip','rect':rect or bounds,'rectangular_path':rect is not None,'layer':o['layer']})
            return rows
        for o in objects:
            h=o['handle'];kind=o['kind'];m=compose(o['matrix'],o['page_matrix'])
            l,b,r,t=(ctypes.c_float() for _ in range(4))
            raw.FPDFPageObj_GetBounds(h,*(ctypes.byref(v) for v in (l,b,r,t)))
            corners=[apply(o['page_matrix'],x,y) for x,y in ((l.value,b.value),(r.value,t.value),(l.value,t.value),(r.value,b.value))]
            xs=[x for x,_ in corners];ys=[y for _,y in corners]
            bounds=box(min(xs),min(ys),max(xs),max(ys))
            if kind==raw.FPDF_PAGEOBJ_TEXT:
                # Clip and transparency contexts of text paints are recorded too,
                # so callers that check every context on the page see them.
                contexts=contexts_for(o);key=repr(contexts)
                if contexts and key!=last_text_contexts:
                    for level,row in enumerate(contexts,1):
                        drawings.append({'level':level,'seqno':None,'fill_opacity':None,'opacity':row.get('opacity'),
                                         'isolated':row.get('isolated'),'knockout':row.get('knockout'),
                                         'blendmode':row.get('blendmode'),'fill':None,**row})
                last_text_contexts=key
                block=blocks[text_index[id(o)]] if aligned_text else None
                if previous_was_text and (block==previous_block or not aligned_text):
                    text_paint[handle_of(h)]=len(log)-1;previous_block=block;continue
                mode=raw.FPDFTextObj_GetTextRenderMode(h)
                paint_kind={0:'fill-text',1:'stroke-text',2:'fill-text',3:'ignore-text'}.get(mode,'clip-text')
                text_paint[handle_of(h)]=len(log)
                log.append({'kind':paint_kind,'bbox':None,'mode':mode,'layer':o['layer']})
                previous_block=block;previous_was_text=True
                continue
            previous_was_text=False;previous_block=None
            if kind==raw.FPDF_PAGEOBJ_PATH:
                fillmode=ctypes.c_int();stroke=ctypes.c_int()
                raw.FPDFPath_GetDrawMode(h,ctypes.byref(fillmode),ctypes.byref(stroke))
                fill=fillmode.value!=0;stroked=bool(stroke.value)
                if not fill and not stroked:continue
                pts,curved,subpaths,_=segments(raw.FPDFPath_CountSegments(h),lambda j,h=h:raw.FPDFPath_GetPathSegment(h,j))
                pts=[apply(m,x,y) for x,y in pts]
                rect=None if curved or subpaths!=1 else rectangle(pts)
                declared=single_re[path_index[id(o)]] if aligned_paths else False
                rectangular=bool(rect) and declared
                if pts:
                    xs=[x for x,_ in pts];ys=[y for _,y in pts];geometry=box(min(xs),min(ys),max(xs),max(ys))
                else:geometry=bounds
                fill_color,fill_alpha=rgba(raw.FPDFPageObj_GetFillColor,h) if fill else (None,None)
                _,stroke_alpha=rgba(raw.FPDFPageObj_GetStrokeColor,h) if stroked else (None,None)
                contexts=contexts_for(o)
                level=1
                for row in contexts:
                    drawings.append({'level':level,'seqno':None,'fill_opacity':None,'opacity':row.get('opacity'),
                                     'isolated':row.get('isolated'),'knockout':row.get('knockout'),
                                     'blendmode':row.get('blendmode'),'fill':None,**row});level+=1
                seqno=len(log)
                if fill:log.append({'kind':'fill-path','bbox':geometry})
                if stroked:log.append({'kind':'stroke-path','bbox':bounds})
                drawings.append({'type':'fs' if fill and stroked else 'f' if fill else 's','level':level,'seqno':seqno,
                                 'layer':o['layer'],'fill_opacity':fill_alpha,'opacity':stroke_alpha,'isolated':None,
                                 'knockout':None,'blendmode':None,'rect':rect if rectangular else geometry,
                                 'rectangular_path':rectangular,'fill':fill_color})
                continue
            contexts=contexts_for(o)
            for level,row in enumerate(contexts,1):
                drawings.append({'level':level,'seqno':None,'fill_opacity':None,'opacity':row.get('opacity'),
                                 'isolated':row.get('isolated'),'knockout':row.get('knockout'),
                                 'blendmode':row.get('blendmode'),'fill':None,**row})
            log.append({'kind':'fill-image' if kind==raw.FPDF_PAGEOBJ_IMAGE else 'fill-shade','bbox':bounds})
        textpage=raw.FPDFText_LoadPage(page.raw)
        try:
            glyphs=[];per_object={}
            for i in range(raw.FPDFText_CountChars(textpage)):
                if raw.FPDFText_IsGenerated(textpage,i)==1:continue
                obj=raw.FPDFText_GetTextObject(textpage,i)
                seqno=text_paint.get(handle_of(obj)) if obj else None
                if seqno is None:raise ValueError('Text-page character without a source text paint')
                key=handle_of(obj);index=per_object.setdefault(key,[len(per_object),0])
                codepoint=raw.FPDFText_GetUnicode(textpage,i)
                rect=raw.FS_RECTF();raw.FPDFText_GetLooseCharBox(textpage,i,ctypes.byref(rect))
                loose=box(rect.left,rect.bottom,rect.right,rect.top)
                l,r,b,t=(ctypes.c_double() for _ in range(4))
                raw.FPDFText_GetCharBox(textpage,i,*(ctypes.byref(v) for v in (l,r,b,t)))
                x=ctypes.c_double();y=ctypes.c_double();raw.FPDFText_GetCharOrigin(textpage,i,ctypes.byref(x),ctypes.byref(y))
                origin=list(point(x.value,y.value))
                ink=box(l.value,b.value,r.value,t.value) if chr(codepoint).strip() else [origin[0],origin[1],origin[0],origin[1]]
                size=ctypes.c_int();raw.FPDFText_GetFontInfo(textpage,i,None,0,ctypes.byref(size))
                n=raw.FPDFText_GetFontInfo(textpage,i,None,0,None);name=ctypes.create_string_buffer(max(n,1))
                raw.FPDFText_GetFontInfo(textpage,i,name,n,None)
                color,alpha=rgba(lambda _,*v:raw.FPDFText_GetFillColor(textpage,i,*v),None)
                angle=raw.FPDFText_GetCharAngle(textpage,i)
                direction=[round(math.cos(angle),9)+0.0,round(-math.sin(angle),9)+0.0] if angle>=0 else [1.0,0.0]
                paint=log[seqno]
                glyphs.append({'seqno':seqno,'object':index[0],'index':index[1],'codepoint':codepoint,'text':chr(codepoint),
                               'glyph_id':None,'origin':origin,'bbox':loose,'ink':ink,
                               'font':name.value.decode('utf-8','replace'),'size':raw.FPDFText_GetFontSize(textpage,i),
                               'type':paint['mode'],'opacity':alpha,'color':color,'direction':direction,
                               'wmode':1 if vertical else 0,'layer':paint['layer']})
                index[1]+=1
        finally:
            raw.FPDFText_ClosePage(textpage)
        # Text paint ink bounds: union of the glyph outline boxes of the paint.
        from collections import Counter
        members=Counter(g['seqno'] for g in glyphs)
        for g in glyphs:
            paint=log[g['seqno']];ink=g['ink'] if g['text'].strip() else None
            if ink:paint['bbox']=ink if paint['bbox'] is None else [min(paint['bbox'][0],ink[0]),min(paint['bbox'][1],ink[1]),
                                                                    max(paint['bbox'][2],ink[2]),max(paint['bbox'][3],ink[3])]
        for g in glyphs:
            paint=log[g['seqno']]
            if paint['bbox'] is None:paint['bbox']=list(g['ink'])
        out=[]
        for g in glyphs:
            paint=log[g['seqno']];ink_bbox=paint['bbox']
            single=members[g['seqno']]==1 and not g['text'].isspace() and paint['kind']=='fill-text'
            bbox=g['bbox']
            proof_bbox=[min(bbox[0],ink_bbox[0]),min(bbox[1],ink_bbox[1]),max(bbox[2],ink_bbox[2]),max(bbox[3],ink_bbox[3])] if single else list(bbox)
            out.append({'glyph_key':f"{g['seqno']}:{g['object']}:{g['index']}",'seqno':g['seqno'],
                        'source_text_paint_kind':paint['kind'],'source_text_paint_bbox':list(ink_bbox),
                        'index':g['index'],'codepoint':g['codepoint'],'text':g['text'],'glyph_id':None,
                        'origin':g['origin'],'bbox':bbox,'font':g['font'],'size':g['size'],'type':g['type'],
                        'opacity':g['opacity'],'color':g['color'],'direction':g['direction'],'wmode':g['wmode'],
                        'layer':g['layer'],'proof_bbox':proof_bbox,'paint_ink_bbox':list(ink_bbox) if single else None,
                        'ink_bounds_basis':'single_character_paint_bbox' if single else 'not_individually_proved'})
        return {'source_pdf_sha256':sha(source.read_bytes()),'page':page_number,'page_bbox':[0.0,0.0,right-left,top-bottom],
                'reader':READER,'reader_version':f"pypdfium2 {pdfium.version.PYPDFIUM_INFO} / pdfium {pdfium.version.PDFIUM_INFO}",
                'glyphs':out,'drawings':drawings,
                'paint_bbox_log':[{'kind':p['kind'],'bbox':list(p['bbox'] or [0.0,0.0,0.0,0.0])} for p in log]}
    finally:
        document.close()


def _crop(source, page, box):
    import pdfplumber
    with pdfplumber.open(source) as pdf:
        image = pdf.pages[page-1].crop(tuple(box)).to_image(resolution=240)
        buffer = io.BytesIO(); image.save(buffer,format='PNG'); return buffer.getvalue()


def analyze(source, page_number, regions):
    policy_document = rules()
    policy = policy_document['rules']['later_opaque_rectangular_fill']
    captured = capture(source,page_number)
    fills,unsupported = opaque_fills(captured['drawings'],policy)
    classified = classify_glyphs(captured['glyphs'],fills,policy)
    region_results = []
    for region in regions:
        box = region['bbox']
        glyphs = [g for g in classified if intersect(g['bbox'],box)]
        pixels = _crop(source,page_number,box)
        region_results.append({**region,'render_dpi':240,'render_sha256':sha(pixels),
                               'glyphs':glyphs,
                               'excluded_glyph_keys':[g['glyph_key'] for g in glyphs if g['excluded_from_visible_candidate']],
                               'retained_paint_order_text':''.join(g['text'] for g in glyphs if not g['excluded_from_visible_candidate']),
                               'partial_or_unsupported_glyph_keys':[g['glyph_key'] for g in glyphs if g['visibility_state'].startswith('held_')],
                               'status':'held' if any(g['visibility_state'].startswith('held_') for g in glyphs) else 'glyph_visibility_proved',
                               'page_quality_clearance':False})
    proof = {'contract_version':'pdf2data.native-paint-visibility-proof.v1','source':captured,
             'policy':policy_document,'policy_sha256':sha(canonical(policy_document)),
             'implementation_sha256':sha(Path(__file__).read_bytes()),
             'accepted_opaque_fills':fills,'unsupported_paints':unsupported,
             'classified_glyphs':classified,'regions':region_results,
             'original_text_layer_preserved':True,'source_text_modified':False,
             'source_packages_modified':False,'page_quality_clearance':False,
             'publication_eligible':False,'legal_correctness_claimed':False}
    return proof


def build(source,page_number,regions,output):
    output = Path(output)
    if output.exists():raise ValueError('Visibility output must be new')
    proof = analyze(source,page_number,regions)
    output.mkdir(parents=True)
    (output/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    for i,region in enumerate(regions,1):
        pixels=_crop(source,page_number,region['bbox'])
        if sha(pixels)!=proof['regions'][i-1]['render_sha256']:raise ValueError('Source changed during capture')
        (output/f'region-{i:03}.png').write_bytes(pixels)
    receipt={'contract_version':'pdf2data.native-paint-visibility-receipt.v1','source_pdf_sha256':proof['source']['source_pdf_sha256'],
             'page':page_number,'proof_sha256':sha((output/'proof.json').read_bytes()),
             'fully_occluded_glyphs':sum(g['excluded_from_visible_candidate'] for g in proof['classified_glyphs']),
             'partial_or_unsupported_glyphs':sum(g['visibility_state'].startswith('held_') for g in proof['classified_glyphs']),
             'page_quality_clearance':False,'source_text_modified':False,'publication_eligible':False}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def verify(folder,source):
    folder=Path(folder); proof=json.loads((folder/'proof.json').read_text()); receipt=json.loads((folder/'receipt.json').read_text())
    if receipt['proof_sha256']!=sha((folder/'proof.json').read_bytes()):raise ValueError('Visibility proof hash changed')
    regions=[{k:v for k,v in r.items() if k not in ('render_dpi','render_sha256','glyphs','excluded_glyph_keys',
                 'retained_paint_order_text','partial_or_unsupported_glyph_keys','status','page_quality_clearance')}
             for r in proof['regions']]
    fresh=analyze(source,proof['source']['page'],regions)
    if fresh!=proof:raise ValueError('Visibility proof differs from fresh original source/paint stack')
    for i,r in enumerate(fresh['regions'],1):
        if sha((folder/f'region-{i:03}.png').read_bytes())!=r['render_sha256']:raise ValueError('Saved source crop changed')
    return {'verified':True,'source_pdf_sha256':fresh['source']['source_pdf_sha256'],
            'excluded_glyphs':sum(g['excluded_from_visible_candidate']for g in fresh['classified_glyphs']),
            'whole_page_clearance':False,'publication_eligible':False}


def attach_table_candidates(proof_folder,source,interpretation_folder,output):
    """One fresh verification boundary, then reuse the verified bounded result.

    These remain additive reconstructed CELL candidates. Unproved partial ink,
    legacy font Unicode and table/global quality holds are carried forward.
    """
    folder=Path(proof_folder);output=Path(output)
    if output.exists():raise ValueError('Candidate output must be new')
    checked=verify(folder,source)  # exactly one actual fresh source boundary
    proof=json.loads((folder/'proof.json').read_text())
    origin=Path(interpretation_folder)
    origin_receipt=json.loads((origin/'receipt.json').read_text())
    for name,digest in origin_receipt['files'].items():
        if sha((origin/name).read_bytes())!=digest:raise ValueError('Interpreted source-cell evidence changed')
    candidates=[]
    for file in sorted(origin.glob('candidate-*.json')):
        if file.name.endswith('-snapshot.json'):continue
        candidate=json.loads(file.read_text())
        if (candidate['source_pdf_sha256']!=checked['source_pdf_sha256']or
                candidate['page']!=proof['source']['page']):continue
        ids=candidate['interpretation']['source_cell_ids']
        if len(ids)!=1:raise ValueError('Paint overlay currently supports one exact source cell')
        region=[r for r in proof['regions']if r['id']==ids[0]]
        if len(region)!=1 or region[0]['bbox']!=candidate['original_cells'][0]['bbox']:
            raise ValueError('Paint proof does not bind exact interpreted cell')
        region=region[0]
        candidate.update(paint_visibility={'proof_sha256':sha((folder/'proof.json').read_bytes()),
            'source_verified_once':True,'excluded_glyph_keys':region['excluded_glyph_keys'],
            'partial_or_unsupported_glyph_keys':region['partial_or_unsupported_glyph_keys'],
            'source_region_status':region['status'],'raw_layer_retained':True,
            'full_page_gates':'not_run_for_new_reconstruction'},
            reconstructed_cell={'cell_id':ids[0], 'bbox':region['bbox'],
                'text':candidate['interpretation']['literal_text'],
                'text_sha256':candidate['literal_sha256'],
                'typography':candidate['interpretation'].get('typography',[]),
                'status':'candidate_with_unresolved_source_visibility'if region['status']=='held'else'candidate_needs_full_source_gates'},
            source_quality_clearance=False,publication_eligible=False)
        candidates.append(candidate)
    if not candidates:raise ValueError('No matching interpreted source cells')
    output.mkdir(parents=True)
    for i,c in enumerate(candidates,1):
        (output/f'candidate-{i:03}.json').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n')
    receipt={'contract_version':'pdf2data.paint-bound-reconstructed-table-cells.v1',
             'source_pdf_sha256':checked['source_pdf_sha256'],'candidate_count':len(candidates),
             'fresh_source_decoder_invocations':1,'original_layers_preserved':True,
             'new_whole_page_clears':0,'publication_eligible':False,
             'files':{p.name:sha(p.read_bytes())for p in sorted(output.iterdir())}}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path)
    p.add_argument('--page',type=int);p.add_argument('--regions',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--verify',type=Path);a=p.parse_args()
    print(json.dumps(verify(a.verify,a.source)if a.verify else build(a.source,a.page,json.loads(a.regions.read_text()),a.output),indent=2))
