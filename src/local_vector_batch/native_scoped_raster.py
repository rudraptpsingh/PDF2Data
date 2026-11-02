"""Additive source-object/raster accounting for existing field witness crops.

This proves neither transcription nor language/layout, and never edits a page
quality ledger. Disjoint invalid glyphs remain explicit outside held evidence.
"""
from collections import Counter
from importlib.metadata import version
import math
import struct
from pathlib import Path

from . import field_witness as witness
from .hashing import file_sha256
from .native_tight_cell_ownership import contained, intersects, supported_contexts

SCHEMA='pdf2data.native-scoped-raster.v1'


def implementation():
    root=Path(__file__).parent
    return {n:file_sha256(root/n) for n in ('native_scoped_raster.py','field_witness.py',
        'native_paint_visibility.py','native_paint_visibility_rules.json',
        'native_tight_cell_ownership.py','quality_gate.py','quality_policy.json')}


def glyph_record(char):
    return dict(text=char.get('text',''),bbox=[float(char[k]) for k in ('x0','top','x1','bottom')],
                font=char.get('fontname'),size=float(char.get('size',0)))


def valid_box(box):
    return (isinstance(box,(list,tuple)) and len(box)==4 and
        all(type(v) in (int,float) and math.isfinite(v) for v in box) and box[0]<box[2] and box[1]<box[3])


def partition(glyphs,box):
    inside=[];outside_invalid=[]
    for i,g in enumerate(glyphs):
        b=g['bbox']
        witness.require(len(b)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in b),
                        'unlocalizable_native_glyph_geometry')
        # A finite zero-advance mark is localizable, but it may not mask ink.
        # Use its entire closed extent for exclusion; contact stays held.
        if not valid_box(b):
            extent=[min(b[0],b[2]),min(b[1],b[3]),max(b[0],b[2]),max(b[1],b[3])]
            disjoint=extent[2]<box[0] or extent[0]>box[2] or extent[3]<box[1] or extent[1]>box[3]
            witness.require(disjoint,'invalid_native_glyph_intersects_scope')
            outside_invalid.append(dict(glyph_index=i,**g,status='outside_held_invalid_geometry'))
        elif intersects(b,box):
            witness.require(contained(b,box) or not g['text'].strip(),'scope_clips_native_glyph')
            if g['text'].strip():inside.append(dict(glyph_index=i,**g))
    witness.require(bool(inside),'no_nonempty_native_glyphs_in_scope')
    return inside,outside_invalid


def require_frame(native,layout,paint):
    expected=[0,0,layout['width'],layout['height']]
    # MuPDF stores page rectangles as IEEE binary32, unlike pdfplumber's
    # Python floats. Match that exact representation, not a geometric tolerance.
    paint_expected=[struct.unpack('f',struct.pack('f',v))[0] for v in expected]
    witness.require(list(native.bbox)==expected and list(paint['page_bbox'])==paint_expected
        and (native.cropbox is None or list(native.cropbox)==list(native.mediabox)),
        'nonzero_or_cropped_source_frame_requires_separate_proof')


