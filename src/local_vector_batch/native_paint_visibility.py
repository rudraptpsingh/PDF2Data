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
                       'excluded_from_visible_candidate': state == 'fully_occluded'})
    return result


def capture(source, page_number):
    import pymupdf
    from pypdf import PdfReader
    source = Path(source)
    # A soft mask can make an apparently opaque fill translucent without a
    # scalar opacity in the drawing trace. Fail closed, even for unused masks.
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
    inspect_resources(PdfReader(source).pages[page_number-1]['/Resources'])
    with pymupdf.open(source) as pdf:
        page = pdf[page_number-1]
        if page.rotation:
            raise ValueError('Rotated page visibility is not supported by this rule')
        glyphs = []
        traces = page.get_texttrace()
        paint_log=page.get_bboxlog()
        for trace in traces:
            for index,(codepoint,glyph_id,origin,bbox) in enumerate(trace['chars']):
                single=len(trace['chars'])==1 and not chr(codepoint).isspace()
                paint_kind,ink_bbox=paint_log[trace['seqno']]
                single=single and paint_kind=='fill-text'
                proof_bbox=[min(bbox[0],ink_bbox[0]),min(bbox[1],ink_bbox[1]),
                            max(bbox[2],ink_bbox[2]),max(bbox[3],ink_bbox[3])] if single else list(bbox)
                glyphs.append({'glyph_key':f"{trace['seqno']}:{index}", 'seqno':trace['seqno'],
                               'index':index,'codepoint':codepoint,'text':chr(codepoint), 'glyph_id':glyph_id,
                               'origin':list(origin),'bbox':list(bbox),'font':trace['font'],
                               'size':trace['size'],'type':trace['type'],'opacity':trace['opacity'],
                               'color':list(trace['color']) if isinstance(trace['color'],(tuple,list)) else trace['color'],
                               'direction':list(trace['dir']),'wmode':trace['wmode'],'layer':trace['layer'],
                               'proof_bbox':proof_bbox,'paint_ink_bbox':list(ink_bbox) if single else None,
                               'ink_bounds_basis':'single_character_paint_bbox'if single else 'not_individually_proved'})
        drawings = []
        for draw in page.get_drawings(extended=True):
            items = draw.get('items') or []
            rectangular = len(items)==1 and items[0][0]=='re'
            rect = list(items[0][1]) if rectangular else list(draw['rect']) if draw.get('rect') else None
            row = {k: draw.get(k) for k in ('type','level','seqno','layer','fill_opacity', 'opacity',
                                             'isolated','knockout','blendmode')}
            row.update(rect=rect,rectangular_path=rectangular,
                       fill=list(draw['fill']) if draw.get('fill') is not None else None)
            drawings.append(row)
        bboxlog = [{'kind':kind,'bbox':list(box)} for kind,box in page.get_bboxlog()]
        return {'source_pdf_sha256':sha(source.read_bytes()),'page':page_number,'page_bbox':list(page.rect),
                'reader':'PyMuPDF','reader_version':pymupdf.VersionBind,
                'glyphs':glyphs,'drawings':drawings,'paint_bbox_log':bboxlog}


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
