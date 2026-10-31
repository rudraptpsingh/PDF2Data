"""Small source-bound query records from verified cached L1 artifacts.

No PDF extraction, OCR or model call. Full processed export remains separate.
Quality observations are retained; exporting never grants approval.
"""
from pathlib import Path
import hashlib,json,os,tempfile
from .jsonl import jsonl_physical_lines
from .legal_l1 import verify_document

SCHEMA='pdf2data.query-record.v1'

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(raw):return hashlib.sha256(raw.encode('utf8')if isinstance(raw,str)else raw).hexdigest()

def file_digest(path):
    h=hashlib.sha256()
    with path.open('rb')as stream:
        while chunk:=stream.read(1024*1024):h.update(chunk)
    return h.hexdigest()

def stamp(path):
    s=path.stat();return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def export_query_data(packages:Path,output:Path):
    """One record per document/full page/entire table/entire candidate unit.

    Parent verification is called once per package. That existing verifier may
    inspect layout; this exporter never JSON-loads the large layout sidecar.
    """
    packages=Path(packages).resolve();output=Path(output)
    if output.exists()or output.is_symlink():
        raise ValueError('query export requires a new file outside immutable source packages')
    output=output.resolve()
    if output.is_relative_to(packages):
        raise ValueError('query export requires a new file outside immutable source packages')
    folders=sorted(p for p in packages.iterdir()if(p/'l1-package.json').is_file())
    if not folders:raise ValueError('no L1 packages')
    counts={'document':0,'page':0,'table':0,'unit':0};flagged=0
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.pdf2data-query-',dir=output.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8',newline='\n')as stream:
            for package in folders:
                manifest_path=package/'l1-package.json'
                paths=[p for p in package.rglob('*')if p.is_file()]
                before={p:stamp(p)for p in paths}
                checked=verify_document(package)  # exactly one parent verification
                manifest_raw=manifest_path.read_bytes();manifest=json.loads(manifest_raw)
                package_sha=digest(manifest_raw);pdf_sha=checked['source_pdf_sha256']
                extraction_manifest_path=package/'extraction/evidence-package.json'
                extraction_raw=extraction_manifest_path.read_bytes();extraction=json.loads(extraction_raw)
                expected={**manifest['artifacts'],**{'extraction/'+k:v['sha256']for k,v in extraction['files'].items()}}
                expected['l1-package.json']=package_sha
                expected['extraction/evidence-package.json']=digest(extraction_raw)
                refs={}
                for relative,expected_sha in expected.items():
                    path=package/relative;actual=file_digest(path)
                    if actual!=expected_sha:raise ValueError('cached artifact changed after parent verification: '+relative)
                    refs[relative]={'kind':'cached_artifact','artifact_path':str(path),'artifact_sha256':actual,'bytes':path.stat().st_size,
                        'source_pdf_sha256':pdf_sha,'source_package_sha256':package_sha,'availability':'local_not_uploaded'}
                def load(relative):
                    raw=(package/relative).read_bytes()
                    if digest(raw)!=refs[relative]['artifact_sha256']:raise ValueError('cached artifact changed while reading: '+relative)
                    return json.loads(raw)
                source=load('source-receipt.json');quality=load('quality.json');readiness=load('readiness.json');structure=load('structure.json');tables=load('extraction/tables.json')
                raw=(package/'extraction/pages.jsonl').read_bytes()
                if digest(raw)!=refs['extraction/pages.jsonl']['artifact_sha256']:raise ValueError('cached pages changed while reading')
                pages=[(index,json.loads(line))for index,line in enumerate(jsonl_physical_lines(raw.decode('utf8')))if line.strip(' \t\r')]
                def emit(kind,ordinal,payload,reference):
                    counts[kind]+=1
                    identity={'schema_version':SCHEMA,'source_pdf_sha256':pdf_sha,'package_sha256':package_sha,'kind':kind,'ordinal':ordinal}
                    record={**identity,'id':digest(canonical(identity)),'source_id':source['declared']['id'],'payload':payload,'payload_sha256':digest(canonical(payload)),
                        'source_ref':reference,'offset_encoding':'unicode_codepoint','source_quality_approval_granted':False,'legal_answer_eligible':False,'publication_eligible':False}
                    stream.write(canonical(record)+'\n')
                metadata={k:v for k,v in structure.items()if k not in{'units','children','regions','family_regions'}}
                emit('document',0,{'source':source,'quality':quality,'readiness':readiness,'verification':checked,'structure_metadata':metadata,
                    'counts':{'pages':len(pages),'tables':len(tables),'unit_candidates':len(structure['units'])},
                    'raw_sidecars':refs,'projection_scope':'complete_cached_pages_tables_units_with_raw_sidecar_references_not_all_representations',
                    'quality_observation_scope':'retained_cached_observations_not_fresh_quality_reproof','legal_state':'unreviewed'},refs['l1-package.json'])
                for index,page in pages:
                    emit('page',page['page'],page,{**refs['extraction/pages.jsonl'],'record_index':index,'text_field':'text','physical_page':page['page']})
                for index,table in enumerate(tables):emit('table',index,table,{**refs['extraction/tables.json'],'json_pointer':'/'+str(index),'physical_page':table['page']})
                for index,unit in enumerate(structure['units']):emit('unit',index,unit,{**refs['structure.json'],'json_pointer':'/units/'+str(index),
                    'evidence_spans':[{k:v for k,v in span.items()if k!='exact_quote'}for span in unit['spans']]})
                flagged+=sum(r['outcome']!='auto_accepted'for r in quality['results'])
                if any(not p.is_file()or stamp(p)!=s for p,s in before.items()):raise ValueError('source package changed during query export')
        os.link(name,output)  # exclusive publication; never overwrite a prior export
    finally:Path(name).unlink(missing_ok=True)
    return{'schema_version':SCHEMA,'output':str(output),'sha256':file_digest(output),'bytes':output.stat().st_size,'records':sum(counts.values()),'record_counts':counts,
        'documents':counts['document'],'pages':counts['page'],'quality_flagged_pages':flagged,'withheld_pages':0,
        'raw_sidecar_availability':'local_not_uploaded','parent_verifications':counts['document'],'fresh_extraction_calls':0,'model_calls':0,
        'source_quality_approval_granted':False,'legal_answer_eligible':False,'publication_eligible':False}
