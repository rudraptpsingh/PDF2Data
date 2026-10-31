"""Account only actual independently bound thin path pixels, never interiors.

Long source-native separators and frame sides keep an unresolved semantic role.
Small rectangular corner tiles qualify only by exact adjacency to such a side.
No caller-provided box or mask is an authority for source coverage.
"""
import ctypes
import hashlib
import json
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()


def box(obj):
    return [float(obj[k])for k in ('x0','top','x1','bottom')]


def overlap(a,b,pad=0):
    return a[0]-pad<b[2]and a[2]+pad>b[0]and a[1]-pad<b[3]and a[3]+pad>b[1]


def _rectangle_path(obj,tolerance):
    b=box(obj);expected={(b[0],b[1]),(b[2],b[1]),(b[2],b[3]),(b[0],b[3])}
    path=obj.get('path',[])
    if len(path)!=5 or [p[0]for p in path]!=['m','l','l','l','h']:return False
    points=[p[1]for p in path[:4]]
    return all(any(abs(x-a)<=tolerance and abs(y-c)<=tolerance for a,c in expected)for x,y in points)and len(set(map(tuple,points)))==4


def _independent_path(independent,native,obj,kind,tolerance):
    import pypdfium2 as pdfium
    b=box(obj);expected=[b[0],native.height-b[3],b[2],native.height-b[1]]
    matches=[]
    for candidate in independent.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH]):
        # Stroke bounds include its half-width; source segment coordinates below
        # supply the exact geometry rather than trusting that bounding interior.
        width=float(obj.get('linewidth',0))if kind=='line'else 0
        bounds=candidate.get_bounds()
        if all(abs(a-c)<=tolerance+width for a,c in zip(bounds,expected)):matches.append(candidate)
    if not matches:return None
    if len(matches)>1:
        # Repeated identical opaque paint is idempotent, not an ambiguous
        # alternative reading. Preserve and bind the complete path multiplicity.
        def fingerprint(candidate):
            mode=ctypes.c_int();stroke=ctypes.c_int();pdfium.raw.FPDFPath_GetDrawMode(candidate.raw,ctypes.byref(mode),ctypes.byref(stroke))
            colors=[]
            for fn in(pdfium.raw.FPDFPageObj_GetFillColor,pdfium.raw.FPDFPageObj_GetStrokeColor):
                color=[ctypes.c_uint()for _ in range(4)]
                if not fn(candidate.raw,*[ctypes.byref(v)for v in color]):return None
                colors.append([v.value for v in color])
            points=[]
            for i in range(pdfium.raw.FPDFPath_CountSegments(candidate.raw)):
                segment=pdfium.raw.FPDFPath_GetPathSegment(candidate.raw,i);x=ctypes.c_float();y=ctypes.c_float()
                if not pdfium.raw.FPDFPathSegment_GetPoint(segment,ctypes.byref(x),ctypes.byref(y)):return None
                points.append([pdfium.raw.FPDFPathSegment_GetType(segment),x.value,y.value,bool(pdfium.raw.FPDFPathSegment_GetClose(segment))])
            return canonical([list(candidate.get_bounds()),list(candidate.get_matrix().get()),mode.value,stroke.value,colors,points])
        signatures=[fingerprint(candidate)for candidate in matches]
        if None in signatures or len(set(signatures))!=1:return None
    candidate=matches[0];matrix=candidate.get_matrix();matrix_values=list(matrix.get())
    if any(not math.isfinite(v)for v in matrix_values)or abs(matrix.a*matrix.d-matrix.b*matrix.c)<1e-12:return None
    if kind=='line'and any(abs(a-b)>1e-9 for a,b in zip(matrix_values[:4],[1,0,0,1])):return None
    mode=ctypes.c_int();stroke=ctypes.c_int()
    if not pdfium.raw.FPDFPath_GetDrawMode(candidate.raw,ctypes.byref(mode),ctypes.byref(stroke)):return None
    if (kind=='rect'and(not mode.value or stroke.value))or(kind=='line'and(mode.value or not stroke.value)):return None
    color=[ctypes.c_uint()for _ in range(4)]
    get_color=pdfium.raw.FPDFPageObj_GetFillColor if kind=='rect'else pdfium.raw.FPDFPageObj_GetStrokeColor
    if not get_color(candidate.raw,*[ctypes.byref(v)for v in color])or color[3].value!=255:return None
    native_color=obj.get('non_stroking_color'if kind=='rect'else'stroking_color')
    native_color=[native_color]*3 if isinstance(native_color,(float,int))else native_color
    if not native_color or len(native_color)!=3 or any(abs(a.value-round(float(c)*255))>1 for a,c in zip(color,native_color)):return None
    if kind=='line':
        width=ctypes.c_float()
        if not pdfium.raw.FPDFPageObj_GetStrokeWidth(candidate.raw,ctypes.byref(width))or abs(width.value-float(obj['linewidth']))>tolerance:return None
    n=pdfium.raw.FPDFPath_CountSegments(candidate.raw);points=[];local_points=[]
    if n!=(5 if kind=='rect'else 2):return None
    for i in range(n):
        segment=pdfium.raw.FPDFPath_GetPathSegment(candidate.raw,i);x=ctypes.c_float();y=ctypes.c_float()
        if not pdfium.raw.FPDFPathSegment_GetPoint(segment,ctypes.byref(x),ctypes.byref(y)):return None
        if pdfium.raw.FPDFPathSegment_GetType(segment)not in(0,2):return None # line or move only
        local_points.append([float(x.value),float(y.value)]);px,py=matrix.on_point(x.value,y.value)
        points.append([float(px),native.height-float(py)])
    wanted=[p[1]for p in obj.get('path',[])if len(p)>1]
    if kind=='rect':wanted.append(wanted[0])
    if len(wanted)!=len(points)or any(abs(x-a)>tolerance or abs(y-c)>tolerance for (x,y),(a,c)in zip(points,wanted)):return None
    return {'matching_equivalent_independent_paths':len(matches),'PDFium_bounds':list(candidate.get_bounds()),'PDFium_path_points':points,'PDFium_local_path_points':local_points,'PDFium_object_matrix':matrix_values,'coordinate_transform_basis':'actual_PDFium_object_matrix_then_native_top_origin','RGB':[v.value for v in color[:3]],'alpha':color[3].value,'fill_mode':mode.value,'stroke':bool(stroke.value)}


