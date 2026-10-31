"""Additive mixed native/raster reconstruction with exact source-region proof.

Original native selection and OCR witnesses stay immutable provenance. Visible
readings and UI/model annotations are separate from the fresh source-pixel,
glyph, ordering and codepoint-span checks. This never confers legal approval.
"""
from pathlib import Path
from collections import Counter
from io import BytesIO
from difflib import SequenceMatcher
import argparse,copy,ctypes,hashlib,json,math,re,shutil,tempfile

KIND='source_bound_mixed_native_raster_reconstruction.v1'

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def rows(path):return[json.loads(line)for line in Path(path).read_text().splitlines()if line.strip()]
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def write_rows(path,value):Path(path).write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n'for v in value))
def compact(text):return ''.join(c for c in text if not c.isspace())
def box(chars):return[min(g['x0']for g in chars),min(g['top']for g in chars),max(g['x1']for g in chars),max(g['bottom']for g in chars)]
def intersects(a,b):return min(a[2],b[2])>max(a[0],b[0])and min(a[3],b[3])>max(a[1],b[1])
def runs(values):
    groups=[]
    for value in values:
        value=int(value)
        if not groups or value>groups[-1][-1]+1:groups.append([value])
        else:groups[-1].append(value)
    return groups

def native_units(parent,page_number,native,textpage,layout):
    from .native_consensus import repair_control_hyphens
    page=next(p for p in rows(Path(parent)/'extraction/pages.jsonl')if p['page']==page_number)
    if native.rotation or native.extract_text()!=page['text']:raise ValueError('Mixed proof requires unrotated unchanged native selection')
    if Counter(compact(page['text']))!=Counter(compact(''.join(g['text']for g in native.chars))):raise ValueError('Native selection does not conserve original glyphs')
    units=[];cursor=0;lines=native.extract_text_lines(return_chars=True)
    for n,line in enumerate(lines):
        text=line['text'];start=page['text'].find(text,cursor)
        if start!=cursor:raise ValueError('Native line partition differs from original selected codepoints')
        end=start+len(text)
        while end<len(page['text'])and page['text'][end].isspace():end+=1
        bbox=box(line['chars']);raw=textpage.get_text_bounded(left=bbox[0]-.2,bottom=native.height-bbox[3]-.2,right=bbox[2]+.2,top=native.height-bbox[1]+.2)
        witness,repairs=repair_control_hyphens(raw,text,layout)
        if compact(witness)!=compact(text):raise ValueError('Native line differs from fresh bounded PDFium reading')
        matching=[p for p in layout.splitlines()if compact(p)==compact(text)]
        if len(matching)!=1:raise ValueError('Native line missing/ambiguous in fresh independent layout')
        units.append({'kind':'native_source_line','text':page['text'][start:end],'source_bbox_PDF_points':bbox,'original_native_char_start':start,'original_native_char_length':end-start,'source_glyph_count':len(line['chars']),'PDFium_raw_sha256':sha(raw),'independent_layout_sha256':sha(matching[0]),'reader_mapping_repairs':repairs,'source_quote':True});cursor=end
    if cursor!=len(page['text']):raise ValueError('Native line partition clips original page')
    return page,units

def image_text_units(region):
    """Order physical text bands then x positions; all witness refs stay attached."""
    source=region['source_bound_line_candidates'];bands=[]
    for unit in sorted(source,key=lambda u:(u['source_bbox_PDF_points'][1],u['source_bbox_PDF_points'][0])):
        bbox=unit['source_bbox_PDF_points'];cy=(bbox[1]+bbox[3])/2;height=bbox[3]-bbox[1]
        matches=[b for b in bands if abs(cy-b['center'])<=.6*max(height,b['height'])and min(bbox[3],b['bottom'])>max(bbox[1],b['top'])]
        if len(matches)>1:raise ValueError('Raster text-band ordering is ambiguous')
        if matches:
            band=matches[0];band['units'].append(unit);band.update(top=min(band['top'],bbox[1]),bottom=max(band['bottom'],bbox[3]))
        else:bands.append({'center':cy,'height':height,'top':bbox[1],'bottom':bbox[3],'units':[unit]})
    result=[]
    for band in sorted(bands,key=lambda b:b['top']):
        units=sorted(band['units'],key=lambda u:u['source_bbox_PDF_points'][0])
        for index,unit in enumerate(units):
            literal=unit['literal'];literal.encode('utf-8')
            result.append({'kind':'reviewed_raster_literal','text':literal,'source_bbox_PDF_points':unit['source_bbox_PDF_points'],'source_crop_render_sha256':region['source_crop_render_sha256'],'original_reader_refs':unit['original_reader_refs'],'region_id':region['region_id'],'source_quote':True})
            result.append({'kind':'source_layout_separator','text':'\n'if index==len(units)-1 else' ','source_quote':False})
    if sum(len(x['original_reader_refs'])for x in result if x['kind']=='reviewed_raster_literal')!=region['original_reader_record_count']:raise ValueError('Raster representation loses original reader witnesses')
    return result

