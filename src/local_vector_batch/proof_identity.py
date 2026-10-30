"""Separate immutable source-extraction identity from a later proof engine."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path


def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def inspect_pdf2data_profile(parser_root):
    root=Path(parser_root).resolve();src=root/'src/local_vector_batch'
    files=sorted(p for p in src.iterdir()if p.suffix in{'.py','.json'})
    expected={'pdfplumber':version('pdfplumber'),'pypdf':version('pypdf'),
              'implementation_sha256':sha((src/'legal_l1.py').read_bytes()),
              'extractor_sha256':sha((src/'pdf_ingest.py').read_bytes()),
              'package_code_sha256':sha(b''.join(p.name.encode()+p.read_bytes()for p in files))}
    profile={'contract_version':'powerlex.pdf2data-l1-profile.v1','parser_kind':'PDF2Data_legal_l1',
             'parser_root':str(root),'source_configuration_expected':expected,
             'source_files':{p.name:sha(p.read_bytes())for p in files},
             'legal_review':'not_run','answer_publication_eligible':False}
    profile['profile_sha256']=sha(canonical(profile));return profile


def verify_l1_source_readiness(folder,source_runtime):
    from .legal_l1 import verify_document
    folder=Path(folder);verified=verify_document(folder);profile=inspect_pdf2data_profile(source_runtime)
    config=json.loads((folder/'source-receipt.json').read_text())['configuration']
    matches=all(config.get(k)==v for k,v in profile['source_configuration_expected'].items())
    quality=json.loads((folder/'quality.json').read_text());pages=[]
    for q in quality['results']:
        allowed=matches and q['page_complete']is True and not q['missing_evidence']and not q['failed_components']and q['outcome']=='auto_accepted'and all(q['components'].get(k,{}).get('state')=='pass'for k in ('source_identity','text_content','language','source_render','words_and_order','ordered_numbers','ordered_operators','layout'))
        pages.append({'page':q['page'],'embedding_candidate_allowed':allowed})
    return {'integrity':verified['integrity'],'current_execution_profile_matches':matches,
            'source_extraction_profile_matches':matches,'source_configuration_observed':config,
            'pages':pages,'answer_publication_eligible':False}


def proof_configuration(operation,parent,engine):
    root=Path(__file__).resolve().parents[2];parent=Path(parent)
    return {'operation':operation,'parent_l1_package_path':str(parent.resolve()),
            'parent_manifest_sha256':sha((parent/'l1-package.json').read_bytes()),
            'source_extraction_configuration':json.loads((parent/'source-receipt.json').read_text())['configuration'],
            'proof_runtime_path':str(root),'proof_profile':inspect_pdf2data_profile(root),
            'proof_implementation_sha256':sha(Path(engine).read_bytes()),
            'legal_review':'not_run','publication_eligible':False}


def verify_source_quality_repair(folder,source_runtime):
    """Reconstruct registered proof operations; unknown claims remain held."""
    folder=Path(folder);config=json.loads((folder/'source-receipt.json').read_text()).get('quality_repair_configuration')
    if not config:raise ValueError('Package has no registered source-quality repair')
    operation=config.get('operation')
    if operation=='native_table_regions':
        from .native_table_regions import verify_region_version
        return verify_region_version(folder,source_runtime)
    if operation=='native_token_spacing':
        from .legal_l1 import verify_document,rows
        from .native_quality_repair import decode_spacing_proof
        verify_document(folder);parent=Path(config['parent_l1_package_path']);verify_document(parent)
        if sha((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']:raise ValueError('Token repair parent package changed')
        current=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
        if current['source_configuration_expected']!=config['proof_profile']['source_configuration_expected']:raise ValueError('Token proof execution profile changed')
        quality=json.loads((folder/'quality.json').read_text());verified=[]
        for row in rows(folder/'layout.jsonl'):
            if 'native_token_spacing_proof'not in row:continue
            proof=decode_spacing_proof(row['native_token_spacing_proof'],parent)
            actual=next(q for q in quality['results']if q['page']==row['page'])
            if actual!=proof['whole_page_quality']or actual['outcome']!='auto_accepted':raise ValueError('Token repair quality differs from original source proof')
            verified.append(row['page'])
        if not verified:raise ValueError('Token quality repair has no registered page proofs')
        verify_unchanged_quality_pages(folder,parent,verified)
        if(folder/'original.pdf').read_bytes()!=(parent/'original.pdf').read_bytes()or(folder/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes():raise ValueError('Token repair source/selected text changed')
        return {'status':'verified','operation':operation,'source_quality_pages_verified':verified,'source_text_modified':False,'publication_eligible':False}
    raise ValueError('Unregistered source-quality repair operation')


def verify_unchanged_quality_pages(folder,parent,verified_pages):
    before=json.loads((Path(parent)/'quality.json').read_text())['results']
    after=json.loads((Path(folder)/'quality.json').read_text())['results']
    if len(before)!=len(after):raise ValueError('Repair changed whole-source quality coverage')
    changed=set(verified_pages)
    for original,current in zip(before,after):
        if original['page']!=current['page']or(original['page']not in changed and original!=current):
            raise ValueError('Unproved page-quality mutation outside registered repair scope')
