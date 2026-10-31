"""Opaque bounded verification sessions over immutable source artifact graphs.

Successful full semantic verification is cached only after a stable before/after
graph read. Every hit rehashes the complete graph and checks the proof/dependency
epoch. Matching hashes reuse earlier verification; they never create approval.
"""
from collections import OrderedDict
from contextvars import ContextVar
import copy,hashlib,json
from pathlib import Path

_ACTIVE=ContextVar('pdf2data_native_verification_session',default=None)


def active_session():return _ACTIVE.get()


def _file(path):
    before=path.stat()
    from .hashing import file_sha256
    digest=file_sha256(path)
    after=path.stat()
    stat=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if stat(before)!=stat(after):raise ValueError('Source artifact changed during verification guard')
    return digest,stat(after)


class VerificationSession:
    def __init__(self,max_packages=4):
        if type(max_packages)is not int or not 1<=max_packages<=16:raise ValueError('Bounded verification cache required')
        self.max_packages=max_packages;self._verified=OrderedDict();self._rows=OrderedDict();self.token=None
        self.stats={'full_verifications':0,'cache_hits':0,'artifact_guard_reads':0,'page_row_reads':0}

    def __enter__(self):
        if self.token is not None:raise ValueError('Verification session already active')
        self.token=_ACTIVE.set(self);return self

    def __exit__(self,*args):
        _ACTIVE.reset(self.token);self.token=None;self._verified.clear();self._rows.clear()

    def _identity(self,package):
        from .proof_identity import inspect_pdf2data_profile
        package=Path(package).resolve();manifest=json.loads((package/'l1-package.json').read_text())
        evidence=json.loads((package/'extraction/evidence-package.json').read_text())
        paths={'l1-package.json','extraction/evidence-package.json',*manifest.get('artifacts',{})}
        paths.update('extraction/'+name for name in evidence['files'])
        identity={}
        for name in sorted(paths):
            path=(package/name).resolve()
            if not path.is_relative_to(package):raise ValueError('Verification cache artifact escapes source package')
            identity[name]=_file(path);self.stats['artifact_guard_reads']+=1
        profile=inspect_pdf2data_profile(Path(__file__).resolve().parents[2])
        return str(package),profile['profile_sha256'],identity

    def verify(self,package,full_verifier):
        identity=self._identity(package)
        # A COW stage renamed to its final directory is the SAME exact sealed
        # graph. Content-addressed reuse avoids a second full traversal; every
        # byte was rehashed above, and file races are still checked in _file.
        signature=(identity[1],{name:value[0]for name,value in identity[2].items()})
        key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest();old=self._verified.get(key)
        if old is not None and old[0]==signature:
            self._verified.move_to_end(key);self.stats['cache_hits']+=1;return copy.deepcopy(old[1])
        self._verified.pop(key,None)
        result=full_verifier(package);self.stats['full_verifications']+=1
        if self._identity(package)!=identity:raise ValueError('Source graph changed during full verification')
        self._verified[key]=(signature,copy.deepcopy(result))
        while len(self._verified)>self.max_packages:self._verified.popitem(last=False)
        return result

    def page_row(self,path,page):
        """Read one already source-bound ordered JSONL page without full parsing."""
        path=Path(path).resolve();identity=_file(path);key=str(path);cached=self._rows.get(key)
        if cached is None or cached[0]!=identity:
            offsets=[]
            with path.open('rb')as stream:
                while True:
                    position=stream.tell();line=stream.readline()
                    if not line:break
                    if line.strip():offsets.append(position)
            self._rows[key]=(identity,offsets);cached=self._rows[key]
            while len(self._rows)>self.max_packages*3:self._rows.popitem(last=False)
        if type(page)is not int or not 1<=page<=len(cached[1]):raise ValueError('Source page outside verified JSONL')
        with path.open('rb')as stream:stream.seek(cached[1][page-1]);row=json.loads(stream.readline())
        if row.get('page')!=page or _file(path)!=identity:raise ValueError('Ordered source page changed during read')
        self.stats['page_row_reads']+=1;return row