def account(region,cached_glyphs,fresh_glyphs,paint,image,fields,table_boxes,policy):
    """A component result only. The input identity boundary lives in derive."""
    witness.require(fresh_glyphs==cached_glyphs,'fresh_source_glyph_inventory_differs_from_frozen_layout')
    box=region['bbox_pdf_points']
    result=dict(region=region,native_fields=fields,source_object_raster_accounting='held',
        transcription='unproved',language='unproved',layout='unproved',page_quality_clearance=False,
        source_quality_approval_granted=False,publication_eligible=False,calculation_allowed=False,
        semantic_completeness='not_established',outside_scope_state='held_not_discarded')
    try:
        witness.require(valid_box(box),'invalid_scope_box')
        for field in fields:
            geometry=(field.get('native_geometry') or {}).get('bbox')
            witness.require(field['native_source_span']['page']==region['page'] and valid_box(geometry)
                and contained(geometry,box),'scope_does_not_bind_native_field_geometry')
        witness.require(bool(fields),'scope_has_no_bound_native_field')
        inside,invalid=partition(fresh_glyphs,box)
        result.update(source_glyphs=inside,outside_invalid_glyph_regions=invalid)
        witness.require(not any(intersects(box,t) for t in table_boxes),'native_table_intersects_scope')
        from .native_paint_visibility import rules
        visibility=rules()['rules']['later_opaque_rectangular_fill']
        witness.require(supported_contexts(paint['drawings'],box,visibility),'unsupported_source_clip_or_transparency')
        for draw in paint['drawings']:
            if draw['type'] not in ('clip','group'):
                b=draw.get('rect')
                witness.require(b is not None and not intersects(b,box),'nontext_source_paint_intersects_scope')
        source_paints=[];zero_ink_spaces=[];seqs={}
        for g in paint['glyphs']:seqs.setdefault(g['seqno'],[]).append(g)
        for index,item in enumerate(paint['paint_bbox_log']):
            b=item['bbox']
            # Exact boundary contact is not an independent exclusion witness.
            if b[2]<box[0] or b[0]>box[2] or b[3]<box[1] or b[1]>box[3]:continue
            witness.require(item['kind']=='fill-text' and index in seqs,'unsupported_source_paint_intersects_scope')
            group=seqs[index]
            ink_glyphs=[g for g in group if g['text'].strip()]
            if not ink_glyphs:
                # A positive advance box is not painted ink. Only a fresh
                # exact point ink-bound for explicit spaces can explain this
                # empty paint; it never authorizes a single mask pixel.
                witness.require(all(math.isfinite(v) for v in b) and b[0]==b[2] and b[1]==b[3]
                    and group and all(g['text'] in (' ','\u00a0') and g['type']==0 and g['opacity']==1.0
                        and not g['layer'] and g['direction']==[1.0,0.0] and g['wmode']==0 for g in group),
                    'source_paint_has_no_accountable_glyphs')
                zero_ink_spaces.append(dict(seqno=index,bbox=b,glyphs=group,mask_pixels_authorized=0))
                continue
            for g in ink_glyphs:
                witness.require(valid_box(g['bbox']) and contained(g['bbox'],box),'scope_cuts_independent_text_paint')
                witness.require(g['type'] in visibility['allowed_text_paint_types'] and g['opacity']==1.0
                    and not g['layer'] and g['direction']==[1.0,0.0] and g['wmode']==0,
                    'unsupported_source_glyph_paint')
                from .native_tight_cell_ownership import visible_source_color
                witness.require(visible_source_color(g['color']),'white_or_unsupported_source_text_color')
            source_paints.append(dict(seqno=index,kind=item['kind'],bbox=b,glyphs=group))
        witness.require(Counter(c for g in inside for c in g['text'] if not c.isspace())==
            Counter(c for p in source_paints for g in p['glyphs'] for c in g['text'] if not c.isspace()),
            'independent_source_object_glyph_inventory_differs')
        result.update(source_text_paints=source_paints,zero_ink_source_spaces=zero_ink_spaces)
        import numpy as np
        ink=np.asarray(image.convert('L'))<policy['source_dark_pixel_threshold']
        mask=np.zeros(ink.shape,dtype=bool);scale=region['render_dpi']/72;ox,oy,_,_=region['crop_bbox_pixels']
        pad=policy['ocr_box_padding_px']
        for g in inside:
            x0,y0,x1,y1=g['bbox']
            left=max(0,math.floor(x0*scale)-ox-pad);right=min(image.width,math.ceil(x1*scale)-ox+pad)
            top=max(0,math.floor(y0*scale)-oy-pad);bottom=min(image.height,math.ceil(y1*scale)-oy+pad)
            mask[top:bottom,left:right]=True
        uncovered=int((ink & ~mask).sum())
        result.update(source_ink_pixels=int(ink.sum()),unaccounted_ink_pixels=uncovered,
            glyph_mask_padding_pixels=pad,source_dark_pixel_threshold=policy['source_dark_pixel_threshold'])
        witness.require(uncovered==0,'unaccounted_source_ink_inside_scope')
        result.update(source_object_raster_accounting='proved',reason='fresh_source_objects_and_zero_unaccounted_crop_ink')
    except ValueError as error:result['reason']=str(error)
    return result