def _closed_page_frames(native,independent,rules):
    """Require eight literal source parts; broad rectangles cannot claim frames."""
    import itertools
    maximum=rules.get('maximum_closed_frame_thickness_points',1)
    fraction=rules.get('minimum_closed_frame_span_fraction',1)
    margin=rules.get('maximum_closed_frame_margin_fraction',0)
    tolerance=rules['maximum_coordinate_error_points'];rects=list(enumerate(native.rects));horizontal=[];vertical=[]
    for index,obj in rects:
        b=box(obj);width,height=b[2]-b[0],b[3]-b[1]
        if obj.get('stroke')or obj.get('fill')is not True or not _rectangle_path(obj,tolerance):continue
        if 0<height<=maximum and width>=native.width*fraction:horizontal.append((index,obj,b))
        if 0<width<=maximum and height>=native.height*fraction:vertical.append((index,obj,b))
    accepted={};frames=[];seen=set()
    for pair_x in itertools.combinations(vertical,2):
      left,right=sorted(pair_x,key=lambda item:item[2][0]);l,r=left[2],right[2]
      if l[0]>native.width*margin or r[2]<native.width*(1-margin):continue
      for pair_y in itertools.combinations(horizontal,2):
        top,bottom=sorted(pair_y,key=lambda item:item[2][1]);t,b=top[2],bottom[2]
        if t[1]>native.height*margin or b[3]<native.height*(1-margin):continue
        expected=[(t[0],l[2]),(t[2],r[0]),(b[0],l[2]),(b[2],r[0]),(l[1],t[3]),(r[1],t[3]),(l[3],b[1]),(r[3],b[1])]
        if any(abs(a-c)>tolerance for a,c in expected):continue
        parts=[left,right,top,bottom]
        for target in([l[0],t[1],l[2],t[3]],[r[0],t[1],r[2],t[3]],[l[0],b[1],l[2],b[3]],[r[0],b[1],r[2],b[3]]):
            matches=[(i,o,box(o))for i,o in rects if all(abs(a-c)<=tolerance for a,c in zip(box(o),target))]
            if not matches:break
            parts.extend(matches)
        if len(parts)<8 or len({i for i,_,_ in parts})!=len(parts)or len({tuple(b)for _,_,b in parts})!=8:continue
        identity=tuple(sorted(i for i,_,_ in parts))
        if identity in seen:continue
        seen.add(identity)
        if len({canonical(o.get('non_stroking_color'))for _,o,_ in parts})!=1:continue
        if any(o.get('stroke')or o.get('fill')is not True or not _rectangle_path(o,tolerance)for _,o,_ in parts):continue
        if any(overlap(band,box(g),rules['glyph_clearance_points'])for _,_,band in parts for g in native.chars if g.get('text','').strip()):continue
        # A later white/vector word or any foreign paint over a side invalidates
        # the frame classification. Never mask the enclosing body/interior.
        foreign=[o for i,o in rects if i not in identity]+list(native.lines)+list(native.curves)+list(native.images)
        if any(overlap(band,box(o))for _,_,band in parts for o in foreign):continue
        proofs={i:_independent_path(independent,native,o,'rect',tolerance)for i,o,_ in parts}
        if any(proof is None for proof in proofs.values()):continue
        accepted.update(proofs);frames.append({'source_bbox':[l[0],t[1],r[2],b[3]],'source_rect_indexes':list(identity),
            'classification':'closed_literal_page_frame','semantic_role':'unresolved_literal_path',
            'geometric_frame_parts':8,'independent_opaque_source_parts':len(parts),'foreign_paint_overlap':False,'native_glyph_overlap':False,
            'interior_authorized':False})
    return accepted,frames