def reconstructed_text(native,images):
    events=[{'bbox':u['source_bbox_PDF_points'],'units':[u]}for u in native]
    for region in images:
        bbox=region['source_crop_bbox_PDF_points']
        if any(intersects(bbox,u['source_bbox_PDF_points'])for u in native):raise ValueError('Native/raster text overlap needs explicit deduplication proof')
        events.append({'bbox':bbox,'units':image_text_units(region)})
    events.sort(key=lambda e:(e['bbox'][1],e['bbox'][0]));text='';spans=[]
    for event in events:
        if text and not text.endswith('\n'):
            spans.append({'kind':'source_layout_separator','char_start':len(text),'char_length':1,'exact_quote':'\n','source_quote':False});text+='\n'
        for unit in event['units']:
            spans.append({**{k:v for k,v in unit.items()if k!='text'},'char_start':len(text),'char_length':len(unit['text']),'exact_quote':unit['text']});text+=unit['text']
    if ''.join(s['exact_quote']for s in spans if s['kind']=='native_source_line')!=''.join(u['text']for u in native):raise ValueError('Native text changes in additive reconstruction')
    for span in spans:
        if text[span['char_start']:span['char_start']+span['char_length']]!=span['exact_quote']:raise ValueError('Mixed codepoint span clipping')
    return text,spans

