#!/usr/bin/env python3
"""Source-conserving mixed native/raster candidate regions and strict completeness.

The candidate sidecar preserves both raw readers and native page text unchanged.
It does not inject a transcription into the selected representation or approve a
sample document as law/client data. Existing CPU readings can be replayed without
model inference; original crops are regenerated for every validation.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,math,sys
from pathlib import Path
from io import BytesIO
import tempfile,shutil

def sha(data):return hashlib.sha256(data).hexdigest()
def file_sha(path):return sha(Path(path).read_bytes())
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def load(path):return json.loads(Path(path).read_text())
def rows(path):return [json.loads(line)for line in Path(path).read_text().splitlines()if line.strip()]
def validate_lines(lines):
    if not isinstance(lines,list)or not lines:raise ValueError('missing OCR lines')
    for line in lines:
        box=line.get('bbox');text=line.get('text')
        if not isinstance(text,str)or not text.strip():raise ValueError('empty OCR text')
        text.encode('utf-8')
        if not isinstance(box,list)or len(box)!=4 or any(isinstance(v,bool)or not isinstance(v,(int,float))or not math.isfinite(v) for v in box):raise ValueError('invalid normalized OCR geometry')
        # Vision returns tiny signed floating-point noise at the image edge.
        # Retain it literally; this tolerance never approves source quality.
        if not(-1e-8<=box[0]<box[2]<=1+1e-8 and -1e-8<=box[1]<box[3]<=1+1e-8):raise ValueError('OCR geometry outside exact crop')
        confidence=line.get('confidence',1)
        if not isinstance(confidence,(float,int))or not 0<=confidence<=1:raise ValueError('invalid OCR confidence')

def literal_geometry_agreements(left,right):
    """Candidate exact-literal alignments, never table associations/approval."""
    def overlap(a,b):
        w=max(0,min(a[2],b[2])-max(a[0],b[0]));h=max(0,min(a[3],b[3])-max(a[1],b[1]))
        intersection=w*h;union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
        return intersection/union if union>0 else 0
    result=[]
    for i,line in enumerate(left):
        matches=[j for j,other in enumerate(right)if line['text']==other['text']and overlap(line['bbox'],other['bbox'])>=.5]
        if len(matches)!=1:continue
        j=matches[0]
        reverse=[k for k,other in enumerate(left)if other['text']==right[j]['text']and overlap(other['bbox'],right[j]['bbox'])>=.5]
        if reverse!=[i]:continue
        result.append({'vision_line_index':i,'rapidocr_line_index':j,'literal':line['text'],'basis':'two_reader_literal_and_geometry_agreement_only','approved':False})
    return result

def source_regions(readings,items,*,table_reader=None):
    import pdfplumber
    from io import BytesIO
    if len({item['id']for item in items})!=len(items):raise ValueError('duplicate source region')
    lookup={item['id']:item for item in items}
    if {r['id']for r in readings['regions']}!=set(lookup)or len(readings['regions'])!=len(items):raise ValueError('region selection differs')
    result=[]
    for reading in readings['regions']:
        item=lookup[reading['id']];package=Path(item['current_package_path']);receipt=load(package/'source-receipt.json')
        pdf=package/'original.pdf';pdf_sha=file_sha(pdf)
        if pdf_sha!=item['source_pdf_sha256']or pdf_sha!=reading['source_pdf_sha256']:raise ValueError('original PDF identity differs')
        if file_sha(package/'l1-package.json')!=item['current_package_sha256']:raise ValueError('immutable package identity differs')
        page=next(p for p in rows(package/'extraction/pages.jsonl')if p['page']==item['page'])
        if sha(page['text'].encode())!=item['page_text_sha256']or page['text_sha256']!=item['page_text_sha256']or reading['page_text_sha256']!=page['text_sha256']:raise ValueError('selected native page identity differs')
        for key in ('page','original_crop_sha256','crop_source_bbox_PDF_points','render_dpi'):
            if item[key]!=reading[key]:raise ValueError('source crop request differs')
        bbox=item['crop_source_bbox_PDF_points'];dpi=item['render_dpi']
        if not isinstance(dpi,int)or not 120<=dpi<=360:raise ValueError('unbounded render')
        with pdfplumber.open(pdf)as opened:
            physical=opened.pages[item['page']-1]
            if len(bbox)!=4 or not(0<=bbox[0]<bbox[2]<=physical.width and 0<=bbox[1]<bbox[3]<=physical.height):raise ValueError('invalid source crop')
            images=[im for im in physical.images if all(abs(a-b)<=.02 for a,b in zip(bbox,[im['x0'],im['top'],im['x1'],im['bottom']]))]
            if len(images)!=1:raise ValueError('region is not one exact original embedded image')
            image=physical.crop(tuple(bbox)).to_image(resolution=dpi).original
            buffer=BytesIO();image.save(buffer,format='PNG');render_sha=sha(buffer.getvalue())
            if render_sha!=item['original_crop_sha256']or file_sha(item['original_crop_path'])!=render_sha:raise ValueError('original crop render differs')
            regions=[]
            for engine in ('vision_cpu','rapidocr_cpu'):
                lines=copy.deepcopy(reading[engine]['lines']);validate_lines(lines)
                for line in lines:
                    x0,y0,x1,y1=line['bbox']
                    line['source_bbox_PDF_points']=[bbox[0]+x0*(bbox[2]-bbox[0]),bbox[1]+y0*(bbox[3]-bbox[1]),bbox[0]+x1*(bbox[2]-bbox[0]),bbox[1]+y1*(bbox[3]-bbox[1])]
                regions.append({'reader':engine,'raw_reading_sha256':sha(canonical(reading[engine])),'lines':lines})
            tables=table_reader(image,reading['rapidocr_cpu']['lines'],page=item['page'],page_id=f'{pdf_sha[:16]}:p{item["page"]}:image{len(result)+1}',pdf_width=bbox[2]-bbox[0],pdf_height=bbox[3]-bbox[1])if table_reader else []
            for table in tables:
                table['bbox']=[table['bbox'][0]+bbox[0],table['bbox'][1]+bbox[1],table['bbox'][2]+bbox[0],table['bbox'][3]+bbox[1]]
                table['approval']='candidate_only_cell_disagreements_and_grid_slots_unresolved'
        result.append({'id':item['id'],'source_pdf_sha256':pdf_sha,'page':item['page'],'page_text_sha256':page['text_sha256'],'parent_package_sha256':item['current_package_sha256'],'source_crop_bbox_PDF_points':bbox,'source_crop_render_sha256':render_sha,'render_dpi':dpi,'image_object_width':images[0]['width'],'image_object_height':images[0]['height'],'native_selected_text':page['text'],'native_codepoint_length':len(page['text']),'readers':regions,'literal_geometry_agreements':literal_geometry_agreements(reading['vision_cpu']['lines'],reading['rapidocr_cpu']['lines']),'table_candidates':tables,'candidate_data_available':True,'source_revalidation':'exact_original_crop_reproduced','quality_clearance':False,'page_quality_preserved':True,'human_review':'not_inferred_from_reader_agreement','source_role':'unreviewed_original_image_region','source_role_hint':receipt.get('declared',{}).get('source_role_hint'),'actual_client_facts':False})
    return {'contract_version':'powerlex.embedded-source-regions.v1','regions':result,'source_packages_modified':False,'selected_text_modified':False,'source_quality_clearances':0,'publication_eligible':False,'remote_operations':0}

def read_cpu(items):
    """Read exact existing crops with cached CPU models; no weight download path."""
    import rapidocr
    import local_vector_batch.ocr as ocr
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec,OCRVersion,ModelType
    from PIL import Image
    root=Path(rapidocr.__file__).parent/'models'
    weights={'det':root/'PP-OCRv6_det_small.onnx','cls':root/'ch_ppocr_mobile_v2.0_cls_mobile.onnx','rec':root/'en_PP-OCRv5_rec_mobile.onnx'}
    if not all(path.is_file()for path in weights.values()):raise ValueError('all exact local CPU model weights required; downloads disabled')
    reader=RapidOCR(params={'Det.model_path':str(weights['det']),'Cls.model_path':str(weights['cls']),'Rec.model_path':str(weights['rec']),'Rec.lang_type':LangRec.EN,'Rec.ocr_version':OCRVersion.PPOCRV5,'Rec.model_type':ModelType.MOBILE,'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,'EngineConfig.onnxruntime.use_cuda':False,'EngineConfig.onnxruntime.use_coreml':False})
    binary=ocr._vision_binary();prior_reader=ocr._rapidocr_reader
    ocr._rapidocr_reader=lambda recognition_model='en':reader
    results=[]
    try:
        for item in items:
            crop=Path(item['original_crop_path'])
            if file_sha(crop)!=item['original_crop_sha256']:raise ValueError('crop identity changed before reading')
            run,wait,elapsed=ocr._run_vision(binary,crop,'cpu',('en-US',))
            if run.returncode:raise ValueError('CPU Vision reading failed')
            rapid,rapid_elapsed=ocr._rapidocr_lines(Image.open(crop))
            results.append({**{key:item[key]for key in ('id','source_pdf_sha256','page','page_text_sha256','original_crop_path','original_crop_sha256','crop_source_bbox_PDF_points','render_dpi')},'vision_cpu':{'lines':json.loads(run.stdout)['lines'],'elapsed_ms':elapsed,'wait_ms':wait},'rapidocr_cpu':{'lines':rapid,'elapsed_ms':rapid_elapsed},'status':'candidate_region_readings_not_quality_clearance'})
    finally:ocr._rapidocr_reader=prior_reader
    return {'regions':results,'vision_executable_sha256':file_sha(binary),'model_files':{key:{'path':str(path),'sha256':file_sha(path)}for key,path in weights.items()},'new_GPU_calls':0,'remote_operations':0,'publication_eligible':False}


SCHEMA='pdf2data.native-image-regions.v1'

def capture_requests(package,page_number,output,dpi=240):
    import pdfplumber
    package=Path(package).resolve();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    page=next((p for p in rows(package/'extraction/pages.jsonl')if p['page']==page_number),None)
    if isinstance(page_number,bool)or not isinstance(page_number,int)or page_number<1:raise ValueError('positive physical page integer required')
    if page is None or page.get('method')!='pdf_text':raise ValueError('mixed image lane requires an existing native page')
    pdf=package/'original.pdf';pdf_sha=file_sha(pdf);items=[]
    if not 120<=dpi<=360:raise ValueError('bounded source render DPI required')
    with pdfplumber.open(pdf)as opened:
        source=opened.pages[page_number-1]
        for index,im in enumerate(source.images):
            box=[round(float(im[key]),8)for key in ('x0','top','x1','bottom')]
            path=output/f'region-{index}.png'
            source.crop(tuple(box)).to_image(resolution=dpi).original.save(path)
            items.append({'id':f'{pdf_sha[:16]}:p{page_number}:image{index}','current_package_path':str(package),'current_package_sha256':file_sha(package/'l1-package.json'),'source_pdf_sha256':pdf_sha,'page':page_number,'page_text_sha256':page['text_sha256'],'original_crop_path':str(path.resolve()),'original_crop_sha256':file_sha(path),'crop_source_bbox_PDF_points':box,'render_dpi':dpi})
    if not items:raise ValueError('selected page has no embedded image objects')
    return items

def bind_readings(readings,items):
    """Every actual image must have exactly one matching raw reading pair."""
    result=copy.deepcopy(readings);selected=[]
    for item in items:
        matches=[r for r in result['regions']if r.get('source_pdf_sha256')==item['source_pdf_sha256']and r.get('page')==item['page']and r.get('page_text_sha256')==item['page_text_sha256']and r.get('original_crop_sha256')==item['original_crop_sha256']and r.get('render_dpi')==item['render_dpi']and r.get('crop_source_bbox_PDF_points')==item['crop_source_bbox_PDF_points']]
        if len(matches)!=1:raise ValueError('missing, duplicate or changed original-image reading')
        matching=matches[0];matching['id']=item['id'];selected.append(matching)
    if len(selected)!=len(result['regions']):raise ValueError('reading outside complete selected-page image scope')
    result['regions']=selected;return result

def assess_completeness(regions,quality):
    """Candidate inclusion is distinct from complete selected-text source proof."""
    page=quality['results'][0]
    conflicts=[]
    for region in regions['regions']:
        left,right=region['readers'];matched=region['literal_geometry_agreements']
        used_left={r['vision_line_index']for r in matched};used_right={r['rapidocr_line_index']for r in matched}
        for reader,used in ((left,used_left),(right,used_right)):
            for index,line in enumerate(reader['lines']):
                if index not in used:conflicts.append({'region_id':region['id'],'reader':reader['reader'],'line_index':index,'literal':line['text'],'source_bbox_PDF_points':line['source_bbox_PDF_points'],'reason':'no_unique_exact_literal_geometric_agreement'})
    # Source raster glyphs, visual ink and table slots are not explained merely
    # by agreement. This remains unknown even if the raw OCR strings all match.
    gates={'native_page_quality':{'state':'pass'if page['outcome']=='auto_accepted'else 'fail','result_sha256':sha(canonical(page))},'all_embedded_images_captured':{'state':'pass','count':len(regions['regions'])},'raster_reader_literal_geometry':{'state':'fail'if conflicts else 'pass','unresolved_reading_count':len(conflicts)},'raster_source_visual_coverage':{'state':'unknown','reason':'region_reader_agreement_does_not_prove_complete_raster_glyph_coverage'},'raster_table_and_field_associations':{'state':'unknown','reason':'independent_grid_slot_or_field_association_proof_required'},'mixed_native_raster_order':{'state':'unknown','reason':'region_positions_preserved_no_single_reading_order_approved'}}
    return {'schema_version':SCHEMA,'gates':gates,'unresolved_readings':conflicts,'candidate_data_available':True,'source_quality_clearances':0,'page_complete':False,'embedding_candidate_allowed':False,'publication_eligible':False,'reason':'source_regions_are_present_but_unresolved_content_or_layout_is_held'}

def derive(package,source_runtime,page_number,readings,working):
    from .proof_identity import verify_l1_source_readiness
    from .quality_gate import assess_package
    from .ocr_tables import ruled_table_candidates
    checked=verify_l1_source_readiness(package,source_runtime)
    if not checked['source_extraction_profile_matches']:raise ValueError('original extraction runtime mismatch')
    items=capture_requests(package,page_number,working)
    bound=bind_readings(readings,items)
    regions=source_regions(bound,items,table_reader=ruled_table_candidates)
    quality=assess_package(Path(package)/'extraction',Path(package)/'original.pdf',pages=[page_number],derive_ocr_source_raster=True)
    if quality.get('packages_blocked')or len(quality['results'])!=1:raise ValueError('fresh original whole-page quality unavailable')
    return {'representation':regions,'fresh_native_quality':quality,'completeness':assess_completeness(regions,quality)}

def build(package,source_runtime,page_number,output,readings_path=None):
    package=Path(package).resolve();source_runtime=Path(source_runtime).resolve();output=Path(output).resolve()
    if output.exists()or output.is_relative_to(package):raise ValueError('new immutable output outside original package required')
    output.mkdir(parents=True)
    try:
        crops=output/'original-regions';items=capture_requests(package,page_number,crops)
        readings=load(readings_path)if readings_path else read_cpu(items)
        # Raw readings and their disagreements are retained in full.
        (output/'raw-readings.json').write_text(json.dumps(readings,ensure_ascii=False,indent=2)+'\n')
        derived=derive(package,source_runtime,page_number,readings,crops)
        for key,value in derived.items():(output/f'{key}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        manifest={'schema_version':SCHEMA,'parent_package_path':str(package),'parent_package_sha256':file_sha(package/'l1-package.json'),'source_runtime':str(source_runtime),'source_pdf_sha256':file_sha(package/'original.pdf'),'page':page_number,'proof_module_sha256':file_sha(__file__),'artifacts':{str(p.relative_to(output)):file_sha(p)for p in sorted(output.rglob('*'))if p.is_file()},'source_packages_modified':False,'selected_native_text_modified':False,'source_quality_clearances':0,'publication_eligible':False}
        (output/'source-regions-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        receipt=verify(output)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
        return receipt
    except Exception:
        shutil.rmtree(output)
        raise

def verify(output):
    output=Path(output);manifest=load(output/'source-regions-manifest.json')
    if any(manifest.get(key)is not False for key in ('source_packages_modified','selected_native_text_modified','publication_eligible'))or manifest.get('source_quality_clearances')!=0:raise ValueError('mixed image candidate cannot claim source approval')
    if manifest.get('schema_version')!=SCHEMA or manifest.get('proof_module_sha256')!=file_sha(__file__):raise ValueError('mixed-image proof engine mismatch')
    expected=set(manifest['artifacts']);actual={str(p.relative_to(output))for p in output.rglob('*')if p.is_file()}-{'source-regions-manifest.json','verification.json'}
    if expected!=actual:raise ValueError('image proof artifact coverage differs')
    for name,digest in manifest['artifacts'].items():
        relative=Path(name)
        if relative.is_absolute()or'..'in relative.parts or file_sha(output/relative)!=digest:raise ValueError('image proof artifact differs')
    parent=Path(manifest['parent_package_path'])
    if file_sha(parent/'l1-package.json')!=manifest['parent_package_sha256']or file_sha(parent/'original.pdf')!=manifest['source_pdf_sha256']:raise ValueError('mixed image original parent changed')
    with tempfile.TemporaryDirectory(prefix='native-image-source-reproof-')as temp:
        derived=derive(parent,Path(manifest['source_runtime']),manifest['page'],load(output/'raw-readings.json'),Path(temp))
        for key,value in derived.items():
            if value!=load(output/f'{key}.json'):raise ValueError('mixed-image source representation/proof differs: '+key)
        # Compare render bytes, not only a caller-resealed artifact manifest.
        for fresh in Path(temp).glob('*.png'):
            if fresh.read_bytes()!=(output/'original-regions'/fresh.name).read_bytes():raise ValueError('regenerated source crop differs')
    return {'status':'source_regions_verified_with_completeness_holds','schema_version':SCHEMA,'source_pdf_sha256':manifest['source_pdf_sha256'],'page':manifest['page'],'image_regions':len(derived['representation']['regions']),'source_quality_clearances':0,'page_complete':False,'embedding_candidate_allowed':False,'unresolved_readings':len(derived['completeness']['unresolved_readings']),'source_packages_modified':False,'selected_native_text_modified':False,'publication_eligible':False}
