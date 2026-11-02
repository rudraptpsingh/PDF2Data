"""Explicit, sealed custody bindings for exact captured native proof decoders.

Frozen JSON and source artifacts are never edited. An isolated interpreter runs
only an exact current decoder or a known released rc12/rc13/rc14 decoder. Its Path
constructors receive only prevalidated resource bindings, not a filesystem-wide
redirect or an import path to the extraction origin.
"""
from pathlib import Path
from importlib.metadata import version
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCHEMA='pdf2data.proof-relocations.v1'
RC12_CODE='05ec5555a128690a282ed5235f52dd684ca7f2866f4f2ab029f7700e1a47781e'
RC13_CODE='076b5055cc1496fde274888a880a31533f1f042365384b8da935984e74da86f5'
RC14_CODE='9ed276efcc6ddc0e1d643ddd9c68b2b014ec46d0e8ed4066cc399a835171c8e7'
RELEASED_DECODER_CODES=frozenset({RC12_CODE,RC13_CODE,RC14_CODE})
SUPPORTED={'native_whitefill_prose','native_table_regions'}


def supported_decoder_epoch(actual_code,current_code,operation):
    return operation in SUPPORTED and actual_code in RELEASED_DECODER_CODES|{current_code}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb')as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()
def read(path):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('Duplicate relocation JSON key')
            value[key]=item
        return value
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pairs)
def hash_value(value):return digest(canonical(value).encode())
def hash_string(value):return isinstance(value,str)and len(value)==64 and all(c in'0123456789abcdef'for c in value)
def captured_path(value):
    if not isinstance(value,str)or not Path(value).is_absolute()or '..'in Path(value).parts:
        raise ValueError('Captured resource paths must be absolute and lexical')
    return str(Path(value))
def inventory(root):
    files={}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():raise ValueError('Custody trees cannot contain symbolic links')
        if path.is_file():files[str(path.relative_to(root))]=file_sha(path)
        elif not path.is_dir():raise ValueError('Custody trees require regular files/directories')
    return files


