"""Independently bound grouping strokes remain relationship candidates.

Only two open, monotone bracket shapes are supported. The coverage mask is a
fresh PDFium render of their exact source object, never its bounding interior.
"""
import ctypes
import hashlib
import json
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def box(obj):
    return [float(obj[k]) for k in ('x0', 'top', 'x1', 'bottom')]


def shape(path):
    """Closed, looping, horizontal and arbitrary drawings stay unsupported."""
    kinds = [p[0] for p in path]
    if kinds not in (['m', 'c', 'l', 'c'], ['m', 'c', 'l', 'c', 'c', 'l', 'c']):
        return False
    if any(len(p) != (4 if p[0] == 'c' else 2) for p in path):
        return False
    points = [q for p in path for q in p[1:]]
    if any(len(q) != 2 or any(not math.isfinite(v) for v in q) for q in points):
        return False
    xs, ys = [q[0] for q in points], [q[1] for q in points]
    width, height = max(xs)-min(xs), max(ys)-min(ys)
    if not 2 <= width <= 18 or not max(12, 2*width) <= height <= 96:
        return False
    if any(b < a-.02 for a, b in zip(ys, ys[1:])):
        return False
    if abs(points[0][0]-points[-1][0]) > .02:
        return False
    # All straight legs are vertical; rounded ends join them without a loop.
    previous = path[0][-1]
    for operation in path[1:]:
        if operation[0] == 'l' and abs(previous[0]-operation[-1][0]) > .02:
            return False
        previous = operation[-1]
    if kinds == ['m', 'c', 'l', 'c']:
        return abs(path[1][-1][0]-path[2][-1][0]) <= .02 and abs(path[0][-1][0]-path[1][-1][0]) >= width-.02
    center = path[3][-1]
    return (abs(path[1][-1][0]-path[2][-1][0]) <= .02 and
            abs(path[4][-1][0]-path[5][-1][0]) <= .02 and
            abs(center[0]-points[0][0]) >= width-.02)


def _segments(obj, native):
    import pypdfium2 as pdfium
    matrix = obj.get_matrix()
    values = list(matrix.get())
    if any(not math.isfinite(v) for v in values) or abs(matrix.a*matrix.d-matrix.b*matrix.c) < 1e-12:
        raise ValueError('Grouping source matrix is singular or nonfinite')
    points, local, kinds, closes = [], [], [], []
    for i in range(pdfium.raw.FPDFPath_CountSegments(obj.raw)):
        segment = pdfium.raw.FPDFPath_GetPathSegment(obj.raw, i)
        x, y = ctypes.c_float(), ctypes.c_float()
        if not pdfium.raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
            raise ValueError('Independent grouping path point unavailable')
        px, py = matrix.on_point(x.value, y.value)
        points.append([float(px), native.height-float(py)])
        local.append([float(x.value), float(y.value)])
        kinds.append(pdfium.raw.FPDFPathSegment_GetType(segment))
        closes.append(bool(pdfium.raw.FPDFPathSegment_GetClose(segment)))
    return dict(points=points, local_points=local, kinds=kinds, closes=closes, matrix=values)


def _wanted(path):
    return [(2 if operation[0] == 'm' else 1 if operation[0] == 'c' else 0, list(point))
            for operation in path for point in operation[1:]]


def _near(a, b):
    return len(a) == len(b) and all(abs(x-y) <= .02 for x, y in zip(a, b))


