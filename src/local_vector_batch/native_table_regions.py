#!/usr/bin/env python3
"""Source-bound plural native-table regions and independently read outside prose.

The proof preserves the original page text and records its table traversal as a
structured grid, rather than asserting a single independent prose reading order
through interleaved table columns. No region is masked by its bounding interior.
"""
import argparse
from collections import Counter
import copy
from io import BytesIO
import json
import math
import re
from pathlib import Path
import shutil
import sys
import tempfile
import hashlib

from .native_table_feedback import canonical, sha, revalidate, capture_source, validate_feedback, read_rows
from .proof_identity import verify_l1_source_readiness as verify_l1_candidate_readiness,proof_configuration

KIND = 'powerlex.native-table-regions.v1'


class _VerifiedSourceRenderCache:
    """Job-local original raster cache; live source/profile drift fails closed."""
    def __init__(self,source,snapshot,policy):
        from .proof_identity import inspect_pdf2data_profile
        self.source=Path(source);self.snapshot=snapshot;self.policy=policy
        self.profile=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
        self.key={'source_pdf_sha256':snapshot['source_pdf_sha256'],'page':snapshot['page'],
                  'page_text_sha256':snapshot['page_text_sha256'],'policy_sha256':sha(canonical(policy)),
                  'proof_profile_sha256':self.profile['profile_sha256']}
        self.images={}

    def _check(self):
        from .proof_identity import inspect_pdf2data_profile
        from .hashing import file_sha256
        actual=file_sha256(self.source)
        if actual!=self.key['source_pdf_sha256']:raise ValueError('Job raster cache original PDF changed')
        if sha(canonical(self.policy))!=self.key['policy_sha256']:raise ValueError('Job raster cache policy changed')
        if any(self.snapshot[k]!=self.key[k]for k in('source_pdf_sha256','page','page_text_sha256')):
            raise ValueError('Job raster cache source/page/text identity changed')
        current=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
        if current['profile_sha256']!=self.key['proof_profile_sha256']:raise ValueError('Job raster cache proof/dependency epoch changed')

    def image(self,render,**options):
        self._check();key=canonical(options)
        if key not in self.images:
            image=render(**options);stream=BytesIO();image.original.save(stream,format='PNG')
            self.images[key]=(image,sha(stream.getvalue()))
        image,expected=self.images[key];stream=BytesIO();image.original.save(stream,format='PNG')
        if sha(stream.getvalue())!=expected:raise ValueError('Job raster cache pixels changed')
        return image


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def chars(value):
    return ''.join(c for c in value if not c.isspace())


def bbox(item):
    return [float(item[k]) for k in ('x0','top','x1','bottom')]


def contains(box, x, y, tolerance=0):
    return box[0]-tolerance <= x <= box[2]+tolerance and box[1]-tolerance <= y <= box[3]+tolerance


def grid_path_owner(box, tables, snap_tolerance):
    """Associate an actual thin path with detector-snapped cell boundary lines."""
    x0,y0,x1,y1=box
    if min(x1-x0,y1-y0)>1:
        return None
    owners=[]
    for table in tables:
        if not (contains(table['bbox'],x0,y0,snap_tolerance) and contains(table['bbox'],x1,y1,snap_tolerance)):
            continue
        xs={v for cell in table['cell_spans']for v in (cell['bbox'][0],cell['bbox'][2])}
        ys={v for cell in table['cell_spans']for v in (cell['bbox'][1],cell['bbox'][3])}
        vertical=x1-x0<=1 and any(abs((x0+x1)/2-v)<=snap_tolerance for v in xs) and all(any(abs(y-v)<=snap_tolerance for v in ys)for y in (y0,y1))
        horizontal=y1-y0<=1 and any(abs((y0+y1)/2-v)<=snap_tolerance for v in ys) and all(any(abs(x-v)<=snap_tolerance for v in xs)for x in (x0,x1))
        if max(x1-x0,y1-y0)<5:
            vertical=horizontal=(max(x1-x0,y1-y0)<=1 and
                any(abs((x0+x1)/2-v)<=snap_tolerance for v in xs) and
                any(abs((y0+y1)/2-v)<=snap_tolerance for v in ys))
        if vertical or horizontal:
            owners.append(table['table_version_id'])
    if len(owners)>1:
        raise ValueError('Native path has ambiguous table ownership')
    return owners[0] if owners else None


