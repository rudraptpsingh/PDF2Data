"""Promote a processed targeted CPU OCR extraction to a NEW source-bound L1.

Original source/native readers and all unselected pages survive unchanged.
The decoder re-renders source, checks saved independent/model/config identity,
and reruns all normal gates; it does not rerun OCR or approve legal meaning.
"""
from difflib import SequenceMatcher
import argparse,hashlib,io,json,shutil,tempfile
from pathlib import Path


def sha(v):return hashlib.sha256(v if isinstance(v,bytes)else v.encode()).hexdigest()
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def load(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def rows(p):return[json.loads(l)for l in Path(p).read_text().splitlines()if l.strip()]
def lines(p,data):Path(p).write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n'for v in data))


def derive(folder):
    import pdfplumber,rapidocr
    from .ocr_policy_selection import select
    from .quality_gate import assess_package
    from .pdf_repair import _page_rows
    folder=Path(folder);config=load(folder/'source-receipt.json')['quality_repair_configuration'];proof=folder/'OCR-source-provenance'
    parent=proof/'parent-l1';result=load(proof/'result.json');number=result['page'];pdf_sha=sha((folder/'original.pdf').read_bytes())
    before={r['page']:r for r in rows(parent/'extraction/pages.jsonl')};after={r['page']:r for r in rows(folder/'extraction/pages.jsonl')}
    if (number!=config['page']or result.get('status')!='processed'or result.get('quality_outcome')!='auto_accepted'or
        result['source_pdf_sha256']!=pdf_sha or before[number]['text'].strip()or before[number]['text_sha256']!=result['original_page_text_sha256']):
        raise ValueError('Targeted OCR parent/source/empty-page scope changed')
    if set(before)!=set(after)or any(before[n]!=after[n]for n in before if n!=number):raise ValueError('OCR changed an unselected physical page')
    for name in('embedding-input.jsonl','fact-candidates.jsonl','structure-candidates.jsonl','tables.json'):
        old,new=_page_rows(parent/'extraction',name),_page_rows(folder/'extraction',name)
        if any(old.get(n,[])!=new.get(n,[])for n in before if n!=number):raise ValueError('OCR changed unselected source records: '+name)
    selected=after[number];independent=load(proof/'independent-reading.json');orientation=load(proof/'orientation-review.json')
    if selected['text_sha256']!=result['new_page_text_sha256']:raise ValueError('Processed OCR selected text identity changed')
    if independent.get('engine')!='vision'or independent.get('selected_page_text_sha256')!=selected['text_sha256']:
        raise ValueError('Independent reader/selected source binding differs')
    if independent.get('text_sha256')!=sha(independent['text']):raise ValueError('Independent reading hash differs')
    with pdfplumber.open(folder/'original.pdf')as pdf:
        if independent.get('source_pdf_rotation_degrees')is not None and independent['source_pdf_rotation_degrees']!=pdf.pages[number-1].rotation:
            raise ValueError('Declared OCR candidate PDF rotation differs from original')
        image=pdf.pages[number-1].to_image(resolution=independent['render_dpi']).original
        buffer=io.BytesIO();image.save(buffer,format='PNG');pixels=buffer.getvalue()
        if independent.get('source_image_dimensions')is not None and independent['source_image_dimensions']!=list(image.size):
            raise ValueError('OCR reader geometry source dimensions differ')
    if sha(pixels)!=independent.get('source_image_sha256')or pixels!=(proof/'independent-original-page.png').read_bytes():
        raise ValueError('Actual original OCR source raster changed')
    selection=select(independent,source_pdf_sha256=pdf_sha,page=number,source_image_sha256=sha(pixels),orientation_review=orientation)
    if selection!=load(proof/'OCR-selection.json'):raise ValueError('OCR script/orientation selection differs from source receipts')
    model_name='en_PP-OCRv5_rec_mobile'if selection['ocr_language']=='eng'else'devanagari_PP-OCRv5_rec_mobile'
    ocr=selected.get('ocr')or{};model_path=Path(rapidocr.__file__).parent/'models'/(model_name+'.onnx')
    if (selected['method']!='rapidocr'or ocr.get('engine')!='rapidocr'or ocr.get('device_used')!='cpu'or
        ocr.get('recognition_model')!=model_name or ocr.get('recognition_model_sha256')!=sha(model_path.read_bytes())or
        ocr.get('angle_classifier_requested')!=selection['ocr_use_angle_classifier']):
        raise ValueError('Selected actual OCR model/configuration identity differs')
    heading=config.get('ocr_heading_row_order');marker=ocr.get('source_bound_heading_row_order')
    if heading or marker:
        if not heading or not marker:raise ValueError('Heading transform lacks registered raw-provider subproof')
        from .ocr_heading_rows import derive as derive_heading
        raw=load(proof/'heading-original-page.json');raw_independent=load(proof/'heading-original-independent.json')
        regenerated=derive_heading(raw,raw_independent,folder/'original.pdf')
        expected_independent={**raw_independent,'selected_page_text_sha256':selected['text_sha256']}
        if (selected!=regenerated['proposed_page']or independent!=expected_independent or
            regenerated['proof']!=load(proof/'heading-row-proof.json')):
            raise ValueError('Heading source geometry/order/raw readings differ from fresh derivation')
    metadata={**independent,'version':'macOS_AppleVision_system_API'}
    quality=assess_package(folder/'extraction',folder/'original.pdf',pages=[number],
        independent_by_page={number:independent['text']},independent_metadata_by_page={number:metadata},derive_ocr_source_raster=True)
    actual=quality['results'][0]
    if actual['outcome']!='auto_accepted'or any(v['state']!='pass'for v in actual['components'].values()):
        raise ValueError('Targeted OCR still fails original whole-page gates')
    return number,actual,selection


def build(parent,source_runtime,experiment,orientation_review,output):
    from .legal_l1 import verify_document,structure_document,validate_structure,readiness_ledger
    from .proof_identity import proof_configuration,inspect_pdf2data_profile
    from .evidence_package import verify_package
    parent,source_runtime,experiment,output=map(lambda p:Path(p).resolve(),(parent,source_runtime,experiment,output))
    if output.exists()or output.is_relative_to(parent):raise ValueError('New OCR L1 version must be outside original package')
    verify_document(parent);result=load(experiment/'result.json')
    if result.get('status')!='processed'or result.get('quality_outcome')!='auto_accepted':raise ValueError('Only already-processed all8-passing OCR pages can enter this route')
    original=load(parent/'source-receipt.json');expected=inspect_pdf2data_profile(source_runtime)['source_configuration_expected']
    if any(original['configuration'].get(k)!=v for k,v in expected.items()):raise ValueError('Original extraction runtime identity differs')
    output.mkdir(parents=True);dest=output/'packages'/parent.name;dest.parent.mkdir()
    with tempfile.TemporaryDirectory(prefix='.targeted-ocr-',dir=output)as temp:
        stage=Path(temp)/parent.name;shutil.copytree(parent,stage)
        provenance=stage/'OCR-source-provenance';provenance.mkdir();shutil.copytree(parent,provenance/'parent-l1')
        for name in('result.json','independent-reading.json','independent-original-page.png','OCR-selection.json','quality.json'):
            shutil.copyfile(experiment/name,provenance/name)
        heading_files=('heading-original-page.json','heading-original-independent.json','heading-row-proof.json')
        heading_present=[(experiment/name).is_file()for name in heading_files]
        if any(heading_present):
            if not all(heading_present)or result.get('representation_repair_kind')!='same_baseline_heading_label_order':
                raise ValueError('Complete typed heading raw-provider evidence required')
            for name in heading_files:shutil.copyfile(experiment/name,provenance/name)
        save(provenance/'orientation-review.json',orientation_review)
        shutil.rmtree(stage/'extraction');shutil.copytree(experiment/'extraction',stage/'extraction')
        verify_package(stage/'extraction',source_pdf=stage/'original.pdf')
        config=proof_configuration('targeted_cpu_ocr',parent,Path(__file__))
        config.update(source_runtime=str(source_runtime),page=result['page'],
            portable_parent_path='OCR-source-provenance/parent-l1',
            original_native_readers_modified=False,selected_page_representation_changed=True,
            provenance_files={str(p.relative_to(stage)):sha(p.read_bytes())for p in sorted(provenance.rglob('*'))if p.is_file()})
        if all(heading_present):config['ocr_heading_row_order']={'kind':'same_baseline_heading_label_order','raw_provider_page':'OCR-source-provenance/heading-original-page.json','raw_independent_reading':'OCR-source-provenance/heading-original-independent.json','source_geometric_order_proof':'OCR-source-provenance/heading-row-proof.json'}
        source=load(stage/'source-receipt.json');source['quality_repair_configuration']=config;save(stage/'source-receipt.json',source)
        number,quality,selection=derive(stage)
        selected_pages=rows(stage/'extraction/pages.jsonl');selected=next(r for r in selected_pages if r['page']==number)
        layout=rows(stage/'layout.jsonl');independent=load(provenance/'independent-reading.json')
        for row in layout:
            if row['page']!=number:continue
            row.update(page_text_sha256=selected['text_sha256'],selected_method=selected['method'],
                quality_independent_basis='actual_AppleVision_original_page_independent_of_selected_RapidOCR',
                quality_independent_text=independent['text'],quality_independent_text_sha256=sha(independent['text']),
                original_native_page_text_sha256=result['original_page_text_sha256'],
                targeted_cpu_OCR_source_proof={'selection':selection,'independent_receipt_sha256':sha((provenance/'independent-reading.json').read_bytes()),'source_image_sha256':independent['source_image_sha256']})
            row['alignment']=[{'kind':kind,'glyph_start':a,'glyph_end':b,'selected_start':c,'selected_end':d}
                for kind,a,b,c,d in SequenceMatcher(None,row['glyph_stream_text'],selected['text'],autojunk=False).get_opcodes()]
        lines(stage/'layout.jsonl',layout)
        structure=structure_document(selected_pages,source['actual_sha256'],source['declared']['family']);save(stage/'structure.json',structure)
        checked=validate_structure(structure,selected_pages,source['actual_sha256'])
        q=load(stage/'quality.json');q['results']=[quality if r['page']==number else r for r in q['results']]
        q['counts']={name:sum(r['outcome']==name for r in q['results'])for name in('auto_accepted','repair_required','source_blocked')};save(stage/'quality.json',q)
        save(stage/'readiness.json',readiness_ledger(structure,q));lines(stage/'repair-queue.jsonl',[j for j in rows(stage/'repair-queue.jsonl')if j['page']!=number])
        validation=load(stage/'validation.json');validation['structure']=checked;save(stage/'validation.json',validation)
        manifest=load(stage/'l1-package.json');manifest['request_sha256']=sha(canonical({'operation':'targeted_cpu_ocr','parent':config['parent_manifest_sha256'],'source_provenance':config['provenance_files']}))
        manifest['artifacts']={name:sha((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest)
        verify_document(stage);stage.rename(dest)
    receipt={'status':'passed','operation':'targeted_cpu_ocr','new_l1_package_path':str(dest),
        'new_l1_package_sha256':sha((dest/'l1-package.json').read_bytes()),'source_pdf_sha256':source['actual_sha256'],'page':number,
        'original_page_text_sha256':result['original_page_text_sha256'],'new_page_text_sha256':selected['text_sha256'],
        'source_quality_gate_scope':'selected_page_only','registered_decoder_required_before_counting':True,
        'original_native_readers_modified':False,'selected_representation_changed':True,'OCR_model_calls_during_wrapper':0,
        'publication_eligible':False};save(output/'construction-receipt.json',receipt);return receipt


def verify(folder,source_runtime):
    from .legal_l1 import verify_document
    from .proof_identity import inspect_pdf2data_profile,verify_unchanged_quality_pages
    folder=Path(folder);verify_document(folder);source=load(folder/'source-receipt.json');config=source.get('quality_repair_configuration',{})
    if config.get('operation')!='targeted_cpu_ocr':raise ValueError('Unregistered targeted OCR operation')
    current=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
    if current['source_configuration_expected']!=config['proof_profile']['source_configuration_expected']:raise ValueError('Targeted OCR decoder epoch differs')
    parent=folder/config['portable_parent_path'];verify_document(parent)
    if sha((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']:raise ValueError('Original OCR parent seal differs')
    if(folder/'original.pdf').read_bytes()!=(parent/'original.pdf').read_bytes():raise ValueError('Original OCR source bytes changed')
    expected=inspect_pdf2data_profile(source_runtime)['source_configuration_expected']
    if any(source['configuration'].get(k)!=v for k,v in expected.items()):raise ValueError('Original OCR extraction runtime differs')
    for name,digest in config['provenance_files'].items():
        if Path(name).is_absolute()or '..'in Path(name).parts or sha((folder/name).read_bytes())!=digest:raise ValueError('Original OCR reading/model provenance changed')
    number,quality,selection=derive(folder)
    actual=next(q for q in load(folder/'quality.json')['results']if q['page']==number)
    if actual!=quality:raise ValueError('OCR quality differs from freshly rederived source proof')
    verify_unchanged_quality_pages(folder,parent,[number])
    before={r['page']:r for r in rows(parent/'layout.jsonl')};after={r['page']:r for r in rows(folder/'layout.jsonl')}
    for number2,raw in before.items():
        if number2!=number and raw!=after[number2]:raise ValueError('OCR changed unselected native layout')
        if number2==number:
            mutable={'page_text_sha256','selected_method','quality_independent_basis','quality_independent_text','quality_independent_text_sha256','alignment'}
            for key in set(raw)-mutable:
                if raw.get(key)!=after[number2].get(key):raise ValueError('OCR changed original raw native evidence')
    return {'status':'verified','operation':'targeted_cpu_ocr','source_quality_pages_verified':[number],
        'quality_scope':'selected_pages_only','original_native_readers_modified':False,'selected_representation_changed':True,
        'OCR_model_calls_during_decoder':0,'all8_normal_source_gates_passed':True,'publication_eligible':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',type=Path);p.add_argument('--source-runtime',type=Path,required=True)
    p.add_argument('--experiment',type=Path);p.add_argument('--orientation-review',type=Path);p.add_argument('--output',type=Path);p.add_argument('--verify',type=Path);a=p.parse_args()
    print(json.dumps(verify(a.verify,a.source_runtime)if a.verify else build(a.parent,a.source_runtime,a.experiment,load(a.orientation_review),a.output),indent=2))