def _top_header_separators(native,independent,rules):
    """Wide opaque black source bars in the configured header band only."""
    policy=rules.get('top_header_black_separator')
    if not policy:return {},[]
    accepted={};observations=[];tolerance=rules['maximum_coordinate_error_points']
    for index,obj in enumerate(native.rects):
        b=box(obj);width,height=b[2]-b[0],b[3]-b[1]
        if obj.get('stroke')or obj.get('fill')is not True or not _rectangle_path(obj,tolerance):continue
        if not(0<height<=policy['maximum_thickness_points']and width>=native.width*policy['minimum_width_page_fraction']and 0<=b[1]<b[3]<=native.height*policy['maximum_bottom_page_fraction']):continue
        color=obj.get('non_stroking_color');color=[color]*3 if isinstance(color,(int,float))else color
        if not color or len(color)!=3 or [round(float(v)*255)for v in color]!=policy['required_RGB']:continue
        if any(overlap(b,box(g),rules['glyph_clearance_points']+height/2)for g in native.chars if g.get('text','').strip()):continue
        foreign=[other for i,other in enumerate(native.rects)if i!=index]+list(native.lines)+list(native.curves)+list(native.images)
        if any(overlap(b,box(other))for other in foreign):continue
        proof=_independent_path(independent,native,obj,'rect',tolerance)
        if proof is None or proof['RGB']!=policy['required_RGB']or proof['alpha']!=policy['required_alpha']:continue
        accepted[index]=proof;observations.append({'classification':'opaque_top_header_separator','source_rect_index':index,
            'source_bbox':b,'semantic_role':'unresolved_literal_path','native_glyph_overlap':False,
            'foreign_paint_overlap':False,'interior_authorized':False})
    return accepted,observations