def derive_regions(package, snapshot, cell_result, *, omitted_path_indexes=()):
    """Re-read original source glyphs, region lines and paths; no caller gates."""
    import cv2
    import numpy as np
    import pdfplumber
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from pdfplumber.table import TableSettings
    from local_vector_batch.native_consensus import agrees,repair_control_hyphens
    from local_vector_batch.quality_gate import load_policy,native_source_raster_evidence,WORD,NUMBER,_normalized,_operators
    from local_vector_batch.native_styles import source_underlines

    package=Path(package);source=package/'original.pdf';n=snapshot['page'];policy=load_policy()
    if sha(source.read_bytes())!=snapshot['source_pdf_sha256']:
        raise ValueError('Region proof original PDF differs from table snapshot')
    from .native_verification_session import active_session
    session=active_session()
    page=session.page_row(package/'extraction/pages.jsonl',n)if session else next(p for p in read_rows(package/'extraction/pages.jsonl')if p['page']==n)
    if page['text_sha256']!=snapshot['page_text_sha256'] or sha(page['text'])!=page['text_sha256']:
        raise ValueError('Region proof selected page text is stale')
    tables=snapshot['tables']
    if cell_result['status']!='validated' or len(cell_result['tables'])!=len(tables):
        raise ValueError('Every native table needs a complete source-cell proof')
    for table in tables:
        proofs=[p for p in cell_result['tables']if p['table_version_id']==table['table_version_id']]
        if len(proofs)!=1 or len(proofs[0]['cells'])!=len(table['cell_spans']):
            raise ValueError('Source-cell proof omits table membership')
        for cell in table['cell_spans']:
            proof=next((p for p in proofs[0]['cells']if p['id']==cell['id']),None)
            if not proof or proof['status']!='validated' or any(proof[k]!=cell[k]for k in ('bbox','row','column','row_span','column_span','selected_cell_sha256','independent_cell_sha256')):
                raise ValueError('Source-cell proof differs from fresh source geometry/readings')
    for i,a in enumerate(tables):
        for b in tables[i+1:]:
            if min(a['bbox'][2],b['bbox'][2])>max(a['bbox'][0],b['bbox'][0]) and min(a['bbox'][3],b['bbox'][3])>max(a['bbox'][1],b['bbox'][1]):
                raise ValueError('Native table regions overlap')
    with pdfplumber.open(source)as pdf,pdfium.PdfDocument(source)as independent:
        native=pdf.pages[n-1];view=independent[n-1];tp=view.get_textpage()
        try:
            # The original is rendered once per immutable page/options within
            # this derivation. Both users receive the exact same source pixels;
            # source, policy, page/text and full proof/dependency epoch are live
            # checked on every use. Nothing is cached across decoder jobs.
            raster_cache=_VerifiedSourceRenderCache(source,snapshot,policy);render=native.to_image
            native.to_image=lambda **options:raster_cache.image(render,**options)
            literal_footer_paths=[]
            if native.images or native.curves:
                from .native_literal_footer_paths import prove_literal_footer_paths
                literal_footer_paths=prove_literal_footer_paths(native,view,policy)
            zero_table_source_proof=None
            if not tables:
                from .native_zero_table_prose import prove_zero_table_source
                zero_table_source_proof=prove_zero_table_source(source,n,native,snapshot['source_pdf_sha256'])
            from .text_script import native_font_script_evidence
            font_evidence = native_font_script_evidence(native.chars, policy)
            if font_evidence['required_scripts']:
                raise ValueError('Native table font mapping needs independent raster linguistic proof')
            raw_native_page=native.extract_text()or''
            if raw_native_page!=page['text']and not any(c.get('processed_cell_version')for t in cell_result['tables']for c in t['cells']):
                raise ValueError('Selected text differs from fresh original native extraction')
            glyph_inventory=Counter(chars(''.join(c['text']for c in native.chars)))
            if Counter(chars(page['text']))!=glyph_inventory:
                raise ValueError('Selected text loses or adds source glyphs')
            independent_layout=PdfReader(source).pages[n-1].extract_text(extraction_mode='layout')or''
            processed_cells=[]
            for table in tables:
                proved=next(p for p in cell_result['tables']if p['table_version_id']==table['table_version_id'])
                for cell in table['cell_spans']:
                    observed=next(p for p in proved['cells']if p['id']==cell['id'])
                    if observed.get('processed_cell_version'):
                        processed_cells.append((table,cell,observed['processed_cell_version']))
            if processed_cells:
                from .native_cell_space_projection import prove_processed_cell
                from .native_table_cell_spacing import read_source_box
                from .native_tight_cell_ownership import page_source_cell_context
                all_cells=[cell for table in tables for cell in table['cell_spans']]
                with page_source_cell_context(source,n,native,snapshot['source_pdf_sha256'],all_cells,
                        pdfium_page=view,text_page=tp,third_layout=independent_layout):
                    for table,cell,stored_projection in processed_cells:
                        x0,y0,x1,y1=cell['bbox']
                        raw,_=read_source_box(native,view,tp,[x0+.2,y0+.2,x1-.2,y1-.2])
                        actual=prove_processed_cell(native,view,tp,
                            {**cell,'table_geometry_sha256':table['table_geometry_sha256']},
                            raw,independent_layout,page['text'],snapshot['source_pdf_sha256'])
                        if actual is None or actual!=stored_projection:
                            raise ValueError('Processed cell differs from fresh original glyph/ink/context proof')
            page_representation=None
            if raw_native_page!=page['text']:
                if not processed_cells:
                    raise ValueError('Selected text differs from fresh original native extraction')
                from .native_cell_space_projection import project_page_text
                page_representation=project_page_text(raw_native_page,[p for _,_,p in processed_cells],page['text'])
            memberships={cell['id']:[] for table in tables for cell in table['cell_spans']}
            outside=[]
            for glyph in native.chars:
                box=bbox(glyph);x,y=(box[0]+box[2])/2,(box[1]+box[3])/2
                owners=[table for table in tables if contains(table['bbox'],x,y)]
                if len(owners)>1:raise ValueError('Source glyph is assigned to overlapping tables')
                if not owners:outside.append(glyph);continue
                cells=[cell for cell in owners[0]['cell_spans']if contains(cell['bbox'],x,y)]
                if len(cells)!=1:raise ValueError('Source glyph has missing/ambiguous table-cell ownership')
                memberships[cells[0]['id']].append(glyph)
            for table in tables:
                for cell in table['cell_spans']:
                    if Counter(chars(cell['text']or''))!=Counter(chars(''.join(c['text']for c in memberships[cell['id']]))):
                        raise ValueError('A table cell does not conserve its original source glyph inventory')
            outside_ids={id(g)for g in outside}
            prose_page=native.filter(lambda obj:obj.get('object_type')!='char'or id(obj)in outside_ids)
            lines=prose_page.extract_text_lines(return_chars=True)
            prose=[]
            for index,line in enumerate(lines):
                box=bbox(line)
                ordered=sorted((g for g in line['chars']if g['text'].strip()),key=lambda g:g['x0'])
                from .native_literal_footer_paths import footer_page_number_layout
                footer_layout=footer_page_number_layout(line,native.width,native.height,n,[t['bbox']for t in tables],native.rects)
                footer=(box[1]>=native.height*.95 and re.search(r'\bPage\s+'+str(n)+r'\s*$',line['text'],re.I)is not None)or footer_layout is not None
                metadata_proof=None
                if not footer and any(b['x0']-a['x1']>max(36,4*max(a.get('size',0),b.get('size',0)))for a,b in zip(ordered,ordered[1:])):
                    from .native_header_registry import prove_header_gap
                    try:
                        metadata_proof=prove_header_gap(line,native,n,[t['bbox']for t in tables],tp,independent_layout,pdfium_page=view,header_tables=tables,table_proofs=cell_result['tables'])
                    except ValueError as error:
                        raise ValueError('Outside-table prose has an unresolved column/block gap: '+str(error))from error
                from .native_table_cell_spacing import read_source_box
                read_box=[max(0,box[0]-.2),max(0,box[1]-.2),min(native.width,box[2]+.2),min(native.height,box[3]+.2)]
                raw,source_rectangle=read_source_box(native,view,tp,read_box)
                mapped,repairs=repair_control_hyphens(raw,line['text'],independent_layout)
                prose_reader_proof=None
                if metadata_proof is None and not agrees(line['text'],mapped,exact_characters=True):
                    from .native_prose_reader import map_prose_reader
                    prose_reader_proof=map_prose_reader(line,native,view,tp,raw,independent_layout)
                    if prose_reader_proof is not None:
                        mapped=prose_reader_proof['mapped_text'];repairs=prose_reader_proof['repairs']
                if metadata_proof is None and not agrees(line['text'],mapped,exact_characters=True):
                    raise ValueError(f'Outside-table prose line {index} disagrees with source reader at {box}')
                if metadata_proof is None and any(NUMBER.findall(_normalized(line['text']))!=NUMBER.findall(_normalized(mapped)) or
                       _operators(line['text'],policy)!=_operators(mapped,policy) for _ in (0,)):
                    raise ValueError('Outside prose numeric/operator disagreement')
                prose.append({'sequence':index,'bbox':box,'region_kind':'source_header_metadata'if metadata_proof else('source_footer_metadata'if footer else'outside_table_prose'),
                              'selected_text_sha256':sha(line['text']),
                              'independent_raw_sha256':sha(raw),'independent_mapped_sha256':sha(mapped),
                              'selected_text':line['text'],'independent_raw_text':raw,'reader_mapping_repairs':repairs,'independent_source_rectangle':source_rectangle,
                              'ordered_numbers':NUMBER.findall(_normalized(line['text'])),
                              'ordered_operators':_operators(line['text'],policy),'source_glyph_count':len(line['chars']),
                              **({'header_metadata_block_proof':metadata_proof}if metadata_proof else{}),
                              **({'source_footer_layout_candidate':footer_layout}if footer_layout else{}),
                              **({'outside_prose_reader_mapping':prose_reader_proof}if prose_reader_proof else{})})
            if Counter(chars(''.join(line['text']for line in lines)))!=Counter(chars(''.join(g['text']for g in outside))):
                raise ValueError('Outside prose line registry omits or duplicates source glyphs')
            # Source ink inventory only: the self text here authorizes NO
            # independent word/number/order gate. Those come from regions above.
            base=native_source_raster_evidence(page,native,snapshot['source_pdf_sha256'],page['text'],table_count=len(tables))
            if 'rendered_image_sha256'not in base or 'unaccounted_ink_pixels'not in base:
                raise ValueError('Original native raster inventory unavailable')
            image=native.to_image(resolution=base['render_dpi']);stream=BytesIO();image.original.save(stream,format='PNG')
            if sha(stream.getvalue())!=base['rendered_image_sha256']:raise ValueError('Original raster changed during proof')
            ink=np.asarray(image.original.convert('L'))<policy['source_dark_pixel_threshold'];h,w=ink.shape
            edge=policy['source_edge_exclusion_px']
            if edge:ink[:edge,:]=False;ink[-edge:,:]=False;ink[:,:edge]=False;ink[:,-edge:]=False
            ox,oy,right,bottom=image.bbox;sx=w/(right-ox);sy=h/(bottom-oy)
            def mask(target,box,padding=0):
                x0,y0,x1,y1=box
                target[max(0,int((y0-oy)*sy)-padding):min(h,int((y1-oy)*sy)+padding+1),max(0,int((x0-ox)*sx)-padding):min(w,int((x1-ox)*sx)+padding+1)]=True
            glyph_mask=np.zeros_like(ink)
            for glyph in native.chars:
                if glyph['text'].strip():mask(glyph_mask,bbox(glyph),policy['ocr_box_padding_px'])
            for underline in source_underlines(native,table_count=len(tables)):mask(glyph_mask,underline['source_bbox'])
            outside_ink=ink&~glyph_mask
            _,labels,source_stats,_=cv2.connectedComponentsWithStats(outside_ink.astype('uint8'),8)
            residual=np.zeros_like(ink)
            for region in base['unresolved_visual_regions']:
                x0,y0,x1,y1=region['bbox_pixels']
                expected=[x0,y0,x1-x0,y1-y0,region['ink_pixels']]
                matches=[index for index,stats in enumerate(source_stats[1:],1)if list(map(int,stats))==expected]
                if len(matches)!=1:raise ValueError('Original residual connected-component identity changed')
                residual|=labels==matches[0]
            if int(residual.sum())!=base['unaccounted_ink_pixels']:
                raise ValueError('Exact original residual ink set cannot be reproduced')
            grid=np.zeros_like(ink);paths=[];settings=TableSettings.resolve({})
            snap=max(settings.snap_x_tolerance,settings.snap_y_tolerance)
            for object_type,objects in [('rect',native.rects),('line',native.lines)]:
                for index,obj in enumerate(objects):
                    box=bbox(obj);linewidth=float(obj.get('linewidth',0))
                    if object_type=='line':
                        if not (box[0]==box[2] or box[1]==box[3])or not 0<linewidth<=1:continue
                        half=linewidth/2;box=[box[0]-half,box[1]-half,box[2]+half,box[3]+half]
                    elif obj.get('stroke')or not obj.get('fill'):continue
                    owner=grid_path_owner(box,tables,snap)
                    if owner is None:continue
                    if max(box[2]-box[0],box[3]-box[1])<5:
                        # A literal tiny corner tile must touch a longer actual
                        # ruling along exactly the same narrow source band.
                        def connected(other):
                            b=bbox(other)
                            if min(b[2]-b[0],b[3]-b[1])>1 or max(b[2]-b[0],b[3]-b[1])<5:return False
                            vertical=abs(box[0]-b[0])<.02 and abs(box[2]-b[2])<.02 and min(abs(box[3]-b[1]),abs(box[1]-b[3]))<.02
                            horizontal=abs(box[1]-b[1])<.02 and abs(box[3]-b[3])<.02 and min(abs(box[2]-b[0]),abs(box[0]-b[2]))<.02
                            return vertical or horizontal
                        if not any(connected(other)for other in native.rects):continue
                    if (object_type,index)not in omitted_path_indexes:mask(grid,box)
                    paths.append({'object_type':object_type,'object_index':index,'source_bbox':bbox(obj),
                                  'literal_ink_bbox':box,'linewidth':linewidth,'table_version_id':owner,
                                  'source_object_sha256':sha(canonical(obj)),
                                  'classification':'native_table_ruling_retained_as_structure'})
            from .literal_path_frames import derive_literal_path_mask
            literal_mask,literal_paths=derive_literal_path_mask(native,view,ink,image.bbox)
            from .native_literal_footer_paths import literal_footer_mask
            footer_mask=literal_footer_mask(native,image.bbox,ink.shape,literal_footer_paths)
            from .native_zero_length_path_components import retained_footer_rectangle_mask
            footer_frame_mask,footer_frames=retained_footer_rectangle_mask(native,image.bbox,ink,literal_footer_paths)
            remaining=residual&~grid&~literal_mask&~footer_mask&~footer_frame_mask
            count,_,stats,_=cv2.connectedComponentsWithStats(remaining.astype('uint8'),8)
            uncovered=[{'bbox_pixels':[int(x),int(y),int(x+width),int(y+height)],'ink_pixels':int(area)}for x,y,width,height,area in stats[1:]]
            if uncovered:
                raise ValueError('Uncovered original source ink remains: '+canonical(uncovered))
            proof={'kind':KIND,'source_pdf_sha256':snapshot['source_pdf_sha256'],'page':n,
                   'page_text_sha256':page['text_sha256'],'tables':cell_result['tables'],
                   **({'source_bound_page_representation':page_representation}if page_representation is not None else{}),
                   **({'zero_table_source_proof':zero_table_source_proof}if zero_table_source_proof is not None else{}),
                   'outside_prose':prose,'source_glyph_inventory_sha256':sha(canonical(sorted(glyph_inventory.items()))),
                   'source_glyph_count':len(native.chars),'table_glyph_count':sum(len(v)for v in memberships.values()),
                   'outside_prose_glyph_count':len(outside),'source_render_sha256':base['rendered_image_sha256'],
                   'source_render_dpi':base['render_dpi'],'source_style_regions':base.get('source_style_regions',[]),
                   'existing_decorative_regions':base.get('decorative_regions',[]),'native_grid_paths':paths,
                   'literal_source_paths':literal_paths,
                   **({'literal_footer_paths':literal_footer_paths,'literal_footer_mask_sha256':sha(footer_mask.tobytes()),'literal_footer_frames':footer_frames,'literal_footer_frame_mask_sha256':sha(footer_frame_mask.tobytes())}if literal_footer_paths else{}),
                   'native_grid_snap_tolerance_points':snap,'native_grid_snap_basis':'selected_pdfplumber_TableSettings_default',
                   'source_residual_mask_sha256':sha(residual.tobytes()),'native_grid_mask_sha256':sha(grid.tobytes()),
                   'accounted_residual_ink_pixels':int(residual.sum()),'remaining_unaccounted_ink_pixels':0,
                   'word_number_operator_basis':('exact_order_within_each_source_bound_processed_cell_view_preserving_raw_matrix_and_outside_prose_line; no global table-column prose order claim'if processed_cells else'exact_order_within_each_source_cell_and_outside_prose_line_or_bound_header_metadata_block; no global table-column prose order claim'),
                   'header_layout':'source top-row geometry preserved; semantic header associations remain candidate',
                   'source_text_modified':False,'legal_review':'not_run','answer_publication_eligible':False,
                   'engine_sha256':sha(Path(__file__).read_bytes()),'policy_sha256':sha(canonical(policy))}
            return proof
        finally:
            tp.close();view.close()