class Bindings:
    def __init__(self,path):
        self.mapping_path=Path(path).resolve(strict=True);self.mapping_sha256=file_sha(self.mapping_path)
        data=read(self.mapping_path)
        if not isinstance(data,dict)or set(data)!={'schema_version','custody_root','packages','files'}or data['schema_version']!=SCHEMA:
            raise ValueError('Unknown or malformed proof relocation map')
        self.root=Path(captured_path(data['custody_root'])).resolve(strict=True)
        if not self.root.is_dir():raise ValueError('Custody root must be a directory')
        self.entries={};self.active={};self.records=[];self.seals={}
        for kind in('packages','files'):
            values=data[kind]
            if not isinstance(values,list)or len(values)>128:raise ValueError('Bounded relocation entries required')
            for item in values:
                keys={'captured_path','resolved_path','manifest_sha256','source_pdf_sha256','source_configuration_sha256'}if kind=='packages'else{'captured_path','resolved_path','sha256'}
                if not isinstance(item,dict)or set(item)!=keys:raise ValueError('Malformed relocation entry')
                old=captured_path(item['captured_path'])
                if old in self.entries:raise ValueError('Duplicate captured resource binding')
                if any(not hash_string(item[k])for k in keys-{'captured_path','resolved_path'}):raise ValueError('Exact relocation SHA256 pins required')
                target=self.within(item['resolved_path'])
                self.entries[old]=(kind,target,item)
    def within(self,path):
        target=Path(captured_path(str(path))).resolve(strict=True)
        if not target.is_relative_to(self.root):raise ValueError('Resolved resource escapes custody root')
        return target
    def package(self,config,source,verify):
        old=captured_path(config['parent_l1_package_path'])
        entry=self.entries.get(old)
        if entry is None or entry[0]!='packages':raise ValueError('Missing explicit parent package binding: '+old)
        _,target,pins=entry
        expected=config['parent_manifest_sha256'];pdf=source['actual_sha256'];configuration=config['source_extraction_configuration']
        if pins['manifest_sha256']!=expected or pins['source_pdf_sha256']!=pdf or pins['source_configuration_sha256']!=hash_value(configuration):
            raise ValueError('Parent relocation pins differ from captured proof')
        if file_sha(target/'l1-package.json')!=expected:raise ValueError('Relocated parent manifest differs from captured parent')
        receipt=read(target/'source-receipt.json')
        if receipt['configuration']!=configuration or receipt['actual_sha256']!=pdf or receipt.get('declared',{}).get('sha256')!=pdf or file_sha(target/'original.pdf')!=pdf:
            raise ValueError('Relocated parent original/source configuration differs')
        before=inventory(target);verify(target)
        if inventory(target)!=before:raise ValueError('Relocated parent changed during validation')
        self.active[old]=target;self.seals[str(target)]=('package',before)
        self.records.append(dict(role='parent_package',captured_path=old,resolved_path=str(target),manifest_sha256=expected,
            source_pdf_sha256=pdf,source_configuration_sha256=hash_value(configuration)))
        return target
    def file(self,old,expected,role):
        old=captured_path(old);entry=self.entries.get(old)
        if entry is None or entry[0]!='files':raise ValueError('Missing explicit file binding: '+old)
        _,target,pins=entry
        if pins['sha256']!=expected or file_sha(target)!=expected:raise ValueError('Relocated '+role+' bytes differ from captured input')
        self.active[old]=target;self.seals[str(target)]=('file',expected)
        self.records.append(dict(role=role,captured_path=old,resolved_path=str(target),sha256=expected))
        return target
    def preflight(self,folder,config,verify):
        source=read(Path(folder)/'source-receipt.json');self.package(config,source,verify)
        if config['operation']=='native_table_regions':
            self.file(config['feedback_path'],config['feedback_sha256'],'feedback')
            dossier=self.file(config['dossier_path'],config['dossier_sha256'],'dossier')
            decisions=read(dossier).get('decisions')
            if not isinstance(decisions,list):raise ValueError('Malformed captured raster dossier')
            for review in decisions:self.file(review['render_path'],review['render_sha256'],'review_raster')
    def path(self,*args):
        path=Path(*args)
        return self.active.get(str(path),path)
    def unchanged(self):
        if file_sha(self.mapping_path)!=self.mapping_sha256:raise ValueError('Relocation map changed during replay')
        for path,(kind,seal)in self.seals.items():
            if (inventory(Path(path))if kind=='package'else file_sha(path))!=seal:
                raise ValueError('Resolved custody resource changed during replay')


def package_directory(root):
    root=Path(root).resolve(strict=True)
    candidates=[root/'src/local_vector_batch',root/'local_vector_batch',root/'site-packages/local_vector_batch']
    if root.name=='local_vector_batch':candidates.append(root)
    found=next((p for p in candidates if(p/'legal_l1.py').is_file()and(p/'pdf_ingest.py').is_file()),None)
    if found is None:raise ValueError('Captured proof package directory not found')
    return found

def source_files(package):
    files=[p for p in sorted(package.iterdir())if p.suffix in{'.py','.json'}]
    if any(p.is_symlink()or not p.is_file()for p in files):raise ValueError('Proof runtime requires regular source files')
    return files

def code_sha(package):return digest(b''.join(p.name.encode()+p.read_bytes()for p in source_files(package)))


def replay(folder,source_runtime,proof_runtime,relocations):
    """Public entry point. Historical execution is explicit and byte constrained."""
    folder=Path(folder).resolve(strict=True);source_runtime=Path(source_runtime).resolve(strict=True)
    proof_runtime=Path(proof_runtime).resolve(strict=True);relocations=Path(relocations).resolve(strict=True)
    request=dict(folder=str(folder),source_runtime=str(source_runtime),proof_runtime=str(proof_runtime),relocations=str(relocations),
                 map_sha256=file_sha(relocations),child_manifest_sha256=file_sha(folder/'l1-package.json'))
    with tempfile.TemporaryDirectory(prefix='pdf2data-custody-replay-')as temporary:
        path=Path(temporary)/'request.json';path.write_text(json.dumps(request),encoding='utf-8')
        run=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--worker',str(path)],
            capture_output=True,text=True,timeout=600,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    if run.returncode:raise ValueError('Custody proof replay refused: '+run.stderr[-3000:])
    value=json.loads(run.stdout)
    if value.get('status')!='verified':raise ValueError('Custody decoder did not verify source quality')
    return value