def derive_literal_path_mask(native,independent,ink,image_bbox):
    import numpy as np
    rules_path=Path(__file__).with_name('literal_path_frames_rules.json');rules=json.loads(rules_path.read_text());tol=rules['maximum_coordinate_error_points']
    frame_parts,frames=_closed_page_frames(native,independent,rules)
    header_parts,headers=_top_header_separators(native,independent,rules)
    candidates=[];long=[];glyphs=[box(c)for c in native.chars if c.get('text','').strip()]
    for kind,objects in [('rect',native.rects),('line',native.lines)]:
        for index,obj in enumerate(objects):
            b=box(obj);dx,dy=b[2]-b[0],b[3]-b[1]
            dash=obj.get('dash')
            solid_dash=dash is None or (isinstance(dash,(list,tuple))and len(dash)==2 and dash[0]==[]and dash[1]==0)
            if not all(math.isfinite(v)for v in b)or dx<0 or dy<0 or not solid_dash:continue
            if kind=='rect':
                if obj.get('stroke')or obj.get('fill')is not True or not _rectangle_path(obj,tol):continue
                thickness=min(dx,dy)
            else:
                if not obj.get('stroke')or obj.get('fill')or not(dx==0 or dy==0):continue
                thickness=float(obj.get('linewidth',0))
            maximum=(rules.get('maximum_closed_frame_thickness_points',rules['maximum_thickness_points'])if kind=='rect'and index in frame_parts else rules['top_header_black_separator']['maximum_thickness_points']if kind=='rect'and index in header_parts else rules['maximum_thickness_points'])
            wide=rules.get('wide_literal_body_separator',{})
            if kind=='rect'and dy>0 and dx>=native.width*wide.get('minimum_width_page_fraction',1):maximum=max(maximum,wide.get('maximum_thickness_points',0))
            if not 0<thickness<=maximum:continue
            clearance=rules['glyph_clearance_points']+(thickness/2 if kind=='line'else 0)
            overlapping=[g for g in glyphs if overlap(b,g,clearance)]
            baseline_header=(kind=='line'and dy==0 and b[1]<=native.height*rules['top_header_black_separator']['maximum_bottom_page_fraction']and dx>=native.width*rules['top_header_black_separator']['minimum_width_page_fraction']and overlapping and all(b[1]>=g[3]-rules.get('header_baseline_bottom_tolerance_points',0)for g in overlapping))
            if overlapping and not baseline_header:continue
            if kind=='line':
                half=thickness/2;band=[b[0]-half,b[1]-half,b[2]+half,b[3]+half]
            else:band=b
            item={'kind':kind,'index':index,'object':obj,'bbox':b,'band':band,'thickness':thickness,'closed_frame_part':kind=='rect'and index in frame_parts,'top_header_part':kind=='rect'and index in header_parts,'baseline_header_part':bool(baseline_header)}
            candidates.append(item)
            if max(dx,dy)>=max(rules.get('minimum_length_points',0),rules['minimum_length_page_fraction']*(native.width if dx>=dy else native.height)):long.append(item)
    selected=[]
    for item in candidates:
        b=item['bbox'];corner=max(b[2]-b[0],b[3]-b[1])<=rules['maximum_corner_side_points']
        adjacent=corner and any(overlap(b,o['bbox'],tol)for o in long)
        if item not in long and not adjacent and not item['closed_frame_part']:continue
        independent_proof=(frame_parts.get(item['index'])if item['closed_frame_part']else header_parts.get(item['index'])if item['top_header_part']else _independent_path(independent,native,item['object'],item['kind'],tol))
        if independent_proof is not None:selected.append((item,independent_proof))
    h,w=ink.shape;ox,oy,right,bottom=image_bbox;sx=w/(right-ox);sy=h/(bottom-oy);mask=np.zeros_like(ink);paths=[]
    for item,independent_proof in selected:
        b=item['band'];pad=rules['raster_rounding_padding_pixels'];x0=max(0,int((b[0]-ox)*sx)-pad);y0=max(0,int((b[1]-oy)*sy)-pad);x1=min(w,int((b[2]-ox)*sx)+pad+1);y1=min(h,int((b[3]-oy)*sy)+pad+1)
        # Only source ink inside a narrow actual bar. No enclosing frame bbox.
        current=np.zeros_like(ink);current[y0:y1,x0:x1]=ink[y0:y1,x0:x1];mask|=current
        paths.append({'classification':'source_header_rule_at_glyph_baseline'if item['baseline_header_part']else'opaque_top_header_separator'if item['top_header_part']else'literal_source_path_retained','semantic_role':rules['semantic_role'],'object_type':item['kind'],'object_index':item['index'],'source_bbox':item['bbox'],'literal_ink_band':b,'source_object_sha256':digest(canonical(item['object'])),'independent_path':independent_proof,'source_ink_pixels':int(current.sum()),'source_ink_mask_sha256':digest(current.tobytes()),'rectangle_interior_authorized':False})
    return mask,{'contract_version':rules['contract_version'],'rules_version':rules.get('rules_version',1),'closed_page_frames':frames,'top_header_separators':headers,'paths':paths,'mask_sha256':digest(mask.tobytes()),'source_ink_pixels':int(mask.sum()),'rules_sha256':digest(rules_path.read_bytes()),'engine_sha256':digest(Path(__file__).read_bytes())}
