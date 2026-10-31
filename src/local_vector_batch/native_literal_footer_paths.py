"""Fresh geometry and raster proof for literal thin footer diamonds.

This does not mask a bounding box or classify arbitrary drawings as decoration.
The exact path and pixels remain evidence. Only pixels intersecting the proved
polygon are accounted; images and curved Beziers still fail.
"""
import ctypes
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
import re


def rules():
    return json.loads(Path(__file__).with_name('native_literal_footer_paths_rules.json').read_text())


def footer_page_number_layout(line, page_width, page_height, page_number, table_boxes, source_rects=()):
    """Recognize a layout candidate; this grants NO independent text/ink gate."""
    policy=rules();cfg=policy['printed_page_number_footer'];box=_box(line)
    physical=(box[1]>=page_height*cfg['minimum_top_fraction'] and
            box[2]>=page_width*cfg['minimum_right_fraction'] and
            all(box[1]>table[3]for table in table_boxes)and
            re.search(r'\s'+str(page_number)+r'\s*$',line['text'])is not None)
    framed=policy['framed_printed_page_footer'];printed=re.search(framed['printed_terminal_page_pattern'],line['text'])
    frames=[]
    if printed:
        for rect in source_rects:
            b=_box(rect)
            if (b[1]>=page_height*framed['minimum_top_fraction'] and
                0<b[3]-b[1]<=framed['maximum_frame_height_points'] and
                b[2]-b[0]>=page_width*framed['minimum_frame_width_fraction'] and
                rect.get('stroke')is True and rect.get('fill')is False and
                0<float(rect.get('linewidth',0))<=framed['maximum_stroke_width_points'] and
                b[0]<=box[0]<=box[2]<=b[2] and b[1]<=box[1]<=box[3]<=b[3] and
                all(box[1]>table[3]for table in table_boxes)):
                frames.append(b)
    if not physical and len(frames)!=1:return None
    return {'kind':'source_printed_page_footer_layout.v2',
        'source_bbox':box,'physical_page_number':page_number,
        'printed_page_label':printed.group(1)if printed else str(page_number),
        'printed_label_equals_physical_page':bool(printed and printed.group(1)==str(page_number)),
        'source_frame_bbox':frames[0]if len(frames)==1 else None,
        'rule_sha256':_sha(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()),
        'independent_text_numbers_operators_proved_by_this_candidate':False}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _overlap(a,b):
    return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])


def _box(obj):
    return [float(obj[k])for k in ('x0','top','x1','bottom')]