def raster_proof(native,independent,mixed,policy,rules):
    import numpy as np,cv2,pypdfium2 as pdfium
    dpi=rules['audit_dpi'];im=native.to_image(resolution=dpi).original;rgb=np.asarray(im.convert('RGB'));gray=np.asarray(im.convert('L'));ink=gray<policy['source_dark_pixel_threshold'];covered=np.zeros(ink.shape,bool);native_mask=np.zeros_like(covered);paint=np.zeros_like(covered);raster_mask=np.zeros_like(covered);sx=im.width/native.width;sy=im.height/native.height;pad=policy['ocr_box_padding_px'];classes=[]
    def pixels(bbox):return[max(0,int(bbox[0]*sx)-pad),max(0,int(bbox[1]*sy)-pad),min(im.width,int(bbox[2]*sx)+pad+1),min(im.height,int(bbox[3]*sy)+pad+1)]
    for char in native.chars:
        if not char['text'].strip():continue
        bbox=[char[k]for k in('x0','top','x1','bottom')]
        if not all(math.isfinite(v)for v in bbox)or bbox[0]>=bbox[2]or bbox[1]>=bbox[3]:raise ValueError('Invalid original native glyph box')
        x0,y0,x1,y1=pixels(bbox);native_mask[y0:y1,x0:x1]=True
    for region in mixed['regions']:
        for line in region['source_bound_line_candidates']:
            source_box=line['source_bbox_PDF_points'];region_box=region['source_crop_bbox_PDF_points']
            if source_box[3]-source_box[1]>rules['maximum_OCR_line_height_fraction']*(region_box[3]-region_box[1]):raise ValueError('Oversized OCR box cannot authorize mixed source coverage')
            x0,y0,x1,y1=pixels(source_box);raster_mask[y0:y1,x0:x1]=True
    covered|=native_mask|raster_mask
    inverted=rules['inverted_header_single_row_grid']
    for region in mixed['regions']:
        x0,y0,x1,y1=pixels(region['source_crop_bbox_PDF_points']);crop=ink[y0:y1,x0:x1];_,_,stats,_=cv2.connectedComponentsWithStats(crop.astype('uint8'),8)
        for bx,by,width,height,area in stats[1:]:
            if width<inverted['minimum_width_fraction']*crop.shape[1]or area<inverted['minimum_component_pixels']:continue
            candidates=runs(np.where(crop[by:by+height,bx:bx+width].mean(axis=1)>inverted['minimum_dark_row_fraction'])[0])
            for run in candidates:
                top=y0+by+run[0];bottom=y0+by+run[-1]+1;left=x0+bx;right=left+width
                if bottom-top<inverted['minimum_panel_height_pixels']:continue
                # Every light/inverse foreground pixel needs its own OCR box;
                # painting a rectangle never explains an unrecognized white word.
                foreground=gray[top:bottom,left:right]>policy['source_dark_pixel_threshold'];unseen=foreground&~raster_mask[top:bottom,left:right];_,_,unknown,_=cv2.connectedComponentsWithStats(unseen.astype('uint8'),8)
                if any(v[4]>policy['source_speck_max_pixels']for v in unknown[1:]):raise ValueError('Inverted raster panel has uncovered light source glyphs')
                panel=np.zeros_like(covered);panel[top:bottom,left:right]=ink[top:bottom,left:right];paint|=panel;covered|=panel
                ends=[y for y in range(bottom,min(y1,bottom+int(inverted['maximum_data_row_height_fraction']*crop.shape[0])))if ink[y,left:right].mean()>inverted['minimum_bottom_rule_fraction']]
                if len(ends)!=1:raise ValueError('Inverted raster header needs one independently bounded data-row bottom rule')
                end=ends[0];columns=runs(np.where((gray[bottom:end+1,left:right]<(255-policy['source_dark_pixel_threshold'])).all(axis=0))[0])
                if len(columns)<3 or any(len(c)>inverted['maximum_ruling_width_pixels']for c in columns):raise ValueError('Raster grid rulings are missing or too thick')
                observations=[o for o in mixed.get('current_structured_observations',[])if o['region_id']==region['region_id']]
                if len(observations)!=1:raise ValueError('Inverted raster table lacks exact reviewed source-column observations')
                table=observations[0].get('table_candidate',{});names=table.get('columns',[]);values=table.get('printed_numeric_literals',[])
                if len(columns)!=len(names)+1 or len(values)!=len(names):raise ValueError('Raster column boundaries differ from reviewed exact header/value count')
                source_cells=[]
                for column,(name,value)in enumerate(zip(names,values)):
                    lo=left+sum(columns[column])/len(columns[column]);hi=left+sum(columns[column+1])/len(columns[column+1]);header=[];data=[]
                    for unit in region['source_bound_line_candidates']:
                        b=unit['source_bbox_PDF_points'];cx,cy=(b[0]+b[2])*sx/2,(b[1]+b[3])*sy/2
                        if lo<cx<hi and top<cy<bottom and compact(unit['literal'])==compact(name):header.append(unit)
                        if lo<cx<hi and bottom<cy<end and compact(unit['literal'])==compact(value):data.append(unit)
                    if len(header)!=1 or len(data)!=1:raise ValueError('Reviewed raster header/value does not match exact source column geometry')
                    source_cells.append({'column':column,'header_literal':header[0]['literal'],'value_literal':data[0]['literal'],'header_bbox':header[0]['source_bbox_PDF_points'],'value_bbox':data[0]['source_bbox_PDF_points']})
                rule=np.zeros_like(covered);rule[end,left:right]=ink[end,left:right]
                for cols in columns:
                    for col in cols:rule[bottom:end+1,left+col]=ink[bottom:end+1,left+col]
                covered|=rule;paint|=rule
                classes.append({'kind':'source_inverted_panel_and_literal_grid_pixels','region_id':region['region_id'],'panel_pixels':[int(left),int(top),int(right),int(bottom)],'bottom_rule_y':end,'vertical_rule_pixel_groups':columns,'source_column_cells':source_cells,'source_dark_pixel_threshold':policy['source_dark_pixel_threshold'],'inverse_foreground_coverage_unresolved_components':0,'source_fill_pixels_sha256':sha(panel.tobytes()),'source_grid_pixels_sha256':sha(rule.tobytes()),'pixel_mask_interior_not_assumed':True})
    footer=rules['native_footer_fill'];path_objects=[o for o in independent.get_objects()if o.type==pdfium.raw.FPDF_PAGEOBJ_PATH]
    for rect in native.rects:
        bbox=[rect[k]for k in('x0','top','x1','bottom')]
        if rect.get('fill')is not True or rect['top']<native.height*footer['minimum_top_fraction']or rect['width']<native.width*footer['minimum_width_fraction']or rect['height']>footer['maximum_height_points']:continue
        if any(intersects(bbox,[g[k]for k in('x0','top','x1','bottom')])for g in native.chars)or any(intersects(bbox,r['source_crop_bbox_PDF_points'])for r in mixed['regions']):raise ValueError('Footer paint overlaps source text or image')
        objects=[o for o in path_objects if all(abs(a-b)<.001 for a,b in zip(o.get_bounds(),[bbox[0],native.height-bbox[3],bbox[2],native.height-bbox[1]]))]
        if len(objects)!=1:raise ValueError('Footer fill lacks independent PDFium path geometry')
        color=[ctypes.c_uint()for _ in range(4)]
        if not pdfium.raw.FPDFPageObj_GetFillColor(objects[0].raw,*[ctypes.byref(v)for v in color]):raise ValueError('Independent footer paint color unavailable')
        fill=[v.value for v in color[:3]]
        mode=ctypes.c_int();stroke=ctypes.c_int()
        if color[3].value!=255 or not pdfium.raw.FPDFPath_GetDrawMode(objects[0].raw,ctypes.byref(mode),ctypes.byref(stroke))or mode.value==0 or stroke.value:raise ValueError('Unsupported footer fill opacity/drawing mode')
        if any(abs(a-round(b*255))>1 for a,b in zip(fill,rect['non_stroking_color'])):raise ValueError('Footer source paint colors disagree')
        x0,y0,x1,y1=pixels(bbox);actual=rgb[y0:y1,x0:x1].astype(float);base=np.array(fill,dtype=float);denom=255-base
        if any(v==0 for v in denom):raise ValueError('Unsupported footer paint color')
        alpha=(255-actual)/denom;matches=(alpha.max(axis=2)-alpha.min(axis=2)<=footer['color_rounding_tolerance']/min(denom))&(alpha.min(axis=2)>=0)&(alpha.max(axis=2)<=1.01)
        mask=np.zeros_like(covered);mask[y0:y1,x0:x1]=ink[y0:y1,x0:x1]&matches;covered|=mask;paint|=mask
        classes.append({'kind':'independently_bound_native_footer_fill','bbox':bbox,'fill_RGB':fill,'PDFium_bounds':list(objects[0].get_bounds()),'source_paint_pixels_sha256':sha(mask.tobytes()),'pixel_mask_interior_not_assumed':True})
    negative=[p['panel_pixels']for p in classes if p['kind']=='source_inverted_panel_and_literal_grid_pixels']
    for region in mixed['regions']:
        for unit in region['source_bound_line_candidates']:
            x0,y0,x1,y1=pixels(unit['source_bbox_PDF_points']);height=y1-y0;width=x1-x0
            if width<15 or height<6:continue
            center=(x0+x1)/2,(y0+y1)/2;inverted_line=any(a<=center[0]<=c and b<=center[1]<=d for a,b,c,d in negative)
            glyph_ink=(gray>policy['source_dark_pixel_threshold'])if inverted_line else ink
            band=glyph_ink[y0+int(.35*height):y0+int(.65*height),x0:x1]
            if band.size and any(v>.8 for v in band.mean(axis=1)):raise ValueError('Possible raster source strike-through requires explicit review')
    residual=ink&~covered;_,_,stats,_=cv2.connectedComponentsWithStats(residual.astype('uint8'),8);unknown=[{'bbox_pixels':[int(x),int(y),int(x+w),int(y+h)],'ink_pixels':int(a)}for x,y,w,h,a in stats[1:]if a>policy['source_speck_max_pixels']]
    if unknown:raise ValueError('Mixed source pixels remain unaccounted: '+canonical(unknown[:10]))
    buf=BytesIO();im.save(buf,format='PNG')
    return {'render_dpi':dpi,'rendered_image_sha256':sha(buf.getvalue()),'render_size':list(im.size),'source_ink_pixels':int(ink.sum()),'native_glyph_mask_sha256':sha(native_mask.tobytes()),'raster_line_mask_sha256':sha(raster_mask.tobytes()),'literal_source_paint_mask_sha256':sha(paint.tobytes()),'remaining_unaccounted_ink_pixels':0,'source_paint_classes':classes,'policy_sha256':sha(canonical(policy))}

