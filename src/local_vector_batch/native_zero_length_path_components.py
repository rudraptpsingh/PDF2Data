"""Bind a closed zero-length subpath to its independently retained rectangle.

PDFMiner splits `rectangle ... stroke; moveto close` into a rect plus curve,
while PDFium retains a single path object. This rule adds ZERO ink coverage;
the ordinary complete-page proof must still account for every source pixel.
"""
import ctypes
import math


def prove_rectangle_corner_point(native, independent, curve):
    import pypdfium2 as pdfium
    box=[float(curve[k])for k in ('x0','top','x1','bottom')]
    if not all(math.isfinite(v)for v in box)or box[0]!=box[2]or box[1]!=box[3]:
        raise ValueError('Zero-length path component requires exact zero area')
    path=curve.get('path',[])
    if [s[0]for s in path]!=['m','h']or list(path[0][1])!=box[:2]or curve.get('fill')is not False or curve.get('stroke')is not True:
        raise ValueError('Unsupported zero-length source path component')
    width=float(curve.get('linewidth',0))
    if not 0<width<=1:raise ValueError('Zero-length rectangle component stroke width unsupported')
    candidates=[]
    for obj in independent.get_objects():
        if obj.type!=pdfium.raw.FPDF_PAGEOBJ_PATH or pdfium.raw.FPDFPath_CountSegments(obj.raw)!=6:continue
        matrix=obj.get_matrix();points=[];kinds=[];closes=[]
        for j in range(6):
            segment=pdfium.raw.FPDFPath_GetPathSegment(obj.raw,j);x=ctypes.c_float();y=ctypes.c_float()
            if not pdfium.raw.FPDFPathSegment_GetPoint(segment,ctypes.byref(x),ctypes.byref(y)):raise ValueError('Independent rectangle point unavailable')
            px,py=matrix.on_point(x.value,y.value);points.append([px,native.height-py]);kinds.append(pdfium.raw.FPDFPathSegment_GetType(segment));closes.append(bool(pdfium.raw.FPDFPathSegment_GetClose(segment)))
        if kinds!=[2,0,0,0,0,2]or closes!=[False,False,False,False,True,True]:continue
        if any(abs(a-b)>.001 for a,b in zip(points[-1],box[:2])):continue
        p=points[:5]
        if any(abs(a-b)>.001 for a,b in zip(p[0],p[-1])):continue
        xs=sorted({round(v[0],3)for v in p[:4]});ys=sorted({round(v[1],3)for v in p[:4]})
        if len(xs)!=2 or len(ys)!=2:continue
        if {(round(x,3),round(y,3))for x,y in p[:4]}!={(x,y)for x in xs for y in ys}:continue
        if any(abs(p[i][0]-p[i+1][0])>.001 and abs(p[i][1]-p[i+1][1])>.001 for i in range(4)):continue
        if not any(all(abs(a-b)<.001 for a,b in zip(point,points[-1]))for point in p[:4]):continue
        rectbox=[xs[0],ys[0],xs[1],ys[1]]
        rects=[r for r in native.rects if all(abs(float(r[k])-v)<.001 for k,v in zip(('x0','top','x1','bottom'),rectbox))and r.get('stroke')is True and r.get('fill')is False and abs(float(r.get('linewidth',0))-width)<.001]
        if len(rects)!=1:continue
        fill=ctypes.c_int();stroke=ctypes.c_int();observed_width=ctypes.c_float();colors=[ctypes.c_uint()for _ in range(4)]
        if not pdfium.raw.FPDFPath_GetDrawMode(obj.raw,ctypes.byref(fill),ctypes.byref(stroke))or fill.value!=0 or stroke.value!=1:continue
        if not pdfium.raw.FPDFPageObj_GetStrokeWidth(obj.raw,ctypes.byref(observed_width))or abs(observed_width.value-width)>.001:continue
        if not pdfium.raw.FPDFPageObj_GetStrokeColor(obj.raw,*[ctypes.byref(v)for v in colors])or colors[3].value!=255:continue
        native_color=rects[0].get('stroking_color')
        native_color=[native_color]*3 if isinstance(native_color,(float,int))else native_color
        if not native_color or len(native_color)!=3 or any(abs(round(float(v)*255)-c.value)>1 for v,c in zip(native_color,colors)):continue
        candidates.append({'rectangle_bbox':rectbox,'independent_transformed_path_points':points,
            'original_rectangle_linewidth':width,'independent_linewidth':observed_width.value,
            'independent_stroke_RGBA':[v.value for v in colors],
            'independent_path_matrix':list(matrix.get()),'independent_line_cap':pdfium.raw.FPDFPageObj_GetLineCap(obj.raw)})
    if len(candidates)!=1:raise ValueError('Zero-length point lacks unique independent retained rectangle/corner/paint proof')
    return {'kind':'independent_closed_zero_length_retained_rectangle_corner.v1','source_bbox':box,
        'original_path':[['m',box[:2]],['h']],**candidates[0],
        'ink_mask_pixels_added':0,'source_text_modified':False,'source_geometry_preserved':True}


def retained_footer_rectangle_mask(native, image_bbox, ink, proofs):
    """Only independently proved original footer perimeter, never its interior."""
    import json,hashlib
    from pathlib import Path
    import numpy as np
    cfg=json.loads(Path(__file__).with_name('native_literal_footer_paths_rules.json').read_text())['framed_printed_page_footer']
    height,width=ink.shape;ox,oy,right,bottom=image_bbox;sx=width/(right-ox);sy=height/(bottom-oy)
    mask=np.zeros_like(ink);paths=[]
    for proof in proofs:
        if proof['kind']!='independent_closed_zero_length_retained_rectangle_corner.v1':continue
        x0,y0,x1,y1=proof['rectangle_bbox'];stroke=proof['original_rectangle_linewidth'];half=stroke/2
        if not (y0>=native.height*cfg['minimum_top_fraction']and y1-y0<=cfg['maximum_frame_height_points']and x1-x0>=native.width*cfg['minimum_frame_width_fraction']):continue
        bands=[[x0-half,y0-half,x1+half,y0+half],[x0-half,y1-half,x1+half,y1+half],[x0-half,y0-half,x0+half,y1+half],[x1-half,y0-half,x1+half,y1+half]]
        def overlaps(a,b):return min(a[2],b[2])>max(a[0],b[0])and min(a[3],b[3])>max(a[1],b[1])
        glyphs=[[g[k]for k in ('x0','top','x1','bottom')]for g in native.chars if g['text'].strip()]
        if any(overlaps(b,g)for b in bands for g in glyphs):raise ValueError('Literal footer perimeter overlaps original source glyphs')
        current=np.zeros_like(ink)
        for a,b,c,d in bands:
            left=max(0,math.floor((a-ox)*sx)-1);top=max(0,math.floor((b-oy)*sy)-1)
            right=min(width,math.floor((c-ox)*sx)+2);bottom=min(height,math.floor((d-oy)*sy)+2)
            current[top:bottom,left:right]=ink[top:bottom,left:right]
        mask|=current;paths.append({'kind':'independent_retained_footer_rectangle_perimeter.v1','source_rectangle_bbox':proof['rectangle_bbox'],'source_linewidth':stroke,'exact_stroke_bands':bands,'source_ink_pixels':int(current.sum()),'source_ink_mask_sha256':hashlib.sha256(current.tobytes()).hexdigest(),'rectangle_interior_authorized':False,'source_text_modified':False})
    return mask,paths