def prove_literal_footer_paths(native, independent, policy):
    """Accept only independently identical non-text diamonds."""
    import numpy as np
    import pypdfium2 as pdfium
    if native.images:
        raise ValueError('Images/curved source objects need separate source proof')
    policy_document=rules();cfg=policy_document['literal_diamond']
    tolerance=cfg['independent_coordinate_tolerance_points']
    result=[]
    for index,path in enumerate(native.curves):
        box=_box(path);x0,y0,x1,y1=box
        if x0==x1 and y0==y1:
            from .native_zero_length_path_components import prove_rectangle_corner_point
            result.append(prove_rectangle_corner_point(native,independent,path))
            continue
        if (any(not math.isfinite(v)for v in box)or not 0<x1-x0<=native.width*cfg['maximum_width_fraction'] or
            not 0<y1-y0<=cfg['maximum_height_points'] or y0<native.height*cfg['minimum_top_fraction'] or
            path.get('fill')is not True or path.get('stroke')is not False):
            raise ValueError('Unsupported non-ink footer path geometry or paint')
        operations=path.get('path',[])
        if [s[0]for s in operations]!=['m','l','l','l','h']:
            raise ValueError('Only closed straight four-vertex footer diamonds supported')
        points=[list(s[1])for s in operations[:4]]
        expected=[[x0,(y0+y1)/2],[(x0+x1)/2,y0],[x1,(y0+y1)/2],[(x0+x1)/2,y1]]
        if any(abs(a-b)>tolerance for p,q in zip(points,expected)for a,b in zip(p,q)):
            raise ValueError('Footer path is not an exact thin diamond')
        others=[*(g for g in native.chars if g['text'].strip()),*native.rects,*native.lines,*native.images,
                *(p for i,p in enumerate(native.curves)if i!=index)]
        if any(_overlap(box,_box(obj))for obj in others):
            raise ValueError('Literal footer path overlaps another original object')
        matches=[]
        for obj in independent.get_objects():
            if obj.type!=pdfium.raw.FPDF_PAGEOBJ_PATH:continue
            bounds=list(obj.get_bounds());expected_bounds=[x0,native.height-y1,x1,native.height-y0]
            if any(abs(a-b)>tolerance for a,b in zip(bounds,expected_bounds)):continue
            if pdfium.raw.FPDFPath_CountSegments(obj.raw)!=5:continue
            captured=[];kinds=[];closes=[]
            for j in range(5):
                segment=pdfium.raw.FPDFPath_GetPathSegment(obj.raw,j)
                x=ctypes.c_float();y=ctypes.c_float()
                if not pdfium.raw.FPDFPathSegment_GetPoint(segment,ctypes.byref(x),ctypes.byref(y)):
                    raise ValueError('Independent footer path point unavailable')
                captured.append([x.value,native.height-y.value]);kinds.append(pdfium.raw.FPDFPathSegment_GetType(segment));closes.append(bool(pdfium.raw.FPDFPathSegment_GetClose(segment)))
            if kinds!=[2,0,0,0,0]or closes!=[False,False,False,False,True]:continue
            if any(abs(a-b)>tolerance for p,q in zip(captured,points+[points[0]])for a,b in zip(p,q)):continue
            fill=ctypes.c_int();stroke=ctypes.c_int();colors=[ctypes.c_uint()for _ in range(4)]
            if not pdfium.raw.FPDFPath_GetDrawMode(obj.raw,ctypes.byref(fill),ctypes.byref(stroke))or fill.value not in (1,2)or stroke.value:
                continue
            if not pdfium.raw.FPDFPageObj_GetFillColor(obj.raw,*[ctypes.byref(v)for v in colors]):continue
            rgba=[v.value for v in colors]
            if rgba[3]!=255 or len(set(rgba[:3]))!=1 :continue
            matches.append({'bounds':bounds,'points':captured,'fill_RGBA':rgba,'fill_mode':fill.value,'stroke':stroke.value})
        if len(matches)!=1:
            raise ValueError('Footer diamond lacks unique independent path/color/opacity proof')
        image=native.to_image(resolution=cfg['audit_dpi']).original.convert('L');pixels=np.asarray(image)
        sx=image.width/native.width;sy=image.height/native.height
        padding=cfg['audit_crop_padding_pixels']
        a=max(0,math.floor(x0*sx)-padding);b=max(0,math.floor(y0*sy)-padding)
        c=min(image.width,math.ceil(x1*sx)+padding);d=min(image.height,math.ceil(y1*sy)+padding)
        crop=pixels[b:d,a:c]
        if not crop.size:
            raise ValueError('Empty literal footer path source crop')
        stream=BytesIO();image.save(stream,format='PNG')
        result.append({'kind':'independent_literal_footer_diamond.v1','curve_index':index,
            'source_bbox':box,'original_path':json.loads(json.dumps(operations)),'independent_path':matches[0],
            'original_path_sha256':_sha(json.dumps(path,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()),
            'rule_sha256':_sha(json.dumps(policy_document,sort_keys=True,separators=(',',':')).encode()),
            'render_dpi':cfg['audit_dpi'],'source_render_sha256':_sha(stream.getvalue()),
            'source_crop_pixels':[a,b,c,d],'source_crop_sha256':_sha(crop.tobytes()),
            'source_crop_minimum_luminance':int(crop.min()),'source_dark_pixel_threshold':policy['source_dark_pixel_threshold'],
            'source_crop_dark_ink_pixels':int((crop<policy['source_dark_pixel_threshold']).sum()),'source_text_modified':False})
    return result


def literal_footer_mask(native, image_bbox, shape, proofs):
    """Pixels whose closed unit square intersects the exact convex diamond.

    On each raster row, extrema of the piecewise-linear diamond occur only at
    the row boundaries or the middle vertex. No bounding interior mask exists.
    """
    import numpy as np
    height,width=shape;ox,oy,right,bottom=image_bbox
    sx=width/(right-ox);sy=height/(bottom-oy)
    mask=np.zeros(shape,dtype=bool)
    for proof in proofs:
        if proof['kind']=='independent_closed_zero_length_retained_rectangle_corner.v1':continue
        x0,y0,x1,y1=proof['source_bbox'];cx=(x0+x1)/2;cy=(y0+y1)/2
        for row in range(max(0,math.floor((y0-oy)*sy)),min(height,math.floor((y1-oy)*sy)+1)):
            lo=max(y0,oy+row/sy);hi=min(y1,oy+(row+1)/sy)
            if lo>hi:continue
            ys=[lo,hi]+([cy]if lo<=cy<=hi else[])
            radii=[(x1-x0)/2*(1-abs(y-cy)/((y1-y0)/2))for y in ys]
            radius=max(radii);left=(cx-radius-ox)*sx;right=(cx+radius-ox)*sx
            # Include each pixel with a nonempty geometric intersection.
            start=max(0,math.ceil(left)-1);end=min(width,math.floor(right)+1)
            mask[row,start:end]=True
    return mask