def copy_provenance(parent,applied,destination):
    """Keep original input bytes; only temporary locator copies are rebound."""
    destination=Path(destination);destination.mkdir()
    shutil.copytree(parent,destination/'parent-l1');shutil.copytree(applied,destination/'UI-applied')
    applied_receipt=load(Path(applied)/'applied-review-receipt.json');packet=Path(applied_receipt['source_packet_path']);shutil.copytree(packet,destination/'image-packet')
    dependencies=destination/'UI-source-files';dependencies.mkdir();locators={}
    for row in rows(Path(applied)/'UI-manifest-snapshot.jsonl'):
        for key in ('selected_text','independent_json','original_focus_crop'):
            origin=Path(row[key]);name=sha(origin.read_bytes())+origin.suffix;target=dependencies/name
            if not target.exists():target.write_bytes(origin.read_bytes())
            locators[str(origin)]=str(target.relative_to(destination))
    return {'source_packet_manifest_sha256':sha((packet/'source-regions-manifest.json').read_bytes()),'applied_review_receipt_sha256':sha((Path(applied)/'applied-review-receipt.json').read_bytes()),'locator_map':locators,'files':{str(p.relative_to(destination)):sha(p.read_bytes())for p in sorted(destination.rglob('*'))if p.is_file()}}

def reviewed_inputs(provenance,source_runtime,configuration):
    """Portable locator rebinding preserves all original PDF/page/crop hashes."""
    from .image_region_review import apply_ui_feedback
    provenance=Path(provenance)
    actual={str(p.relative_to(provenance))for p in provenance.rglob('*')if p.is_file()}
    if actual!=set(configuration['files']):raise ValueError('Mixed provenance artifact coverage changed')
    if any(sha((provenance/name).read_bytes())!=value for name,value in configuration['files'].items()):raise ValueError('Mixed original provenance bytes changed')
    original_packet=provenance/'image-packet';original_manifest=load(original_packet/'source-regions-manifest.json');original_model=load(provenance/'UI-applied/model-review.json')
    if sha((original_packet/'source-regions-manifest.json').read_bytes())!=configuration['source_packet_manifest_sha256']or original_model['source_packet_manifest_sha256']!=configuration['source_packet_manifest_sha256']:raise ValueError('Original image model review binds another source packet')
    parent=provenance/'parent-l1'
    with tempfile.TemporaryDirectory(prefix='portable-mixed-source-inputs-')as temporary:
        temp=Path(temporary);packet=temp/'packet';shutil.copytree(original_packet,packet)
        manifest=copy.deepcopy(original_manifest);manifest['parent_package_path']=str(parent.resolve());manifest['source_runtime']=str(Path(source_runtime).resolve());save(packet/'source-regions-manifest.json',manifest)
        model=copy.deepcopy(original_model);model['source_packet_path']=str(packet);model['source_packet_manifest_sha256']=sha((packet/'source-regions-manifest.json').read_bytes());save(temp/'model.json',model)
        ui=[]
        for row in rows(provenance/'UI-applied/UI-manifest-snapshot.jsonl'):
            revised=copy.deepcopy(row);revised['source_pdf']=str((parent/'original.pdf').resolve())
            for key in ('selected_text','independent_json','original_focus_crop'):revised[key]=str((provenance/configuration['locator_map'][row[key]]).resolve())
            ui.append(revised)
        write_rows(temp/'UI-manifest.jsonl',ui)
        result=apply_ui_feedback(packet,temp/'model.json',temp/'UI-manifest.jsonl',provenance/'UI-applied/UI-feedback-snapshot.jsonl',temp/'applied')
        if result['remaining_review_questions']or result['remaining_candidate_reader_records']:raise ValueError('Mixed source still has unanswered literal choices')
        mixed=load(temp/'applied/corrected-mixed-candidate.json')
        if mixed!=load(provenance/'UI-applied/corrected-mixed-candidate.json'):raise ValueError('Portable source locator replay changes corrected candidate content')
        return mixed,{'model_resolved_records':result['prior_model_resolved_records'],'UI_resolved_records':result['UI_resolved_reader_records'],'UI_questions_applied':result['unique_UI_questions_applied'],'reviewer_identity_authenticated':False,'source_packet_original_manifest_sha256':configuration['source_packet_manifest_sha256'],'locator_only_rebinding':True,'source_role_declaration':original_model.get('source_role','unreviewed_source_image_data')}