def worker(request):
    bindings=Bindings(request['relocations'])
    if bindings.mapping_sha256!=request['map_sha256']:raise ValueError('Relocation map changed before replay')
    folder=bindings.within(request['folder']);origin=bindings.within(request['source_runtime']);runtime=bindings.within(request['proof_runtime'])
    if file_sha(folder/'l1-package.json')!=request['child_manifest_sha256']:raise ValueError('Frozen child changed before replay')
    child_seal=inventory(folder);source=read(folder/'source-receipt.json');config=source.get('quality_repair_configuration',{})
    operation=config.get('operation')
    if operation not in SUPPORTED:raise ValueError('Custody replay does not support this proof operation')
    package=package_directory(runtime);bindings.within(package)
    files=source_files(package)
    for path in files:bindings.within(path)
    captured=config['proof_profile'];expected=captured['source_configuration_expected'];actual_code=code_sha(package)
    current_code=code_sha(Path(__file__).resolve().parent)
    if not supported_decoder_epoch(actual_code,current_code,operation):raise ValueError('Captured decoder is not a supported released/current producer epoch')
    if actual_code!=expected['package_code_sha256']or {p.name:file_sha(p)for p in files}!=captured['source_files']:
        raise ValueError('Captured decoder source bytes differ from frozen proof profile')
    runtime_seal={p.name:file_sha(p)for p in files}
    with tempfile.TemporaryDirectory(prefix='pdf2data-exact-decoder-')as temporary:
        clean=Path(temporary);destination=clean/'src/local_vector_batch';destination.mkdir(parents=True)
        for path in files:shutil.copyfile(path,destination/path.name)
        # Only verified flat package files enter the isolated import path. Caller
        # siblings, nested code and the extraction origin are never imported.
        sys.path.insert(0,str(clean/'src'))
        from local_vector_batch import proof_identity,legal_l1,native_whitefill_prose,native_table_regions,native_table_feedback
        executed=proof_identity.inspect_pdf2data_profile(clean)
        if executed['source_configuration_expected']!=expected:raise ValueError('Captured decoder dependencies/profile differ')
        origin_package=package_directory(origin);bindings.within(origin_package)
        for path in source_files(origin_package):bindings.within(path)
        origin_seal={p.name:file_sha(p)for p in source_files(origin_package)}
        origin_profile=proof_identity.inspect_pdf2data_profile(origin)
        if any(config['source_extraction_configuration'].get(k)!=v for k,v in origin_profile['source_configuration_expected'].items()):
            raise ValueError('Explicit extraction origin differs from captured source configuration')
        legal_l1.verify_document(folder)
        bindings.preflight(folder,config,legal_l1.verify_document)
        # This changes resource resolution only. Frozen dictionaries and files,
        # source algorithms and their exact captured execution guard stay intact.
        for module in(proof_identity,native_whitefill_prose,native_table_regions,native_table_feedback):module.Path=bindings.path
        result=proof_identity.verify_source_quality_repair(folder,origin)
        bindings.unchanged()
        if inventory(folder)!=child_seal or {p.name:file_sha(p)for p in files}!=runtime_seal or {p.name:file_sha(p)for p in source_files(origin_package)}!=origin_seal:
            raise ValueError('Frozen child or explicit runtime changed during replay')
        result['custody_replay']=dict(schema_version='pdf2data.custody-proof-replay.v1',mapping_sha256=bindings.mapping_sha256,
            custody_root=str(bindings.root),captured_proof_profile_sha256=captured['profile_sha256'],executed_package_code_sha256=actual_code,
            captured_proof_runtime=config.get('proof_runtime_path'),resolved_proof_runtime=str(runtime),
            captured_source_runtime=config.get('source_runtime'),resolved_source_runtime=str(origin),
            source_runtime_imported=False,frozen_artifacts_modified=False,bindings=bindings.records,
            dependency_versions={name:version(name)for name in('pypdf','pdfplumber','pymupdf','pypdfium2')},
            path_binding_adapter_sha256=file_sha(Path(__file__)))
        return result


if __name__=='__main__':
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='--worker':raise ValueError('Internal custody worker arguments required')
        print(json.dumps(worker(read(sys.argv[2])),ensure_ascii=False))
    except Exception as error:
        print(type(error).__name__+': '+str(error),file=sys.stderr);sys.exit(2)
