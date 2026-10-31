"""Bound failed-page witnesses; diagnostics never authorize source quality.

The outer sidecar seals a small link in page-proofs.json. Complete escaped JSON
payloads stay off progress rows, preserving every reading/codepoint without
clipping or loading large witnesses in the normal progress observer.
"""
import copy,hashlib,json,re
from pathlib import Path

CONTRACT='pdf2data.native-region-held-diagnostic.v1'
SNAPSHOT='pdf2data.native-region-complete-snapshot.v1'


class HeldRegionEvidence(ValueError):
    def __init__(self,error,stage,snapshot,cells,observation):
        super().__init__(str(error));self.original_error=error;self.stage=stage
        self.snapshot=snapshot;self.cells=cells;self.observation=observation


def _api():
    from . import targeted_reproof
    return targeted_reproof
def _sha(data):return hashlib.sha256(data).hexdigest()
def _save(path,value):
    # ASCII escaping preserves even unpaired auxiliary codepoints losslessly.
    Path(path).write_text(json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2)+'\n',encoding='ascii')
def _policy_hash():
    from .quality_gate import load_policy
    return _api().digest(json.dumps(load_policy(),sort_keys=True,ensure_ascii=False))


def _expected(bound,origin,runtime,modules,page):
    return {'source_pdf_path':str((bound['path']/'original.pdf').resolve()),'source_pdf_sha256':bound['source_pdf_sha256'],
        'parent_l1_package_path':str(bound['path'].resolve()),'parent_manifest_sha256':bound['manifest_sha256'],
        'page':page,'page_text_sha256':bound['pages'][page]['text_sha256'],
        'source_runtime_path':str(Path(origin).resolve()),'source_extraction_profile_sha256':bound['source_origin_profile']['profile_sha256'],
        'proof_runtime_path':str(Path(runtime).resolve()),'proof_runtime_tree_sha256':modules['epoch']['tree_sha256'],
        'proof_profile_sha256':bound['profile']['profile_sha256'],'quality_policy_sha256':_policy_hash()}


def _typed(binding):
    if type(binding.get('page'))is not int or binding['page']<1:raise ValueError('Diagnostic physical page must be an exact positive integer')
    for key in('source_pdf_path','parent_l1_package_path','source_runtime_path','proof_runtime_path'):
        if not isinstance(binding.get(key),str)or not Path(binding[key]).is_absolute():raise ValueError('Diagnostic absolute source/profile path required')
    for key in('source_pdf_sha256','parent_manifest_sha256','page_text_sha256','source_extraction_profile_sha256','proof_runtime_tree_sha256','proof_profile_sha256','quality_policy_sha256'):
        if not isinstance(binding.get(key),str)or re.fullmatch('[a-f0-9]{64}',binding[key])is None:raise ValueError('Diagnostic hash binding is invalid: '+key)


def _checked_json(root,entry):
    if not isinstance(entry,dict)or set(entry)!={'path','sha256'}:raise ValueError('Closed diagnostic artifact link required')
    name=entry['path']
    if not isinstance(name,str)or Path(name).is_absolute()or '..'in Path(name).parts:raise ValueError('Diagnostic artifact path escapes local scope')
    root=Path(root).resolve();path=(root/name).resolve(strict=True)
    if not path.is_relative_to(root):raise ValueError('Diagnostic symlink escapes local scope')
    before=path.stat();data=path.read_bytes();after=path.stat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if identity(before)!=identity(after)or(root/name).resolve(strict=True)!=path or _sha(data)!=entry['sha256']:
        raise ValueError('Diagnostic artifact identity/hash changed')
    return json.loads(data)


