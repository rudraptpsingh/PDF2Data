"""Export every parsed page and representation; quality flags never filter data."""
from pathlib import Path
import json,hashlib,os,tempfile
from .legal_l1 import verify_document,rows
SCHEMA='pdf2data.processed.v1'

def export_processed(packages:Path,output:Path):
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