def derive(request_bundle):
    import pdfplumber
    from PIL import Image
    from .legal_l1 import rows
    from .quality_gate import load_policy
    from .native_paint_visibility import capture
    request_bundle=Path(request_bundle).resolve()
    manifest=witness.check_files(request_bundle,'request')
    request_before={n:file_sha256(request_bundle/n) for n in ('manifest.json',*manifest['files'])}
    request=witness.checked_request(request_bundle)
    meta=witness.load(request_bundle/'manifest.json')['metadata'];package=Path(meta['source_package'])
    before={n:file_sha256(package/n) for n in witness.SOURCE_FILES}
    layouts={r['page']:r for r in rows(package/'layout.jsonl')}
    pages={r['page']:r for r in rows(package/'extraction/pages.jsonl')}
    tables=witness.load(package/'extraction/tables.json');policy=load_policy();results=[];cache={}
    witness.require(len(request['regions'])<=16,'bounded_existing_witness_regions_required')
    with pdfplumber.open(package/'original.pdf') as pdf:
        for region in request['regions']:
            n=region['page'];layout=layouts[n]
            witness.require(region['render_dpi']<=240,'native_scope_render_dpi_must_not_exceed_240')
            witness.require(layout['page_text_sha256']==pages[n]['text_sha256'],'scope_page_reading_binding_differs')
            if n not in cache:
                native=pdf.pages[n-1]
                witness.require(native.width==layout['width'] and native.height==layout['height'],'source_page_geometry_changed')
                paint=capture(package/'original.pdf',n);require_frame(native,layout,paint)
                cache[n]=([glyph_record(c) for c in native.chars],paint)
            fresh,paint=cache[n]
            witness.require(paint['source_pdf_sha256']==request['source_pdf_sha256'],'source_paint_pdf_identity_differs')
            fields=[f for f in request['fields'] if f['region_id']==region['id']]
            with Image.open(request_bundle/region['image_file']) as opened:
                image=opened.copy()
            selected=[t['bbox'] for t in tables if t['page']==n]
            result=account(region,layout['glyphs'],fresh,paint,image,fields,selected,policy)
            result.update(source_pdf_sha256=request['source_pdf_sha256'],source_package_sha256=request['source_package_sha256'],
                page_text_sha256=pages[n]['text_sha256'],layout_record_sha256=witness.digest(layout),
                source_paint_capture_sha256=witness.digest(paint))
            results.append(result)
    witness.require(before=={n:file_sha256(package/n) for n in witness.SOURCE_FILES},'source_changed_during_scoped_raster_accounting')
    witness.require(request_before=={n:file_sha256(request_bundle/n) for n in request_before},'request_changed_during_scoped_raster_accounting')
    return dict(schema_version=SCHEMA,request_sha256=file_sha256(request_bundle/'request.json'),
        source_pdf_sha256=request['source_pdf_sha256'],source_package_sha256=request['source_package_sha256'],
        source_files_sha256=before,results=results,components_proved=sum(r['source_object_raster_accounting']=='proved' for r in results),
        components_held=sum(r['source_object_raster_accounting']=='held' for r in results),
        dependency_versions={n:version(n) for n in ('pdfplumber','pypdf','pypdfium2','pymupdf')},
        original_page_outcomes_preserved=True,new_content_extraction=False,new_ocr_calls=0,model_calls=0,
        acceptance_scope='source_object_and_raster_accounting_component_only',**witness.FLAGS)


def build(request_bundle,output):
    request_bundle=Path(request_bundle).resolve();impl=implementation();result=derive(request_bundle)
    witness.require(impl==implementation(),'scoped_raster_implementation_changed')
    with witness.output_stage(output,witness.request_inputs(request_bundle)) as stage:
        witness.write(stage/'accounting.json',result)
        witness.write(stage/'manifest.json',dict(schema_version=SCHEMA,request=str(request_bundle),
            request_manifest_sha256=file_sha256(request_bundle/'manifest.json'),implementation=impl,
            files={'accounting.json':file_sha256(stage/'accounting.json')},**witness.FLAGS))
    return dict(schema_version=SCHEMA,output=str(Path(output).resolve()),components_proved=result['components_proved'],
        components_held=result['components_held'],acceptance_scope=result['acceptance_scope'],**witness.FLAGS)


def verify(bundle):
    bundle=Path(bundle).resolve()
    witness.require(bundle.is_dir() and {p.name for p in bundle.iterdir()}=={'manifest.json','accounting.json'}
        and all(p.is_file() and not p.is_symlink() for p in bundle.iterdir()),'scoped_raster_requires_sealed_regular_files')
    manifest=witness.load(bundle/'manifest.json');manifest_sha=file_sha256(bundle/'manifest.json')
    witness.require(manifest.get('schema_version')==SCHEMA and manifest.get('implementation')==implementation(),
                    'scoped_raster_contract_or_implementation_differs')
    witness.require(all(manifest.get(k) is v for k,v in witness.FLAGS.items()),'scoped_raster_cannot_grant_approval')
    witness.require(manifest.get('files')=={'accounting.json':file_sha256(bundle/'accounting.json')},'scoped_raster_artifact_changed')
    request=Path(manifest['request'])
    witness.require(file_sha256(request/'manifest.json')==manifest['request_manifest_sha256'],'scoped_raster_request_changed')
    fresh=derive(request)
    witness.require(witness.canonical(fresh)==witness.canonical(witness.load(bundle/'accounting.json')),'scoped_raster_differs_from_fresh_source_replay')
    witness.require(file_sha256(bundle/'manifest.json')==manifest_sha and manifest['implementation']==implementation()
        and manifest['files']=={'accounting.json':file_sha256(bundle/'accounting.json')},'scoped_raster_bundle_or_implementation_changed')
    return dict(schema_version=SCHEMA,valid=True,components_proved=fresh['components_proved'],
        components_held=fresh['components_held'],acceptance_scope=fresh['acceptance_scope'],**witness.FLAGS)
