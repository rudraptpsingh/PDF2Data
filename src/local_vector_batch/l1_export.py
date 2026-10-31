"""Export every parsed page and representation; quality flags never filter data."""
from pathlib import Path
import json,hashlib,os,tempfile
from .legal_l1 import verify_document,rows
SCHEMA='pdf2data.processed.v1'

def export_processed(packages:Path,output:Path,region_packets:Path|None=None):
    packages=packages.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(packages):raise ValueError('export requires a new file outside immutable source packages')
    folders=sorted(p for p in packages.iterdir() if (p/'l1-package.json').exists())
    if not folders:raise ValueError('no L1 packages')
    docs=[]
    for p in folders:
        checked=verify_document(p)
        load=lambda n:json.loads((p/n).read_text())
        docs.append({'source_id':p.name,'package_sha256':hashlib.sha256((p/'l1-package.json').read_bytes()).hexdigest(),
            'package_path':str(p),'source_pdf_path':str(p/'original.pdf'),'source_pdf_sha256':checked['source_pdf_sha256'],
            'source':load('source-receipt.json'),'pages':rows(p/'extraction/pages.jsonl'),'layout':rows(p/'layout.jsonl'),
            'tables':load('extraction/tables.json'),'structure':load('structure.json'),'quality':load('quality.json'),
            'readiness':load('readiness.json'),'repair_queue':rows(p/'repair-queue.jsonl'),
            'verification':checked,'legal_state':'unreviewed','publication_eligible':False})
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
