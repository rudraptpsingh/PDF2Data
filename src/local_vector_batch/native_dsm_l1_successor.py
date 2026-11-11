"""Registered, held, one-page DSM native-reading successor.

The operation is intentionally limited to the exact reviewed source and pinned
parent/target inputs. It never grants source or legal quality approval.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile

OPERATION='native_dsm_text_successor_v1'
PAGE=13


def _sha(b): return sha256(b if isinstance(b,bytes) else b.encode()).hexdigest()
def _read(p): return json.loads(Path(p).read_text())
def _rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def _save(p,v): Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def _lines(p,v): Path(p).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in v))


def _replace(value, old_hash, new_hash):
    if isinstance(value,str):
        return value.replace(old_hash,new_hash).replace('रािपत्र','राजपत्र')
    if isinstance(value,list):return [_replace(v,old_hash,new_hash) for v in value]
    if isinstance(value,dict):return {k:_replace(v,old_hash,new_hash) for k,v in value.items()}
    return value


def build(parent, targeted, output, *, parent_sha256, targeted_sha256):
    """Build a new held L1 only after fresh, exact source preflight."""
    from .native_text_successor_preflight import inspect
    from .native_dsm_header_candidate import propose
    from .legal_l1 import (verify_document,_capture_page,structure_document,validate_structure,
                           readiness_ledger)
    from .evidence_package import create_package,verify_package
    from .pdf_ingest import text_quality
    from .quality_gate import native_source_raster_evidence,assess_page,load_policy
    from .quality_routes import matching_routes
    from .proof_identity import inspect_pdf2data_profile
    import pdfplumber
    from pypdf import PdfReader
    parent,targeted,output=map(lambda x:Path(x).resolve(),(parent,targeted,output))
    if output.exists() or any(output==p or output.is_relative_to(p) or p.is_relative_to(output) for p in (parent,targeted)):
        raise ValueError('new output outside both immutable inputs required')
    pre=inspect(parent,targeted,parent_manifest_sha256=parent_sha256,extraction_manifest_sha256=targeted_sha256)
    source=parent/'original.pdf';source_sha=pre['source_pdf_sha256']
    selected=next(x for x in _rows(targeted/'pages.jsonl') if x['page']==PAGE)
    candidate=propose(source,PAGE,selected['text']);old_hash=selected['text_sha256'];new_hash=candidate['candidate_page_sha256']
    # The exact candidate changes one codepoint, so selected-page offsets do not shift.
    if len(candidate['candidate_page_text'])!=len(selected['text']):raise ValueError('candidate offsets changed')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dsm-native-l1-',dir=output.parent) as td:
        stage=Path(td)/'package';shutil.copytree(parent,stage)
        shutil.rmtree(stage/'extraction');shutil.copytree(targeted,stage/'extraction')
        ext=stage/'extraction';pages=_rows(ext/'pages.jsonl')
        for i,p in enumerate(pages):
            if p['page']!=PAGE:continue
            p=deepcopy(p);p['text']=candidate['candidate_page_text'];p['text_sha256']=new_hash
            p['chars']=len(p['text']);p['selected_quality']=text_quality(p['text'])
            p['status']='review_required';p['native_header_glyph_candidate']={k:v for k,v in candidate.items() if k!='candidate_page_text'}
            pages[i]=p;selected=p
        _lines(ext/'pages.jsonl',pages)
        for name in ('embedding-input.jsonl','fact-candidates.jsonl','structure-candidates.jsonl'):
            rows=_rows(ext/name);patched=[]
            for row in rows:
                number=row.get('page') or row.get('metadata',{}).get('page')
                references_selected = old_hash in json.dumps(row,ensure_ascii=False)
                patched.append(_replace(row,old_hash,new_hash) if number==PAGE or references_selected else row)
            _lines(ext/name,patched)
        report=_read(ext/'report.json');report['page_status_counts']={s:sum(p['status']==s for p in pages) for s in ('ready','blank','review_required')}
        report['text_chars']=sum(p['chars'] for p in pages);report['complete_for_embedding']=False
        _save(ext/'report.json',report);create_package(ext);verify_package(ext,source_pdf=source)
        layout=_rows(stage/'layout.jsonl')
        with pdfplumber.open(source) as pdf:
            native=pdf.pages[PAGE-1];alternate=PdfReader(source).pages[PAGE-1]
            captured=_capture_page(source,source_sha,native,selected,alternate)
            for i,row in enumerate(layout):
                if row['page']==PAGE:layout[i]=captured
            policy=load_policy();witness=captured['quality_independent_text']
            tables=_read(ext/'tables.json');count=sum(t['page']==PAGE for t in tables)
            geometry=native_source_raster_evidence(selected,native,source_sha,witness,policy=policy,table_count=count)
            q=assess_page(selected,source_pdf_sha256=source_sha,package_pdf_sha256=source_sha,
                independent_text=witness,source_geometry=geometry,table_count=count,policy=policy,source_pdf_path=source)
        if q['outcome']!='repair_required':raise ValueError('selected page must remain held')
        q['policy_sha256']=_sha(json.dumps(policy,sort_keys=True,ensure_ascii=False));q['source_url']=report.get('source_url')
        q['independent_text_sha256']=_sha(witness) if witness is not None else None
        q['independent_reader_basis']=captured['quality_independent_basis']
        _lines(stage/'layout.jsonl',layout)
        source_receipt=_read(stage/'source-receipt.json')
        structure=structure_document(pages,source_sha,source_receipt['declared']['family'],layout=layout,tables=tables)
        checked=validate_structure(structure,pages,source_sha);_save(stage/'structure.json',structure)
        quality=_read(parent/'quality.json');quality['results']=[q if x['page']==PAGE else x for x in quality['results']]
        quality['counts']={s:sum(x['outcome']==s for x in quality['results']) for s in ('auto_accepted','repair_required','source_blocked')}
        quality['independent_basis_by_page'][str(PAGE)]=captured['quality_independent_basis'];_save(stage/'quality.json',quality)
        _save(stage/'readiness.json',readiness_ledger(structure,quality))
        jobs=_rows(parent/'repair-queue.jsonl');jobs=[x for x in jobs if x['page']!=PAGE]
        jobs.append({'page':PAGE,'source_pdf_sha256':source_sha,'page_text_sha256':new_hash,
                     'status':'awaiting_source_proof','failed_components':q.get('failed_components',[]),
                     'missing_evidence':q.get('missing_evidence',[]),
                     'routes':[{'id':r['id'],'action':r['action']} for r in matching_routes(q)],
                     'mutation_policy':'Repair a new extraction version; never rewrite this immutable L1 package.'})
        _lines(stage/'repair-queue.jsonl',sorted(jobs,key=lambda x:x['page']))
        config={'operation':OPERATION,'parent_l1_package_path':str(parent),'parent_manifest_sha256':parent_sha256,
                'target_extraction_path':str(targeted),'target_extraction_manifest_sha256':targeted_sha256,
                'source_pdf_sha256':source_sha,'page':PAGE,'candidate_page_sha256':new_hash,
                'implementation_sha256':_sha(Path(__file__).read_bytes()),
                'proof_profile':inspect_pdf2data_profile(Path(__file__).resolve().parents[2]),
                'publication_eligible':False}
        source_receipt['quality_repair_configuration']=config;_save(stage/'source-receipt.json',source_receipt)
        validation=_read(stage/'validation.json');validation['structure']=checked
        validation['page_readiness']=report['page_status_counts'];validation['publication_eligible']=False
        _save(stage/'validation.json',validation)
        manifest=_read(stage/'l1-package.json');manifest['request_sha256']=_sha(json.dumps(config,ensure_ascii=False,sort_keys=True,separators=(',',':')))
        manifest['artifacts']={name:_sha((stage/name).read_bytes()) for name in manifest['artifacts']};_save(stage/'l1-package.json',manifest)
        verified=verify_document(stage)
        parent_quality=_read(parent/'quality.json')['results'];new_quality=_read(stage/'quality.json')['results']
        if any(a!=b for a,b in zip(parent_quality,new_quality) if a['page']!=PAGE):raise ValueError('unselected quality changed')
        if pre!=inspect(parent,targeted,parent_manifest_sha256=parent_sha256,extraction_manifest_sha256=targeted_sha256):raise ValueError('inputs changed during build')
        stage.rename(output)
    return {'status':'held_successor_built','operation':OPERATION,'source_pdf_sha256':source_sha,
        'parent_manifest_sha256':parent_sha256,'target_extraction_manifest_sha256':targeted_sha256,
        'successor_manifest_sha256':_sha((output/'l1-package.json').read_bytes()),'page':PAGE,
        'candidate_page_sha256':new_hash,'quality_outcome':q['outcome'],'unselected_quality_unchanged':True,
        'verification':verified,'publication_eligible':False}


def verify(folder,source_runtime=None):
    """Rebuild independently from pinned parent/source and compare every artifact."""
    from .legal_l1 import verify_document
    from .proof_identity import inspect_pdf2data_profile
    folder=Path(folder).resolve();verify_document(folder)
    cfg=_read(folder/'source-receipt.json').get('quality_repair_configuration',{})
    if cfg.get('operation')!=OPERATION or cfg.get('implementation_sha256')!=_sha(Path(__file__).read_bytes()):
        raise ValueError('successor operation or decoder code changed')
    if cfg.get('proof_profile') != inspect_pdf2data_profile(Path(__file__).resolve().parents[2]):
        raise ValueError('successor proof code or runtime changed')
    with tempfile.TemporaryDirectory(prefix='dsm-successor-verify-') as td:
        replay=Path(td)/'replayed'
        receipt=build(cfg['parent_l1_package_path'],cfg['target_extraction_path'],replay,
                      parent_sha256=cfg['parent_manifest_sha256'],targeted_sha256=cfg['target_extraction_manifest_sha256'])
        actual={p.relative_to(folder).as_posix():_sha(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
        expected={p.relative_to(replay).as_posix():_sha(p.read_bytes()) for p in replay.rglob('*') if p.is_file()}
        if actual!=expected:raise ValueError('successor artifacts differ from fresh reconstruction')
    return {'status':'verified','operation':OPERATION,'source_pdf_sha256':cfg['source_pdf_sha256'],
            'page':PAGE,'candidate_page_sha256':cfg['candidate_page_sha256'],
            'source_quality':'repair_required','unselected_quality_unchanged':True,'publication_eligible':False}