def _quality_from_regions(snapshot, proof):
    """Registered evidence semantics: derives gates, never trusts caller booleans."""
    if proof['kind']not in PROOF_REGISTRY or proof['kind']!=KIND:
        raise ValueError('Unknown native region proof kind')
    if any(proof[k]!=snapshot[k]for k in ('source_pdf_sha256','page','page_text_sha256')):
        raise ValueError('Region proof source identity is stale')
    result=copy.deepcopy(snapshot['fresh_quality']['results'][0])
    if any(result['components'][k]['state']!='pass'for k in ('source_identity','text_content','language')):
        raise ValueError('Region proof cannot replace failed source identity/content/language gates')
    identity=sha(canonical(proof))
    result['components']['source_render']={'state':'pass','basis':'exact_native_glyphs_and_literal_grid_path_pixels',
                                         'receipt_sha256':identity,'rendered_image_sha256':proof['source_render_sha256'],
                                         'unaccounted_ink_pixels':0,'page_complete':True,'unresolved_visual_regions':[]}
    for name in ('words_and_order','ordered_numbers','ordered_operators'):
        result['components'][name]={'state':'pass','basis':proof['word_number_operator_basis'],
                                   'receipt_sha256':identity,'source_cells':sum(len(t['cells'])for t in proof['tables']),
                                   'outside_prose_lines':len(proof['outside_prose'])}
    result['components']['layout']={'state':'pass','basis':('plural_nonoverlapping_source_native_grids_and_outside_prose_registry'if proof['tables']else'zero_native_tables_with_complete_source_prose_and_literal_path_registry'),
                                   'receipt_sha256':identity,'tables':len(proof['tables']),'header_semantics_reviewed':False}
    result.update(outcome='auto_accepted',page_complete=True,text_region_verified=True,missing_evidence=[],failed_components=[],
                  unresolved_visual_regions=[],source_style_regions=[*proof['source_style_regions'],*proof['native_grid_paths']],
                  acceptance_scope='machine_verified_extraction_regions; table semantics/legal review not performed')
    return result