def derive_mixed(parent,provenance,source_runtime,provenance_configuration,page_number):
    import pdfplumber,pypdfium2 as pdfium
    from pypdf import PdfReader
    from .legal_l1 import verify_document
    from .quality_gate import load_policy
    from .text_script import native_font_script_evidence,native_script_verification
    verify_document(parent);mixed,review=reviewed_inputs(provenance,source_runtime,provenance_configuration)
    original=Path(parent)/'original.pdf';pdf_sha=sha(original.read_bytes());policy=load_policy();rule_path=Path(__file__).with_name('native_image_reconstruction_rules.json');rules=load(rule_path)
    with pdfplumber.open(original)as source,pdfium.PdfDocument(original)as alternate:
        native=source.pages[page_number-1];independent=alternate[page_number-1];tp=independent.get_textpage()
        try:
            font=native_font_script_evidence(native.chars,policy)
            if font['required_scripts']:raise ValueError('Mixed native font requires independent linguistic proof')
            if native.curves or native.lines:raise ValueError('Additional native vector objects need independent source proof')
            layout=PdfReader(original).pages[page_number-1].extract_text(extraction_mode='layout')or''
            old,native_lines=native_units(parent,page_number,native,tp,layout)
            images=mixed['regions']
            if any(r['native_page_text_sha256']!=old['text_sha256']for r in images)or len(images)!=len(native.images):raise ValueError('Mixed source image/native-page accounting differs')
            text,spans=reconstructed_text(native_lines,images)
            if native_script_verification(text,policy)['required_scripts']:raise ValueError('Reconstructed complex script requires independent raster linguistic proof')
            pixels=raster_proof(native,independent,mixed,policy,rules)
            if sum(p['kind']=='independently_bound_native_footer_fill'for p in pixels['source_paint_classes'])!=len(native.rects):raise ValueError('An original native paint primitive remains unexplained')
            proof={'kind':KIND,'source_pdf_sha256':pdf_sha,'page':page_number,'original_native_page_text_sha256':old['text_sha256'],'reconstructed_page_text_sha256':sha(text),'original_native_codepoints':len(old['text']),'reconstructed_codepoints':len(text),'source_native_line_count':len(native_lines),'source_image_region_count':len(images),'source_image_original_reader_records':sum(r['original_reader_record_count']for r in images),'source_image_corrected_literal_units':sum(len(r['source_bound_line_candidates'])for r in images),'codepoint_spans':spans,'pixels':pixels,'native_font_evidence':font,'review_basis':review,'rules_sha256':sha(rule_path.read_bytes()),'engine_sha256':sha(Path(__file__).read_bytes()),'offset_encoding':'unicode_codepoint','original_native_text_modified':False,'original_OCR_witnesses_modified':False,'table_semantics_reviewed':False,'legal_review':'not_run','publication_eligible':False,'source_unknown_fields':[],'structured_image_observations':mixed.get('current_structured_observations',[])}
            for observation in mixed.get('prior_model_visual_observations',[]):
                for value in observation.get('visible_transcription',[]):
                    for label in re.findall(r'\[([^\]]+)\]',value):
                        proof['source_unknown_fields'].append({'region_id':observation['region_id'],'source_crop_render_sha256':observation['crop_render_sha256'],'classification':'blank_or_obscured_in_original_source','model_observation':label,'value':None,'actual_client_fact':False,'must_not_infer_identity':True})
            for span in spans:
                if span['kind']=='reviewed_raster_literal':
                    span['source_role']='unverified_source_image_literal'
            proof['raster_table_candidates']=raster_table_records(mixed,spans,page_number,old['id'],pdf_sha,text)
            return text,proof
        finally:tp.close();independent.close()