def persist(output,bound,origin,runtime,modules,page,failure):
    api=_api();output=Path(output).resolve();folder=output/'diagnostics'/f'page-{page:06}';folder.mkdir(parents=True,exist_ok=False)
    snapshot={'contract_version':SNAPSHOT,'source_snapshot':failure.snapshot,
        'original_selected_page_record':copy.deepcopy(bound['pages'][page]),
        'original_layout_page_record':copy.deepcopy(bound.get('layout',{}).get(page))}
    error=failure.original_error;message=str(error)
    cells=failure.cells if failure.cells is not None else {'status':'not_returned','complete_report_available':False,
        'reason':'cell_validation_raised_before_returning_report','source_quality_clearance':False}
    _save(folder/'source-snapshot.json',snapshot);_save(folder/'cell-validation.json',cells)
    binding={'contract_version':CONTRACT,**_expected(bound,origin,runtime,modules,page),'stage':failure.stage,
        'observation_basis':failure.observation,'authority':'diagnostic_candidate_only_final_fresh_source_success_required',
        'status':'held','source_quality_clearance':False,'selected_text_modified':False,'legal_review':'not_run','answer_publication_eligible':False,
        'error':{'class':type(error).__module__+'.'+type(error).__qualname__,'message_codepoints':[ord(c)for c in message],'message_ascii_json':json.dumps(message,ensure_ascii=True)},
        'artifacts':{name:{'path':name,'sha256':api.file_sha(folder/name)}for name in('source-snapshot.json','cell-validation.json')}}
    _typed(binding);_save(folder/'bindings.json',binding)
    return {'contract_version':CONTRACT,'binding':{'path':str((folder/'bindings.json').relative_to(output)),'sha256':api.file_sha(folder/'bindings.json')}}


def load_binding(path,bound,origin,runtime,modules,page,*,checked_binding=None):
    """Read a candidate witness only after exact source/profile/hash checks."""
    api=_api();path=Path(path).resolve()
    binding=checked_binding if checked_binding is not None else _checked_json(path.parent,{'path':path.name,'sha256':api.file_sha(path)})
    _typed(binding)
    if (binding.get('contract_version')!=CONTRACT or binding.get('status')!='held'or
        binding.get('authority')!='diagnostic_candidate_only_final_fresh_source_success_required'or
        any(binding.get(k)is not False for k in('source_quality_clearance','selected_text_modified','answer_publication_eligible'))or
        binding.get('legal_review')!='not_run'or binding.get('stage')not in('validate_cells','derive_regions')or
        binding.get('observation_basis')not in('fresh_original_capture','untrusted_builder_candidate')):
        raise ValueError('Diagnostic cannot acquire source-quality authority')
    if any(binding.get(k)!=v for k,v in _expected(bound,origin,runtime,modules,page).items()):raise ValueError('Diagnostic source/page/text/profile/policy binding changed')
    if api.file_sha(bound['path']/'original.pdf')!=binding['source_pdf_sha256']:raise ValueError('Diagnostic original source bytes changed')
    if set(binding.get('artifacts',{}))!={'source-snapshot.json','cell-validation.json'}:raise ValueError('Complete diagnostic payload links required')
    snapshot=_checked_json(path.parent,binding['artifacts']['source-snapshot.json']);cells=_checked_json(path.parent,binding['artifacts']['cell-validation.json'])
    if (snapshot.get('contract_version')!=SNAPSHOT or snapshot.get('original_selected_page_record')!=bound['pages'][page]or
        snapshot.get('original_layout_page_record')!=bound.get('layout',{}).get(page)):
        raise ValueError('Diagnostic original page/layout records differ')
    source=snapshot.get('source_snapshot')or{}
    if (any(source.get(k)!=v for k,v in {'source_pdf_sha256':bound['source_pdf_sha256'],'page':page,'page_text_sha256':bound['pages'][page]['text_sha256']}.items())or
        source.get('fresh_quality',{}).get('policy_sha256')!=binding['quality_policy_sha256']):raise ValueError('Diagnostic captured source snapshot differs')
    if not isinstance(cells,dict):raise ValueError('Diagnostic cell terminal report is invalid')
    if cells.get('status')!='not_returned'and any(cells.get(k)!=source.get(k)for k in('source_pdf_sha256','page','page_text_sha256')):raise ValueError('Diagnostic cell report source differs')
    return {'binding':binding,'snapshot':source,'cells':cells}


def verify_link(link,output,bound,origin,runtime,modules,page):
    if not isinstance(link,dict)or set(link)!={'contract_version','binding'}or link['contract_version']!=CONTRACT:raise ValueError('Invalid sealed diagnostic link')
    binding=_checked_json(output,link['binding'])
    return load_binding(Path(output)/link['binding']['path'],bound,origin,runtime,modules,page,checked_binding=binding)
