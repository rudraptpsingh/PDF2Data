"""Geometry observations, never inferred image text or independent tables.

Native PDF text can coexist with substantial raster-only evidence. Preserve
those regions and hold their completeness. Nested table candidates are kept
verbatim and related geometrically, rather than counted as independent grids.
"""
from __future__ import annotations
import math
import hashlib
from pathlib import Path

NATIVE_VISUALS_IMPLEMENTATION_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        return None
    x0, y0, x1, y1 = value
    return list(value) if x1 > x0 and y1 > y0 else None


def native_raster_regions(page, *, minimum_page_fraction=0.0):
    """Locate exposed raster objects; the original PDF remains authoritative.

    No image is classified as decorative from its size. The configurable area
    threshold defaults to zero; a missing geometry observation remains held.
    A form/XObject not exposed by the native parser still requires the separate
    source-raster quality gate, so an empty list is not a visual recovery proof.
    """
    if type(minimum_page_fraction) not in (int, float) or not 0 <= minimum_page_fraction <= 1:
        raise ValueError('Invalid native image area policy')
    regions=[]
    area=float(getattr(page,'width',0) or 0)*float(getattr(page,'height',0) or 0)
    for index, image in enumerate(getattr(page,'images',[]) or []):
        bbox=_box([image.get(k) for k in ('x0','top','x1','bottom')]) if isinstance(image,dict) else None
        fraction=(bbox[2]-bbox[0])*(bbox[3]-bbox[1])/area if bbox and area>0 else None
        if fraction is not None and fraction < minimum_page_fraction:
            continue
        regions.append(dict(region_index=index,bbox=bbox,bbox_coordinates='PDF points, top-left',
            image_object_name=image.get('name') if isinstance(image,dict) else None,
            page_area_fraction=fraction,status='raster_content_unverified',
            text_recovery_verified=False,requires_source_raster_review=True))
    return regions


def associate_table_geometry(tables, *, tolerance=0.25):
    """Annotate candidates in place without modifying cells or dropping rows.

    Containment is geometric evidence only: a nested table may be meaningful.
    It needs review rather than an automatic duplicate/independence decision.
    """
    if type(tolerance) not in (int,float) or not 0 <= tolerance <= 2:
        raise ValueError('Invalid table containment tolerance')
    boxes={t['id']:_box(t.get('bbox')) for t in tables}
    def area(b):return (b[2]-b[0])*(b[3]-b[1])
    def contains(a,b):return all((a[0]<=b[0]+tolerance,a[1]<=b[1]+tolerance,a[2]>=b[2]-tolerance,a[3]>=b[3]-tolerance))
    nested=duplicates=0
    for table in tables:
        box=boxes[table['id']];parents=[];same=[]
        if box:
            for other in tables:
                candidate=boxes[other['id']]
                if other['id']==table['id'] or other.get('page')!=table.get('page') or not candidate:
                    continue
                if contains(candidate,box) and contains(box,candidate):same.append(other['id'])
                elif contains(candidate,box) and area(candidate)>area(box):parents.append(other['id'])
        parents.sort(key=lambda id:(area(boxes[id]),id))
        relationship='unresolved_geometry' if box is None else 'contained_geometry_candidate' if parents else 'maximal_geometry_candidate'
        nested+=bool(parents);duplicates+=bool(same)
        table['geometry_relationships']=dict(classification=relationship,contained_by_candidates=parents,
            nearest_container_candidate=parents[0] if parents else None,equal_geometry_candidates=sorted(same),
            independence_review='required',semantic_table_count_proven=False)
    return dict(raw_candidates=len(tables),contained_candidates=nested,equal_geometry_candidates=duplicates,
        maximal_geometry_candidates=sum(t['geometry_relationships']['classification']=='maximal_geometry_candidate' for t in tables),
        semantic_table_count=None)
