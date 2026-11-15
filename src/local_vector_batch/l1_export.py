"""Export every parsed page and representation; quality flags never filter data."""
from pathlib import Path
import json,hashlib,os,tempfile
from .legal_l1 import verify_document,rows
from .export_projection import candidate_snapshot, page_states
SCHEMA='pdf2data.processed.v1'

def export_processed(packages:Path,output:Path,region_packets:Path|None=None,*,document_model=False):
    packages=packages.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(packages):raise ValueError('export requires a new file outside immutable source packages')
    folders=sorted(p for p in packages.iterdir() if (p/'l1-package.json').exists())
    if not folders:raise ValueError('no L1 packages')
    docs=[]
    for p in folders:
        checked=verify_document(p)
        load=lambda n:json.loads((p/n).read_text())
        pages=rows(p/'extraction/pages.jsonl');quality=load('quality.json')
        quality_ref={'path':'quality.json','sha256':hashlib.sha256((p/'quality.json').read_bytes()).hexdigest()}
        docs.append({'source_id':p.name,'package_sha256':hashlib.sha256((p/'l1-package.json').read_bytes()).hexdigest(),
            'package_path':str(p),'source_pdf_path':str(p/'original.pdf'),'source_pdf_sha256':checked['source_pdf_sha256'],
            'source':load('source-receipt.json'),'pages':pages,'layout':rows(p/'layout.jsonl'),
            'page_states':page_states(pages,checked['source_pdf_sha256'],quality,quality_ref),
            **candidate_snapshot(p,pages,checked['source_pdf_sha256']),
            'tables':load('extraction/tables.json'),'structure':load('structure.json'),'quality':load('quality.json'),
            'readiness':load('readiness.json'),'repair_queue':rows(p/'repair-queue.jsonl'),
            'verification':checked,'legal_state':'unreviewed','publication_eligible':False})
    if document_model:
        from copy import deepcopy
        from .document_blocks import build_blocks
        from .document_model import build_document_model
        for doc in docs:
            # The readable block builder annotates tables. Keep those placement
            # annotations off the immutable raw table observations in this export.
            work={**doc,'tables':deepcopy(doc['tables'])}
            work['document_blocks']=build_blocks(work)
            doc['document_blocks']=work['document_blocks']
            doc['document_model']=build_document_model(work)
    if region_packets is not None:
        from .native_image_regions import verify as verify_regions
        region_packets=Path(region_packets).resolve()
        candidates=[region_packets]if(region_packets/'source-regions-manifest.json').is_file()else sorted(p.parent for p in region_packets.glob('*/source-regions-manifest.json'))
        if not candidates:raise ValueError('no mixed image candidate packets')
        seen=set()
        for candidate in candidates:
            checked=verify_regions(candidate);key=(checked['source_pdf_sha256'],checked['page'])
            if key in seen:raise ValueError('duplicate mixed page representations')
            seen.add(key)
            manifest=json.loads((candidate/'source-regions-manifest.json').read_text())
            targets=[d for d in docs if d['source_pdf_sha256']==key[0]and d['package_sha256']==manifest['parent_package_sha256']]
            if len(targets)!=1:raise ValueError('mixed image source does not match one exact exported parent')
            target=targets[0]
            if key[1]not in {p['page']for p in target['pages']}:raise ValueError('mixed image page outside exported source')
            target.setdefault('mixed_source_region_candidates',[]).append({'verification':checked,'representation':json.loads((candidate/'representation.json').read_text()),'completeness':json.loads((candidate/'completeness.json').read_text()),'packet_path':str(candidate),'packet_manifest_sha256':hashlib.sha256((candidate/'source-regions-manifest.json').read_bytes()).hexdigest(),'publication_eligible':False})
    packet={'schema_version':SCHEMA,'offset_encoding':'unicode_codepoint','documents':docs,
        'scope':'all_processed_data_including_quality_flagged_candidates','publication_eligible':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.pdf2data-export-',dir=output.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(packet,f,ensure_ascii=False);f.write('\n')
        # Exclusive publication; do not overwrite an existing export.
        os.link(name,output)
    finally:Path(name).unlink(missing_ok=True)
    return {'schema_version':SCHEMA,'documents':len(docs),'pages':sum(len(d['pages']) for d in docs),
        'quality_flagged_pages':sum(d['quality']['counts']['repair_required'] for d in docs),
        'withheld_pages':0,'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'publication_eligible':False}


def export_table_contexts(processed:Path,source_id:str,output:Path):
    """Write a fragment and sidecar from one exact processed document object."""
    from .table_contexts import write_table_contexts
    processed=Path(processed).resolve();output=Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve() in processed.parents:
        raise ValueError('new output directory outside the processed input required')
    raw=processed.read_bytes();packet=json.loads(raw)
    if packet.get('schema_version')!=SCHEMA:raise ValueError('processed export required')
    matches=[d for d in packet.get('documents',[]) if d.get('source_id')==source_id]
    if len(matches)!=1:raise ValueError('source ID must identify exactly one processed document')
    doc=matches[0]
    if 'document_model' not in doc or not doc.get('package_sha256'):
        raise ValueError('package-bound processed document with document model required')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pdf2data-context-',dir=output.parent) as temp:
        stage=Path(temp)/'bundle';stage.mkdir()
        fragment=stage/'data.json';fragment.write_text(json.dumps(doc,ensure_ascii=False)+'\n',encoding='utf-8')
        count=write_table_contexts(stage/'table-contexts.jsonl',doc)
        result={'source_id':source_id,'processed_sha256':hashlib.sha256(raw).hexdigest(),
                'package_sha256':doc['package_sha256'],'source_pdf_sha256':doc['source_pdf_sha256'],
                'fragment_sha256':hashlib.sha256(fragment.read_bytes()).hexdigest(),
                'contexts_sha256':hashlib.sha256((stage/'table-contexts.jsonl').read_bytes()).hexdigest(),
                'table_contexts':count,'publication_eligible':False}
        (stage/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        stage.rename(output)
    return result


def verify_table_contexts(processed:Path,bundle:Path):
    """Reproduce the pair against the named processed bytes; no source approval."""
    from .table_contexts import write_table_contexts
    processed=Path(processed);bundle=Path(bundle)
    raw=processed.read_bytes();packet=json.loads(raw);receipt=json.loads((bundle/'receipt.json').read_text())
    if packet.get('schema_version')!=SCHEMA or receipt.get('processed_sha256')!=hashlib.sha256(raw).hexdigest():
        raise ValueError('processed export identity differs')
    docs=[d for d in packet.get('documents',[]) if d.get('source_id')==receipt.get('source_id')]
    if len(docs)!=1 or not docs[0].get('document_model') or not docs[0].get('package_sha256'):
        raise ValueError('one package-bound document model required')
    doc=docs[0];fragment=(bundle/'data.json').read_bytes();contexts=(bundle/'table-contexts.jsonl').read_bytes()
    if (json.loads(fragment)!=doc or receipt.get('fragment_sha256')!=hashlib.sha256(fragment).hexdigest()
        or receipt.get('contexts_sha256')!=hashlib.sha256(contexts).hexdigest()
        or receipt.get('package_sha256')!=doc['package_sha256'] or receipt.get('source_pdf_sha256')!=doc['source_pdf_sha256']):
        raise ValueError('fragment, sidecar or identity differs')
    with tempfile.TemporaryDirectory(prefix='pdf2data-context-verify-') as temp:
        expected=Path(temp)/'contexts.jsonl';count=write_table_contexts(expected,doc)
        if expected.read_bytes()!=contexts or count!=receipt.get('table_contexts'):
            raise ValueError('sidecar differs from processed document reconstruction')
    return {'status':'verified','processed_sha256':receipt['processed_sha256'],
            'fragment_sha256':receipt['fragment_sha256'],'contexts_sha256':receipt['contexts_sha256'],
            'package_sha256':doc['package_sha256'],'source_pdf_sha256':doc['source_pdf_sha256'],
            'publication_eligible':False}
