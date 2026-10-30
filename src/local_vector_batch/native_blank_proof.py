"""Immutable L1 quality proof for exact-white physical pages, never empty vectors.

The older blank_page_repair commit API mutates extraction status and uses gray
thresholds. This separate proof requires every rendered RGB component to be255,
both fresh source readers empty, zero native character objects, and a complete
uncropped physical viewport. Original extraction bytes remain unchanged.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile

from .proof_identity import canonical,inspect_pdf2data_profile,proof_configuration,verify_unchanged_quality_pages

OPERATION='native_exact_white_blank'


def digest(value):return sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def inspect_exact_white(source,page,selected_text,source_sha256=None):
    """Fresh original-source inspection. Nearwhite/scans/text never qualify."""
    import numpy as np
    import pdfplumber
    import pypdfium2 as pdfium
    source=Path(source);actual=digest(source.read_bytes())
    if source_sha256 is not None and actual!=source_sha256:raise ValueError('Blank proof source PDF hash mismatch')
    if type(page)is not int or page<1:raise ValueError('Invalid blank physical page')
    with pdfplumber.open(source)as native_pdf,pdfium.PdfDocument(source)as pdf:
        if page>len(pdf)or len(pdf)!=len(native_pdf.pages):raise ValueError('Blank physical page is absent')
        native=native_pdf.pages[page-1];physical=pdf[page-1];text_page=physical.get_textpage()
        try:
            native_text=native.extract_text()or'';other=text_page.get_text_range()
            media=list(physical.get_mediabox());crop=list(physical.get_cropbox())
            image=physical.render(scale=180/72,draw_annots=True).to_pil().convert('RGB');pixels=np.asarray(image)
            nonwhite=int(np.count_nonzero(np.any(pixels!=255,axis=2)));png=BytesIO();image.save(png,format='PNG')
            reasons=[]
            checks={'selected_text_contains_no_content':not selected_text.strip(),
                    'fresh_native_text_empty':native_text=='','fresh_pdfium_text_empty':other=='',
                    'native_character_objects_absent':len(native.chars)==0,
                    'pdfium_character_objects_absent':text_page.count_chars()==0,
                    'entire_rgb_render_exact_white':nonwhite==0,'physical_media_and_crop_boxes_equal':media==crop}
            reasons=[key for key,passed in checks.items()if not passed]
            return {'kind':'pdf2data.exact-white-physical-page.v1','source_pdf_sha256':actual,'page':page,
                    'page_text_sha256':digest(selected_text),'status':'source_proven_exact_white_blank'if not reasons else'held',
                    'checks':checks,'failed_checks':reasons,'render_dpi':180,'render_mode':'RGB','draw_annotations':True,
                    'render_rgb_sha256':digest(pixels.tobytes()),'render_png_sha256':digest(png.getvalue()),
                    'render_size':list(image.size),'nonwhite_rgb_pixels':nonwhite,'rgb_white_value_required':255,
                    'media_box':media,'crop_box':crop,'rotation':physical.get_rotation(),
                    'native_character_objects':len(native.chars),'pdfium_character_objects':text_page.count_chars(),
                    'native_text_length':len(native_text),'pdfium_text_length':len(other),
                    'native_text_sha256':digest(native_text.encode('utf-8','surrogatepass')),
                    'pdfium_text_sha256':digest(other.encode('utf-8','surrogatepass')),
                    'reader_hash_encoding':'utf8_surrogatepass; any reader character prevents blank approval',
                    'source_objects':{name:len(getattr(native,name))for name in ('images','rects','lines','curves')},
                    'source_text_modified':False,'embedding_candidate_allowed':False,'legal_review':'not_run',
                    'publication_eligible':False,'engine_sha256':digest(Path(__file__).read_bytes())}
        finally:text_page.close();physical.close()


def blank_quality(proof):
    if (proof['status']!='source_proven_exact_white_blank'or not all(proof['checks'].values())or
            proof.get('render_dpi')!=180 or proof.get('render_mode')!='RGB'or proof.get('draw_annotations')is not True or
            proof.get('nonwhite_rgb_pixels')!=0 or proof.get('rgb_white_value_required')!=255 or
            any(proof.get(k)!=0 for k in ('native_character_objects','pdfium_character_objects','native_text_length','pdfium_text_length'))or
            proof.get('media_box')!=proof.get('crop_box')):
        raise ValueError('Physical page is not proven exactly white and reader-empty')
    identity=digest(canonical(proof));components={}
    bases={'source_identity':'exact_original_PDF_and_physical_page_identity',
           'text_content':'source_proven_empty_physical_page_no_textual_claim',
           'language':'no_source_character_objects_or_reader_content; no_linguistic_claim',
           'source_render':'every_original_fullpage_RGB_component255_at180dpi_annotations_rendered',
           'words_and_order':'both_fresh_source_readers_empty',
           'ordered_numbers':'both_fresh_source_readers_empty_no_numeric_claim',
           'ordered_operators':'both_fresh_source_readers_empty_no_operator_claim',
           'layout':'uncropped_whole_physical_page_exact_white_no_layout_content'}
    for key,basis in bases.items():components[key]={'state':'pass','basis':basis,'receipt_sha256':identity}
    return {'source_pdf_sha256':proof['source_pdf_sha256'],'page':proof['page'],'page_text_sha256':proof['page_text_sha256'],
            'outcome':'auto_accepted','page_complete':True,'text_region_verified':True,'components':components,
            'missing_evidence':[],'failed_components':[],'unresolved_visual_regions':[],'source_style_regions':[],
            'verified_blank_physical_page':True,'embedding_candidate_allowed':False,
            'acceptance_scope':'source_verified_empty_physical_page_only; no_text_or_legal_assertion'}


def create_blank_version(parent,source_runtime,pages,output):
    from .legal_l1 import verify_document,rows,write_rows,readiness_ledger
    parent,source_runtime,output=map(lambda p:Path(p).resolve(),(parent,source_runtime,output))
    if output.exists():raise ValueError('Choose a new immutable blank-proof output')
    numbers=list(pages)
    if not numbers or len(numbers)>100 or len(numbers)!=len(set(numbers)):raise ValueError('Distinct bounded physical blank pages required')
    verify_document(parent);source=read(parent/'source-receipt.json')
    if source.get('quality_repair_configuration'):raise ValueError('Nested source-quality proof chains need an explicit merged decoder')
    expected=inspect_pdf2data_profile(source_runtime)['source_configuration_expected']
    if any(source['configuration'].get(k)!=v for k,v in expected.items()):raise ValueError('Original blank-source extraction profile differs')
    selected={p['page']:p for p in rows(parent/'extraction/pages.jsonl')};proofs=[];accepted={}
    for n in sorted(numbers):
        if n not in selected:raise ValueError('Blank physical page is absent from source L1')
        proof=inspect_exact_white(parent/'original.pdf',n,selected[n]['text'],source['actual_sha256'])
        if proof['page_text_sha256']!=selected[n]['text_sha256']:raise ValueError('Blank selected page hash changed')
        if inspect_exact_white(parent/'original.pdf',n,selected[n]['text'],source['actual_sha256'])!=proof:raise ValueError('Exact-white physical proof did not reproduce')
        proofs.append(proof)
        if proof['status']=='source_proven_exact_white_blank':accepted[n]=blank_quality(proof)
    output.mkdir(parents=True);save(output/'page-proofs.json',proofs)
    config=proof_configuration(OPERATION,parent,Path(__file__));config['source_runtime']=str(source_runtime)
    result={'contract_version':'pdf2data.immutable-exact-white-quality.v1','status':'held'if not accepted else'passed',
            'parent_l1_package_path':str(parent),'parent_manifest_sha256':digest((parent/'l1-package.json').read_bytes()),
            'source_pdf_sha256':source['actual_sha256'],'requested_pages':sorted(numbers),'physical_blank_pages_verified':sorted(accepted),
            'still_held_pages':[p['page']for p in proofs if p['status']!='source_proven_exact_white_blank'],
            'embedding_candidates_created':0,'source_files_modified':False,'source_text_modified':False,
            'model_calls':0,'remote_operations':0,'legal_review':'not_run','publication_eligible':False}
    if accepted:
        dest=output/'packages'/parent.name;dest.parent.mkdir()
        with tempfile.TemporaryDirectory(prefix='.exact-white-',dir=output)as tmp:
            stage=Path(tmp)/parent.name;shutil.copytree(parent,stage);raw=rows(stage/'layout.jsonl')
            for page in raw:
                if page['page']in accepted:
                    page['exact_white_blank_proof']=next(p for p in proofs if p['page']==page['page'])
                    page['quality_repair_configuration']=config
            write_rows(stage/'layout.jsonl',raw);quality=read(stage/'quality.json')
            quality['results']=[accepted.get(q['page'],q)for q in quality['results']]
            quality['counts']={state:sum(q['outcome']==state for q in quality['results'])for state in ('auto_accepted','repair_required','source_blocked')}
            save(stage/'quality.json',quality);save(stage/'readiness.json',readiness_ledger(read(stage/'structure.json'),quality))
            write_rows(stage/'repair-queue.jsonl',[q for q in rows(stage/'repair-queue.jsonl')if q['page']not in accepted])
            sr=read(stage/'source-receipt.json');sr['quality_repair_configuration']=config;save(stage/'source-receipt.json',sr)
            manifest=read(stage/'l1-package.json');manifest['request_sha256']=digest(canonical({'parent':result['parent_manifest_sha256'],'operation':OPERATION,'proofs':digest(canonical(proofs))}))
            manifest['artifacts']={name:digest((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest)
            verify_document(stage)
            if(stage/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes():raise ValueError('Blank proof altered original extraction status/text')
            stage.rename(dest)
        result['new_l1_package_path']=str(dest);result['new_l1_package_sha256']=digest((dest/'l1-package.json').read_bytes())
        result['fresh_decoder']=verify_blank_version(dest,source_runtime)
    result['signature']=digest(canonical(result));save(output/'validation.json',result);return result


def verify_blank_version(folder,source_runtime):
    from .legal_l1 import verify_document,rows
    folder=Path(folder);verify_document(folder);config=read(folder/'source-receipt.json')['quality_repair_configuration'];parent=Path(config['parent_l1_package_path']);verify_document(parent)
    if config['operation']!=OPERATION or digest((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']:raise ValueError('Blank-proof parent identity changed')
    current=proof_configuration(OPERATION,parent,Path(__file__))
    if current['proof_profile']['source_configuration_expected']!=config['proof_profile']['source_configuration_expected']:raise ValueError('Blank proof execution profile changed')
    original=read(parent/'source-receipt.json');origin=inspect_pdf2data_profile(source_runtime)['source_configuration_expected']
    if any(original['configuration'].get(k)!=v for k,v in origin.items()):raise ValueError('Blank proof original extraction epoch differs')
    pages={p['page']:p for p in rows(parent/'extraction/pages.jsonl')};quality={q['page']:q for q in read(folder/'quality.json')['results']};verified=[]
    for row in rows(folder/'layout.jsonl'):
        if 'exact_white_blank_proof'not in row:continue
        n=row['page'];proof=inspect_exact_white(parent/'original.pdf',n,pages[n]['text'],original['actual_sha256'])
        if proof!=row['exact_white_blank_proof']or quality[n]!=blank_quality(proof):raise ValueError('Exact-white source proof or derived quality differs')
        verified.append(n)
    if not verified:raise ValueError('No source-proven blank physical pages')
    verify_unchanged_quality_pages(folder,parent,verified)
    if(folder/'original.pdf').read_bytes()!=(parent/'original.pdf').read_bytes()or(folder/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes():raise ValueError('Blank proof changed source PDF/extraction bytes')
    return {'status':'verified','operation':OPERATION,'source_quality_pages_verified':sorted(verified),
            'blank_physical_pages':sorted(verified),'embedding_candidate_allowed':False,'embedding_candidates_created':0,
            'source_text_modified':False,'source_files_modified':False,'publication_eligible':False}
