"""Bound emitted parser warnings without suppressing them or granting quality."""
from contextvars import ContextVar
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import warnings

CONTRACT='pdf2data.source-warning-provenance.v1'
MAX_EVENTS=64
MAX_MESSAGE_CODEPOINTS=4096
ACTIVE=ContextVar('pdf2data_source_warning_scope',default=None)
SCOPE_LOCK=threading.RLock()
LOGGER_PREFIXES=('pypdf','pdfminer','pdfplumber','pypdfium2','local_vector_batch')


def stage(value):
    observer=ACTIVE.get()
    if observer is not None:observer.stage=value


class _Handler(logging.Handler):
    def __init__(self,owner):super().__init__(logging.WARNING);self.owner=owner
    def emit(self,record):
        if ACTIVE.get()is self.owner and any(record.name==p or record.name.startswith(p+'.')for p in LOGGER_PREFIXES):
            self.owner.record('logging',record.getMessage(),record.name,record.pathname,record.lineno,record.levelname)


class SourceWarnings:
    def __init__(self,output,source_pdf_sha256,page,page_text_sha256,proof_profile_sha256):
        if type(page)is not int or page<1 or any(not isinstance(v,str)or re.fullmatch('[a-f0-9]{64}',v)is None for v in(source_pdf_sha256,page_text_sha256,proof_profile_sha256)):
            raise ValueError('Exact physical page and SHA bindings required for warnings')
        self.output=Path(output);self.bound={'source_pdf_sha256':source_pdf_sha256,'page':page,
            'page_text_sha256':page_text_sha256,'proof_profile_sha256':proof_profile_sha256}
        self.stage='candidate_read';self.events=[];self.dropped=0;self.link=None
    def record(self,kind,message,category,filename,lineno,level='WARNING'):
        if len(self.events)>=MAX_EVENTS:self.dropped+=1;return
        self.events.append({'kind':kind,'processing_stage':self.stage,'category':category,
            'filename':str(filename),'lineno':lineno,'level':level,
            'message':message[:MAX_MESSAGE_CODEPOINTS],'message_codepoints':len(message),
            'message_sha256':hashlib.sha256(message.encode('utf-8','surrogatepass')).hexdigest(),
            'message_truncated':len(message)>MAX_MESSAGE_CODEPOINTS})
    def __enter__(self):
        # Python 3.12 showwarning is process-global. Serialize our scoped hooks
        # within one worker; independent worker processes remain parallel.
        SCOPE_LOCK.acquire()
        self.token=ACTIVE.set(self);self.previous_showwarning=warnings.showwarning
        # Preserve all warning filters and original output. Only actually emitted
        # warnings are observed; unrelated threads/scopes are never attributed.
        def observed(message,category,filename,lineno,file=None,line=None):
            if ACTIVE.get()is self:self.record('python_warning',str(message),category.__module__+'.'+category.__qualname__,filename,lineno)
            return self.previous_showwarning(message,category,filename,lineno,file=file,line=line)
        self.showwarning=observed;warnings.showwarning=observed
        self.handler=_Handler(self);logging.getLogger().addHandler(self.handler)
        return self
    def __exit__(self,*error):
        logging.getLogger().removeHandler(self.handler)
        if warnings.showwarning is self.showwarning:warnings.showwarning=self.previous_showwarning
        ACTIVE.reset(self.token)
        SCOPE_LOCK.release()
        if self.events or self.dropped:
            folder=self.output/'warning-provenance';folder.mkdir(parents=True,exist_ok=True)
            path=folder/f'page-{self.bound["page"]:06}.json'
            if path.exists():raise ValueError('New warning receipt required')
            document={'contract_version':CONTRACT,**self.bound,'events':self.events,'dropped_events':self.dropped,
                'limits':{'events':MAX_EVENTS,'message_codepoints':MAX_MESSAGE_CODEPOINTS},
                'scope':'emitted_warnings_only_not_all_reader_failures',
                'candidate_only':True,'source_quality_clearance':False,'answer_publication_eligible':False,
                'legal_review':'not_run','source_text_modified':False}
            data=(json.dumps(document,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode('ascii')
            path.write_bytes(data)
            self.link={'path':str(path.relative_to(self.output)),'sha256':hashlib.sha256(data).hexdigest()}
        return False


def verify(link,output,source_pdf_sha256,page,page_text_sha256,proof_profile_sha256):
    from .native_region_diagnostics import _checked_json
    document=_checked_json(output,link)
    expected={'contract_version':CONTRACT,'source_pdf_sha256':source_pdf_sha256,'page':page,
        'page_text_sha256':page_text_sha256,'proof_profile_sha256':proof_profile_sha256,
        'candidate_only':True,'source_quality_clearance':False,'answer_publication_eligible':False,
        'legal_review':'not_run','source_text_modified':False}
    if type(document.get('page'))is not int or any(document.get(k)!=v for k,v in expected.items()):raise ValueError('Warning source/scope/authority drift')
    if len(document.get('events',[]))>MAX_EVENTS or any(len(e.get('message',''))>MAX_MESSAGE_CODEPOINTS for e in document['events']):raise ValueError('Warning bounds exceeded')
    return document