def derive_grouping_mask(native, independent, source, ink, image_bbox, *, dpi=120):
    """Bind every supported native subpath to a unique complete source object."""
    import numpy as np
    import pypdfium2 as pdfium
    mask = np.zeros_like(ink)
    curves = list(enumerate(native.curves))
    supported = [(i, c) for i, c in curves if shape(c.get('path', []))]
    if not supported:
        return mask, []
    source_bytes = Path(source).read_bytes()
    original_input = independent.pdf._input
    independent_bytes = original_input if isinstance(original_input, bytes) else Path(original_input).read_bytes()
    if sha(independent_bytes) != sha(source_bytes):
        raise ValueError('Grouping independent source identity differs')
    # Reopen the declared physical page; compare the supplied page's complete
    # object geometry and text, not just a matching bracket from another page.
    def page_identity(page):
        textpage = page.get_textpage()
        try:
            text = textpage.get_text_range()
        finally:
            textpage.close()
        return [text, [[o.type, list(o.get_bounds()), list(o.get_matrix().get()),
                       _segments(o, native) if o.type == pdfium.raw.FPDF_PAGEOBJ_PATH else None]
                      for o in page.get_objects(max_depth=0)]]
    with pdfium.PdfDocument(source_bytes) as exact:
        if page_identity(independent) != page_identity(exact[native.page_number-1]):
            raise ValueError('Grouping independent physical page differs')
    if native.rotation or not _near(image_bbox, [0, 0, native.width, native.height]):
        raise ValueError('Grouping proof requires an unrotated complete physical page')
    if not _near(independent.get_size(), [native.width, native.height]):
        raise ValueError('Grouping independent page dimensions differ')
    candidates = []
    for index, obj in enumerate(independent.get_objects(max_depth=0)):
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
            continue
        captured = _segments(obj, native)
        starts = [j for j, kind in enumerate(captured['kinds']) if kind == 2]
        if not starts or starts[0] != 0 or any(captured['closes']):
            continue
        matches = []
        for begin, end in zip(starts, starts[1:]+[len(captured['points'])]):
            segment = list(zip(captured['kinds'][begin:end], captured['points'][begin:end]))
            found = [(i, c) for i, c in supported if len(_wanted(c['path'])) == len(segment) and
                     all(k == kind and _near(p, point) for (k, p), (kind, point) in zip(_wanted(c['path']), segment))]
            if len(found) != 1:
                break
            matches.append(found[0])
        else:
            candidates.append((index, obj, captured, matches))
    proofs = []
    for curve_index, curve in supported:
        matches = [item for item in candidates if curve_index in [i for i, _ in item[3]]]
        if len(matches) != 1:
            raise ValueError('Grouping curve lacks unique independent path/subpath proof')
    consumed = set()
    for object_index, obj, captured, matched in candidates:
        indexes = [i for i, _ in matched]
        if consumed.intersection(indexes) or len(set(indexes)) != len(indexes):
            raise ValueError('Grouping path ownership is ambiguous')
        mode, stroke = ctypes.c_int(), ctypes.c_int()
        rgba = [ctypes.c_uint() for _ in range(4)]
        width = ctypes.c_float()
        if (not pdfium.raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(mode), ctypes.byref(stroke)) or
                mode.value or not stroke.value or
                not pdfium.raw.FPDFPageObj_GetStrokeColor(obj.raw, *[ctypes.byref(v) for v in rgba]) or
                [v.value for v in rgba] != [0, 0, 0, 255] or
                not pdfium.raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(width)) or
                pdfium.raw.FPDFPageObj_GetDashCount(obj.raw) != 0):
            raise ValueError('Grouping paint must be opaque black solid stroke only')
        # Restrict affine scaling to a similarity: a sheared stroke needs a new proof.
        a, b, c, d, _, _ = captured['matrix']
        scale = math.hypot(a, b)
        if abs(a*c+b*d) > 1e-7 or abs(scale-math.hypot(c, d)) > 1e-7:
            raise ValueError('Unsupported grouping stroke transform')
        source_width = float(width.value)*scale
        if not .2 <= source_width <= 1.2:
            raise ValueError('Unsupported grouping stroke width')
        associations = []
        for i, curve in matched:
            dash = curve.get('dash')
            if (curve.get('stroke') is not True or curve.get('fill') is not False or
                    dash not in (None, ([], 0), [[], 0]) or
                    abs(float(curve['linewidth'])-source_width) > .02 or
                    curve.get('stroking_color') not in ((0, 0, 0), [0, 0, 0], 0)):
                raise ValueError('Native grouping paint differs from independent path')
            region = box(curve)
            points=[point for operation in curve['path']for point in operation[1:]]
            if not _near(region,[min(p[0]for p in points),min(p[1]for p in points),
                                 max(p[0]for p in points),max(p[1]for p in points)]):
                raise ValueError('Native grouping bounds differ from exact source path')
            if any(region[0]-source_width < g['x1'] and region[2]+source_width > g['x0'] and
                   region[1]-source_width < g['bottom'] and region[3]+source_width > g['top']
                   for g in native.chars if g.get('text', '').strip()):
                raise ValueError('Grouping curve overlaps source glyphs')
            adjacent = [dict(text=g['text'], bbox=box(g)) for g in native.chars if g.get('text', '').strip()
                        and region[1]-3 <= (g['top']+g['bottom'])/2 <= region[3]+3
                        and (region[0]-96 <= g['x1'] <= region[0] or region[2] <= g['x0'] <= region[2]+96)]
            levels = {round(g['bbox'][1]/3) for g in adjacent}
            if len(levels) < 2:
                raise ValueError('Grouping curve lacks adjacent source text rows')
            associations.append(dict(curve_index=i, source_bbox=region, source_path=json.loads(canonical(curve['path'])),
                                     source_object_sha256=sha(canonical(curve)), adjacent_glyphs=adjacent,
                                     relationship='source_grouping_geometry_candidate', semantic_ownership_approved=False))
        # A second in-memory document retains this complete exact object only.
        # All subpaths were matched, so no foreign stroke can gain ownership.
        with pdfium.PdfDocument(source) as isolated:
            page = isolated[native.page_number-1]
            objects = list(page.get_objects(max_depth=0))
            selected = objects[object_index]
            if _segments(selected, native) != captured:
                raise ValueError('Grouping source/page changed during raster ownership proof')
            for i, other in enumerate(objects):
                if i != object_index:
                    page.remove_obj(other)
                    other.close()
            page.gen_content()
            bitmap = page.render(scale=dpi/72, may_draw_forms=False, prefer_bgrx=True,
                                 no_smoothtext=True, no_smoothpath=True, no_smoothimage=True)
            try:
                pixels = np.asarray(bitmap.to_pil().convert('L'))
                dark = pixels < 100
            finally:
                bitmap.close()
        if dark.shape != ink.shape or not dark.any() or (dark & ~ink).any():
            raise ValueError('Independent grouping paint differs from actual source raster')
        owned=dark
        mask |= owned
        consumed.update(indexes)
        proofs.append(dict(kind='source_grouping_paths.v1', source_pdf_sha256=sha(source_bytes),
                           physical_page=native.page_number, native_curve_indexes=indexes,
                           source_subpaths=associations, independent_object_index=object_index,
                           independent_path=captured, stroke_RGBA=[v.value for v in rgba],
                           stroke_width=source_width, fill_mode=mode.value,
                           stroke_line_cap=pdfium.raw.FPDFPageObj_GetLineCap(obj.raw),
                           stroke_line_join=pdfium.raw.FPDFPageObj_GetLineJoin(obj.raw),
                           raster_mask_sha256=sha(owned.tobytes()), raster_ink_pixels=int(owned.sum()),
                           raster_dpi=dpi, raster_antialias=False, bounding_interior_authorized=False, semantic_ownership_approved=False))
    return mask, proofs