PROOF_REGISTRY={KIND:derive_regions}


def assess_registered_regions(package,snapshot,cell_result,proof):
    """Reconstruct the registered source proof before deriving any pass gates."""
    implementation=PROOF_REGISTRY.get(proof.get('kind'))
    if implementation is None:raise ValueError('Unknown native region proof kind')
    if implementation(package,snapshot,cell_result)!=proof:
        raise ValueError('Native region proof differs from current original-source revalidation')
    return _quality_from_regions(snapshot,proof)


def build_version(package,runtime,feedback,dossier,page,output,*,external_decoder=False):
    package,runtime,feedback,dossier,output=map(lambda p:Path(p).resolve(),(package,runtime,feedback,dossier,output))
    if output.exists()or output.is_relative_to(package)or package.is_relative_to(output)or output.is_relative_to(runtime)or runtime.is_relative_to(output)or feedback.is_relative_to(output)or dossier.is_relative_to(output):
        raise ValueError('Choose a new immutable output outside parent, source runtime and inputs')
    output.mkdir(parents=True);(output/'engine-source.py').write_bytes(Path(__file__).read_bytes())
    from local_vector_batch.legal_l1 import verify_document,write_rows,readiness_ledger
    verify_document(package)
    original_manifest=sha((package/'l1-package.json').read_bytes());original_pages=sha((package/'extraction/pages.jsonl').read_bytes())
    repair_configuration=proof_configuration('native_table_regions',package,Path(__file__))
    repair_configuration.update(source_runtime=str(runtime),feedback_path=str(feedback),feedback_sha256=sha(feedback.read_bytes()),dossier_path=str(dossier),dossier_sha256=sha(dossier.read_bytes()),page=page)
    cells=revalidate(package,runtime,feedback,dossier,output/'cell-revalidation',page)
    if cells['status']=='held':
        result={'status':'held','reason':'Source cell review remains incomplete','page':page,
            'cell_validation_status':'held','cell_validation_receipt':'cell-revalidation/validation.json',
            'source_text_modified':False,'new_source_quality_version_created':False,
            'answer_publication_eligible':False,'model_calls':0,'remote_operations':0}
        save(output/'validation.json',result);return result
    if cells['status']!='validated':raise ValueError('Source cell review remains incomplete/rejected')
    snapshot=read(output/'cell-revalidation/source-snapshot.json')
    try:
        proof=PROOF_REGISTRY[KIND](package,snapshot,cells)
        # Only the private builder reaches this path with a just-derived proof.
        # The public assessor still reconstructs arbitrary/untrusted proofs.
        quality=_quality_from_regions(snapshot,proof)
    except ValueError as error:
        result={'status':'held','reason':str(error),'page':page,'original_package':str(package),
                'source_text_modified':False,'new_source_quality_version_created':False,'answer_publication_eligible':False,
                'model_calls':0,'remote_operations':0}
        save(output/'validation.json',result);return result
    save(output/'region-proof.json',proof)
    dest=output/'packages'/package.name;dest.parent.mkdir()
    with tempfile.TemporaryDirectory(prefix='.native-region-',dir=output)as tmp:
        stage=Path(tmp)/package.name;shutil.copytree(package,stage)
        layout=read_rows(stage/'layout.jsonl')
        for row in layout:
            if row['page']==page:
                row['native_region_proof']=proof
                row['quality_repair_configuration']=repair_configuration
                row['native_region_repair_lineage']={'parent_package_sha256':original_manifest,
                    'cell_revalidation_signature':cells['signature'],'feedback_sha256':sha(feedback.read_bytes()),
                    'review_origin':'34 independent native cells and source-bound human cell decisions' if cells['jobs'] else 'independent native source cells',
                    'engine_sha256':sha(Path(__file__).read_bytes())}
        write_rows(stage/'layout.jsonl',layout)
        source_receipt=read(stage/'source-receipt.json');source_receipt['quality_repair_configuration']=repair_configuration;save(stage/'source-receipt.json',source_receipt)
        q=read(stage/'quality.json');q['results']=[quality if row['page']==page else row for row in q['results']]
        q['counts']={state:sum(row['outcome']==state for row in q['results'])for state in ('auto_accepted','repair_required','source_blocked')}
        q['native_region_engine']={'kind':KIND,'engine_sha256':sha(Path(__file__).read_bytes())};save(stage/'quality.json',q)
        save(stage/'readiness.json',readiness_ledger(read(stage/'structure.json'),q))
        write_rows(stage/'repair-queue.jsonl',[job for job in read_rows(stage/'repair-queue.jsonl')if job['page']!=page])
        manifest=read(stage/'l1-package.json');manifest['request_sha256']=sha(canonical({'parent':original_manifest,'proof':sha(canonical(proof)),'operation':KIND}))
        manifest['artifacts']={name:sha((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest)
        verified=verify_document(stage)
        if sha((stage/'extraction/pages.jsonl').read_bytes())!=original_pages:raise ValueError('Selected page bytes changed')
        stage.rename(dest)
    adapted=verify_l1_candidate_readiness(dest,runtime)
    if not next(row for row in adapted['pages']if row['page']==page)['embedding_candidate_allowed']:
        raise ValueError('Existing candidate adapter does not accept the verified source-quality page')
    if sha((package/'l1-package.json').read_bytes())!=original_manifest:raise ValueError('Original package changed')
    # Default standalone callers receive one fresh registered source decoder
    # before readiness. A trusted integration may defer that exact check to its
    # actual adapter boundary; such output is explicitly blocked and has NO
    # adapter records until the caller performs the fresh registered decoder.
    decoder=None if external_decoder else verify_region_version(dest,runtime)
    source=read(dest/'source-receipt.json')['declared'];aliases=source.get('catalog_source_identities')or[{'document_id':source['id'],'url':source.get('url','')}]
    if not external_decoder:write_rows(output/'adapter-records.jsonl',[{'document_id':alias['document_id'],'source_url':alias.get('url',''),
       'original_pdf':str(dest/'original.pdf'),'package_path':str(dest/'extraction'),'l1_package_path':str(dest),
       'source_pdf_sha256':proof['source_pdf_sha256'],'quality_receipts_file':str(dest/'quality.json'),
       'legal_review':'not_run','publication_eligible':False}for alias in aliases])
    result={'contract_version':'powerlex.native-region-quality-version.v1','status':'blocked_pending_registered_source_decoder'if external_decoder else'passed','page':page,
            'source_pdf_sha256':proof['source_pdf_sha256'],'parent_package':str(package),'parent_package_sha256':original_manifest,
            'new_l1_package_path':str(dest),'new_l1_package_sha256':sha((dest/'l1-package.json').read_bytes()),
            'verifier':verified,'quality_before':read(package/'quality.json')['counts'],'quality_after':q['counts'],
            'region_proof_sha256':sha(canonical(proof)),'cell_validation_signature':cells['signature'],
            'engine_source_artifact':'engine-source.py',
            'registered_source_decoder':decoder,'registered_source_decoder_required':True,
            'source_text_modified':False,'new_source_quality_version_created':True,'answer_publication_eligible':False,
            'model_calls':0,'remote_operations':0}
    result['signature']=sha(canonical(result));save(output/'validation.json',result);return result


def verify_region_version(folder,source_runtime):
    """Fresh registered decoder; matching hashes alone do not establish gates."""
    from .legal_l1 import verify_document
    from .proof_identity import verify_unchanged_quality_pages
    folder=Path(folder);verify_document(folder)
    config=read(folder/'source-receipt.json')['quality_repair_configuration'];parent=Path(config['parent_l1_package_path'])
    if config['operation']!='native_table_regions'or sha((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']:raise ValueError('Native region parent identity changed')
    current=proof_configuration('native_table_regions',parent,Path(__file__))
    if current['proof_profile']['source_configuration_expected']!=config['proof_profile']['source_configuration_expected']:raise ValueError('Native region proof execution profile changed')
    feedback,dossier=Path(config['feedback_path']),Path(config['dossier_path'])
    if sha(feedback.read_bytes())!=config['feedback_sha256']or sha(dossier.read_bytes())!=config['dossier_sha256']:raise ValueError('Native region source review inputs changed')
    n=config['page'];stored=next(row for row in read_rows(folder/'layout.jsonl')if row['page']==n)['native_region_proof']
    with tempfile.TemporaryDirectory()as temp:
        cells=revalidate(parent,source_runtime,feedback,dossier,Path(temp)/'cells',n)
        snapshot=read(Path(temp)/'cells/source-snapshot.json');proof=derive_regions(parent,snapshot,cells)
        if proof!=stored:raise ValueError('Stored native region evidence no longer matches original source')
        # `proof` was freshly reconstructed above, not supplied by a caller.
        fresh=_quality_from_regions(snapshot,proof)
        actual=next(q for q in read(folder/'quality.json')['results']if q['page']==n)
        if fresh!=actual:raise ValueError('Stored region quality differs from reconstructed source proof')
    verify_unchanged_quality_pages(folder,parent,[n])
    if(folder/'original.pdf').read_bytes()!=(parent/'original.pdf').read_bytes()or(folder/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes():raise ValueError('Source PDF or selected text changed in region version')
    return {'status':'verified','operation':'native_table_regions','source_quality_pages_verified':[n],
            'fresh_registered_source_decoder':True,'source_pdf_sha256':proof['source_pdf_sha256'],
            'page_text_sha256':proof['page_text_sha256'],'region_proof_sha256':sha(canonical(proof)),
            'source_text_modified':False,'publication_eligible':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('package','runtime','feedback','dossier','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--page',type=int,required=True);a=parser.parse_args()
    result=build_version(a.package,a.runtime,a.feedback,a.dossier,a.page,a.output)
    print(json.dumps(result));sys.exit(0 if result['status']=='passed'else 3)