def raster_table_records(mixed,spans,page,page_id,pdf_sha,text):
    """Keep literal grid cells with geometry/codepoint spans; semantics candidate."""
    result=[]
    for observation in mixed.get('current_structured_observations',[]):
        model=observation.get('table_candidate');region=next(r for r in mixed['regions']if r['region_id']==observation['region_id'])
        if not model:continue
        candidates=[s for s in spans if s['kind']=='reviewed_raster_literal'and s['region_id']==region['region_id']];columns=model['columns'];header=[]
        for name in columns:
            matches=[s for s in candidates if compact(s['exact_quote'])==compact(name)]
            if len(matches)!=1:raise ValueError('Raster table header literal is missing/ambiguous in source spans')
            header.append(matches[0])
        centers=[(s['source_bbox_PDF_points'][0]+s['source_bbox_PDF_points'][2])/2 for s in header]
        if centers!=sorted(centers)or len(set(centers))!=len(centers):raise ValueError('Raster table header order conflicts with source geometry')
        edges=[region['source_crop_bbox_PDF_points'][0],*[(a+b)/2 for a,b in zip(centers,centers[1:])],region['source_crop_bbox_PDF_points'][2]]
        def in_column(span,column):
            box=span['source_bbox_PDF_points'];return edges[column]<(box[0]+box[2])/2<edges[column+1]
        matrix=[header];roles=['header'];prior_bottom=max(s['source_bbox_PDF_points'][3]for s in header)
        specifications=[]
        if model.get('units'):specifications.append(('unit',[f'(in {unit})'for unit in model['units']]))
        if model.get('printed_numeric_literals'):specifications.append(('data',model['printed_numeric_literals']))
        specifications.extend(('data',row)for row in model.get('rows',[]))
        for role,values in specifications:
            if len(values)!=len(columns):raise ValueError('Reviewed raster row width differs from source columns')
            anchors=[s for s in candidates if compact(s['exact_quote'])==compact(values[0])and in_column(s,0)and s['source_bbox_PDF_points'][1]>=prior_bottom]
            if len(anchors)!=1:raise ValueError('Raster table row anchor is missing or repeated')
            anchor=anchors[0]['source_bbox_PDF_points'];row=[]
            for column,value in enumerate(values):
                matches=[]
                for span in candidates:
                    box=span['source_bbox_PDF_points']
                    if compact(span['exact_quote'])==compact(value)and in_column(span,column)and min(anchor[3],box[3])>max(anchor[1],box[1]):matches.append(span)
                if len(matches)!=1:raise ValueError('Raster table cell has ambiguous/missing exact source geometry')
                row.append(matches[0])
            matrix.append(row);roles.append(role);prior_bottom=max(s['source_bbox_PDF_points'][3]for s in row)
        cells=[];flat=[]
        for row_index,row in enumerate(matrix):
            cells.append([s['exact_quote']for s in row])
            for column,span in enumerate(row):
                if text[span['char_start']:span['char_start']+span['char_length']]!=span['exact_quote']:raise ValueError('Raster table cell quote is not in reconstructed page')
                flat.append({'row':row_index,'column':column,'row_role':roles[row_index],'bbox':span['source_bbox_PDF_points'],'text':span['exact_quote'],'page':page,'page_id':page_id,'page_text_sha256':sha(text),'char_start':span['char_start'],'char_length':span['char_length'],'exact_quote':span['exact_quote'],'source_crop_render_sha256':region['source_crop_render_sha256'],'source_reader_refs':span['original_reader_refs'],'semantic_state':'unresolved'})
        bbox=[min(c['bbox'][0]for c in flat),min(c['bbox'][1]for c in flat),max(c['bbox'][2]for c in flat),max(c['bbox'][3]for c in flat)];identity=sha(canonical({'pdf':pdf_sha,'page':page,'region':region['region_id'],'cells':flat}))
        result.append({'id':f'{page_id}:table-raster{len(result)+1}','table_version_id':identity,'page':page,'bbox':bbox,'bbox_coordinates':'PDF points, top-left','cells':cells,'cell_spans':flat,'row_roles':roles,'extraction_basis':'source_bound_mixed_raster_review','status':'needs_row_column_review','table_semantics_reviewed':False,'source_pdf_sha256':pdf_sha,'page_text_sha256':sha(text),'source_crop_render_sha256':region['source_crop_render_sha256'],'source_unknown_fields_not_filled':True})
    return result


def quality_from_mixed(parent,page_number,text,proof):
    original=next(q for q in load(Path(parent)/'quality.json')['results']if q['page']==page_number)
    if original['components']['source_identity']['state']!='pass'or proof['pixels']['remaining_unaccounted_ink_pixels']!=0 or sha(text)!=proof['reconstructed_page_text_sha256']:raise ValueError('Mixed reconstruction lacks required independent source identity/pixels')
    codepoints=proof['codepoint_spans'];cursor=0
    for span in codepoints:
        if span['char_start']!=cursor or text[cursor:cursor+span['char_length']]!=span['exact_quote']:raise ValueError('Mixed source span partition differs')
        cursor+=span['char_length']
    if cursor!=len(text):raise ValueError('Mixed source span partition is incomplete')
    receipt=sha(canonical(proof));components={
      'source_identity':{'state':'pass','basis':'original_PDF_and_immutable_native_parent_reverified','receipt_sha256':receipt},
      'text_content':{'state':'pass','basis':'additive_exact_native_and_reviewed_raster_literal_spans','chars':len(text),'explicit_source_unknown_fields':len(proof['source_unknown_fields']),'receipt_sha256':receipt},
      'language':{'state':'pass','detected_script':'latin','basis':'native_font_guard_and_English_raster_reader_visual_review','independent_model_confirmed':False,'receipt_sha256':receipt},
      'source_render':{'state':'pass','basis':'fresh_native_glyph_positive_and_inverse_raster_foreground_and_literal_source_paint','unaccounted_ink_pixels':0,'rendered_image_sha256':proof['pixels']['rendered_image_sha256'],'receipt_sha256':receipt},
      'words_and_order':{'state':'pass','basis':'independent_native_lines_and_source_geometry_bands_with_distinct_review_origins','receipt_sha256':receipt},
      'ordered_numbers':{'state':'pass','basis':'original_native_codepoints_and_reviewed_exact_raster_number_units','receipt_sha256':receipt},
      'ordered_operators':{'state':'pass','basis':'original_native_codepoints_and_reviewed_exact_raster_literal_units','receipt_sha256':receipt},
      'layout':{'state':'pass','basis':'nonoverlapping_source_native_lines_and_complete_image_regions_with_exact_spans','table_semantics_reviewed':False,'receipt_sha256':receipt}}
    return {**original,'source_pdf_sha256':proof['source_pdf_sha256'],'page_text_sha256':sha(text),'outcome':'auto_accepted','components':components,'page_complete':True,'missing_evidence':[],'failed_components':[],'unresolved_visual_regions':[],'acceptance_scope':'reviewed readable source text and complete source regions/pixels; source-obscured sample fields retained as unknown; no legal approval','mixed_source_reconstruction_receipt_sha256':receipt,'source_unknown_fields':proof['source_unknown_fields']}