def prove_grouped_row(line, native, independent, textpage, layout, grouping_paths):
    """Partition a printed grouping row, retaining both blocks without meaning."""
    import re
    from pdfplumber.utils import extract_text
    from .native_table_cell_spacing import read_source_box
    glyphs=sorted((g for g in line['chars']if g['text'].strip()),key=lambda g:g['x0'])
    gaps=[(a['x1'],b['x0'])for a,b in zip(glyphs,glyphs[1:])if b['x0']-a['x1']>max(36,4*max(a.get('size',0),b.get('size',0)))]
    if len(gaps)!=1:raise ValueError('Grouping row needs one exact source block gap')
    left,right=gaps[0];cy=(line['top']+line['bottom'])/2
    matches=[(p,s)for p in grouping_paths for s in p['source_subpaths']
             if left<s['source_bbox'][0]<s['source_bbox'][2]<right and s['source_bbox'][1]-3<=cy<=s['source_bbox'][3]+3]
    if len(matches)!=1:raise ValueError('Grouping row lacks unique source-path ownership')
    split=(left+right)/2
    groups=[[g for g in line['chars']if(g['x0']+g['x1'])/2<split],
            [g for g in line['chars']if(g['x0']+g['x1'])/2>=split]]
    texts=[extract_text(g,layout=False)for g in groups]
    compact=lambda text:''.join(c for c in text if not c.isspace())
    if compact(''.join(texts))!=compact(line['text']):raise ValueError('Grouping row changes source glyphs')
    layout_rows=[raw for raw in layout.splitlines()if [compact(v)for v in re.split(r' {3,}',raw.strip())if v]==[compact(v)for v in texts]]
    if not layout_rows:raise ValueError('Independent layout lacks exact grouped source blocks')
    blocks=[]
    for group,text in zip(groups,texts):
        b=[min(g['x0']for g in group),min(g['top']for g in group),max(g['x1']for g in group),max(g['bottom']for g in group)]
        raw,frame=read_source_box(native,independent,textpage,[b[0]-.2,b[1]-.2,b[2]+.2,b[3]+.2])
        if compact(raw)!=compact(text):raise ValueError('Independent source grouping block differs')
        blocks.append({'bbox':b,'selected_text':text,'independent_text':raw,'source_rectangle':frame})
    return {'kind':'source_grouped_text_row.v1','blocks':blocks,
            'source_grouping_object_sha256':sha(canonical(matches[0][0])),
            'source_grouping_curve_index':matches[0][1]['curve_index'],
            'semantic_ownership_approved':False,'source_quality_cleared':False}