def rewrite_extraction(stage,page_number,text,proof):
    """Build a NEW version; exact old native/OCR bytes remain in provenance."""
    from .pdf_ingest import FACT,text_quality
    from .evidence_package import create_package,verify_package
    from .structure_candidates import page_candidates,table_candidates
    extraction=Path(stage)/'extraction';pages=rows(extraction/'pages.jsonl');old=pages[page_number-1];method='mixed_native_and_reviewed_raster_regions'
    revised={**old,'text':text,'text_sha256':sha(text),'chars':len(text),'method':method,'selected_quality':text_quality(text),'status':'review_required','source_reconstruction':{'kind':KIND,'original_native_text_sha256':proof['original_native_page_text_sha256'],'proof_sha256':sha(canonical(proof)),'source_unknown_fields':proof['source_unknown_fields'],'actual_client_facts':False,'legal_review':'not_run'}}
    pages[page_number-1]=revised;write_rows(extraction/'pages.jsonl',pages)
    inputs=rows(extraction/'embedding-input.jsonl')
    for row in inputs:
        if row['metadata']['page']==page_number:
            row['text']=text;row['metadata']['page_text_sha256']=sha(text);row['metadata']['selected_method']=method
    write_rows(extraction/'embedding-input.jsonl',inputs)
    facts=[f for f in rows(extraction/'fact-candidates.jsonl')if f['page']!=page_number]
    for match in FACT.finditer(text):
        value=match.group().strip();kind=('currency'if re.match(r'₹|INR|Rs\.?',value,re.I)else'percentage'if'%'in value else'capacity'if re.search(r'\b[mk]w',value,re.I)else'duration'if re.search(r'\b(?:days?|months?|years?)\b',value,re.I)else'date')
        span=next((s for s in proof['codepoint_spans']if s['char_start']<=match.start()<s['char_start']+s['char_length']),None)
        facts.append({'page':page_number,'page_id':old['id'],'page_text_sha256':sha(text),'char_start':match.start(),'char_length':match.end()-match.start(),'kind':kind,'value_text':value,'context':text[max(0,match.start()-90):match.end()+90],'status':'unverified_candidate','source_region_kind':span['kind']if span else'unknown','actual_client_fact':False,'image_sample_data':span is not None and span['kind']=='reviewed_raster_literal'and proof['review_basis'].get('source_role_declaration')=='explanatory_sample_forms_not_client_data'})
    facts.sort(key=lambda f:(f['page'],f['char_start']));write_rows(extraction/'fact-candidates.jsonl',facts)
    structures=rows(extraction/'structure-candidates.jsonl')
    structures=[r for r in structures if not any(e.get('page')==page_number for e in r.get('evidence',[]))]+page_candidates(revised)
    tables=load(extraction/'tables.json');tables.extend(proof['raster_table_candidates']);save(extraction/'tables.json',tables)
    for table in proof['raster_table_candidates']:
        for cell in table['cell_spans']:
            evidence={'page':page_number,'page_id':old['id'],'page_text_sha256':sha(text),'char_start':cell['char_start'],'char_length':cell['char_length'],'quote':cell['exact_quote'],'offset_encoding':'codepoint','selected_method':method}
            structures.append({'kind':'table_cell','table_id':table['id'],'table_version_id':table['table_version_id'],'table_bbox':table['bbox'],'row':cell['row'],'column':cell['column'],'row_role':cell['row_role'],'proposed_header':cell['row_role']=='header','candidate_header_row':0,'value_text':cell['text'],'evidence':[evidence],'span_status':'exact_source_region_and_codepoints','review_reasons':['source_grid_literal_verified_header_semantics_unresolved'],'status':'review_required'})
    write_rows(extraction/'structure-candidates.jsonl',structures)
    report=load(extraction/'report.json');report.update(text_chars=sum(len(p['text'])for p in pages),numeric_candidates=len(facts),structure_candidates=len(structures),tables=len(tables),page_status_counts={status:sum(p['status']==status for p in pages)for status in ('ready','blank','review_required')});report.setdefault('issues',[]).append({'page':page_number,'kind':'additive_mixed_source_reconstruction','detail':'Original native text and OCR witnesses retained; source-bound image readings, exact spans and pixel proof in L1 layout; legal/semantic review separate'})
    save(extraction/'report.json',report);create_package(extraction);verify_package(extraction,source_pdf=Path(stage)/'original.pdf');return pages

def build_mixed_version(parent,source_runtime,applied,page_number,output):
    from .legal_l1 import verify_document,structure_document,validate_structure,readiness_ledger
    from .proof_identity import proof_configuration
    parent,source_runtime,applied,output=map(lambda p:Path(p).resolve(),(parent,source_runtime,applied,output))
    if output.exists()or output.is_relative_to(parent):raise ValueError('Choose a new immutable mixed output outside original source')
    verify_document(parent);output.mkdir(parents=True);dest=output/'packages'/parent.name;dest.parent.mkdir()
    with tempfile.TemporaryDirectory(prefix='.mixed-reconstruction-',dir=output)as temporary:
        stage=Path(temporary)/parent.name;shutil.copytree(parent,stage)
        provenance=copy_provenance(parent,applied,stage/'mixed-source-provenance')
        text,proof=derive_mixed(stage/'mixed-source-provenance/parent-l1',stage/'mixed-source-provenance',source_runtime,provenance,page_number)
        quality=quality_from_mixed(parent,page_number,text,proof)
        pages=rewrite_extraction(stage,page_number,text,proof)
        layout=rows(stage/'layout.jsonl')
        for row in layout:
            if row['page']!=page_number:continue
            row['page_text_sha256']=sha(text);row['selected_method']='mixed_native_and_reviewed_raster_regions';row['original_native_text_sha256']=proof['original_native_page_text_sha256'];row['mixed_source_reconstruction_proof']=proof
            row['alignment']=[{'kind':tag,'glyph_start':a,'glyph_end':b,'selected_start':c,'selected_end':d}for tag,a,b,c,d in SequenceMatcher(None,row['glyph_stream_text'],text,autojunk=False).get_opcodes()]
        write_rows(stage/'layout.jsonl',layout)
        source=load(stage/'source-receipt.json');config=proof_configuration('mixed_native_raster_reconstruction',parent,Path(__file__));config.update(source_runtime=str(source_runtime),page=page_number,provenance=provenance,original_native_text_modified=False,selected_representation_augmented=True)
        source['quality_repair_configuration']=config;save(stage/'source-receipt.json',source)
        structure=structure_document(pages,source['actual_sha256'],source['declared']['family']);save(stage/'structure.json',structure);checked=validate_structure(structure,pages,source['actual_sha256'])
        q=load(stage/'quality.json');q['results']=[quality if row['page']==page_number else row for row in q['results']];q['counts']={name:sum(r['outcome']==name for r in q['results'])for name in ('auto_accepted','repair_required','source_blocked')};save(stage/'quality.json',q)
        save(stage/'readiness.json',readiness_ledger(structure,q));write_rows(stage/'repair-queue.jsonl',[job for job in rows(stage/'repair-queue.jsonl')if job['page']!=page_number])
        validation=load(stage/'validation.json');validation['structure']=checked;save(stage/'validation.json',validation)
        manifest=load(stage/'l1-package.json');manifest['request_sha256']=sha(canonical({'parent':sha((parent/'l1-package.json').read_bytes()),'operation':KIND,'mixed_proof':sha(canonical(proof))}));manifest['artifacts']={name:sha((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest)
        verify_document(stage);verify_mixed_version(stage,source_runtime);stage.rename(dest)
    checked=verify_mixed_version(dest,source_runtime);receipt={'status':'passed','operation':KIND,'new_l1_package_path':str(dest),'new_l1_package_sha256':sha((dest/'l1-package.json').read_bytes()),'page':page_number,'source_pdf_sha256':proof['source_pdf_sha256'],'original_native_page_text_sha256':proof['original_native_page_text_sha256'],'reconstructed_page_text_sha256':proof['reconstructed_page_text_sha256'],'source_quality_pages_verified':checked['source_quality_pages_verified'],'reconstructed_codepoints':len(text),'original_native_text_modified':False,'original_OCR_witnesses_modified':False,'selected_representation_augmented':True,'source_unknown_fields':proof['source_unknown_fields'],'quality_counts':load(dest/'quality.json')['counts'],'publication_eligible':False,'legal_review':'not_run','model_calls':0,'remote_operations':0};save(output/'validation.json',receipt);return receipt

def verify_mixed_version(folder,source_runtime):
    from .legal_l1 import verify_document
    from .proof_identity import inspect_pdf2data_profile
    folder=Path(folder);verify_document(folder);source=load(folder/'source-receipt.json');config=source.get('quality_repair_configuration',{})
    if config.get('operation')!='mixed_native_raster_reconstruction':raise ValueError('Unregistered mixed reconstruction operation')
    current=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
    if config.get('proof_profile',{}).get('source_configuration_expected')!=current['source_configuration_expected']:raise ValueError('Mixed reconstruction proof execution profile changed')
    provenance=folder/'mixed-source-provenance';parent=provenance/'parent-l1';verify_document(parent)
    if sha((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']or(folder/'original.pdf').read_bytes()!=(parent/'original.pdf').read_bytes():raise ValueError('Original native parent/source PDF was changed')
    text,proof=derive_mixed(parent,provenance,source_runtime,config['provenance'],config['page']);pages=rows(folder/'extraction/pages.jsonl');original=rows(parent/'extraction/pages.jsonl')
    for before,after in zip(original,pages):
        if before['page']!=config['page']and before!=after:raise ValueError('Mixed reconstruction modifies an unrelated page')
    selected=next(p for p in pages if p['page']==config['page']);raw=next(r for r in rows(folder/'layout.jsonl')if r['page']==config['page'])
    if selected['text']!=text or selected['text_sha256']!=sha(text)or raw.get('mixed_source_reconstruction_proof')!=proof:raise ValueError('Reconstructed text/regions differ from current original source proof')
    fresh=quality_from_mixed(parent,config['page'],text,proof);q=load(folder/'quality.json');actual=next(p for p in q['results']if p['page']==config['page'])
    if fresh!=actual:raise ValueError('Mixed source quality differs from independently reconstructed proof')
    before=load(parent/'quality.json')['results']
    if any(a!=b for a,b in zip(before,q['results'])if a['page']!=config['page']):raise ValueError('Mixed reconstruction changes unrelated quality gates')
    return {'status':'verified','operation':'mixed_native_raster_reconstruction','source_quality_pages_verified':[config['page']],'original_native_text_modified':False,'original_OCR_witnesses_modified':False,'selected_representation_augmented':True,'source_codepoint_spans_verified':len(proof['codepoint_spans']),'unaccounted_ink_pixels':proof['pixels']['remaining_unaccounted_ink_pixels'],'source_unknown_fields_preserved':len(proof['source_unknown_fields']),'publication_eligible':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('parent','source-runtime','applied','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--page',type=int,required=True);a=p.parse_args();print(json.dumps(build_mixed_version(a.parent,a.source_runtime,a.applied,a.page,a.output)))
if __name__=='__main__':main()
