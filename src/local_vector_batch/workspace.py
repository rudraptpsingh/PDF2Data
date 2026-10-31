"""Persistent single-user localhost workspace. Processing always goes through CLI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import hashlib,json,mimetypes,re,secrets,shutil,subprocess,sys,tempfile,threading,uuid,zipfile
from urllib.parse import parse_qs,unquote,urlsplit

def now():return datetime.now(timezone.utc).isoformat()

ARTIFACTS=[
    ('source','PDF','Original','receive','input.pdf'),
    ('text','TXT','Page text','extract','l1/document/extraction/pages.jsonl'),
    ('structure','{}','Structure','extract','l1/document/structure.json'),
    ('tables','▦','Tables','extract','l1/document/extraction/tables.json'),
    ('quality','✓','Checks','verify','l1/document/quality.json'),
    ('markdown','MD','Markdown','export','readable/document.md'),
    ('html','</>','Document','export','readable/document.html'),
    ('layout','▧','Layout','export','readable/layout.html'),
    ('word','W','Word','export','readable/document.docx'),
    ('json','{}','JSON','export','readable/data.json'),
    ('relations','⋈','Relations','export','readable/relations.json')]

class Workspace:
    def __init__(self,root,docx_python=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        marker=self.root/'workspace.json'
        if not marker.exists():
            if any(p.name!='.workspace.lock' for p in self.root.iterdir()):raise ValueError('choose a new or existing PDF2Data workspace directory')
            marker.write_text(json.dumps({'schema_version':'pdf2data.workspace.v1','created_at':now()}))
        if json.loads(marker.read_text()).get('schema_version')!='pdf2data.workspace.v1':raise ValueError('unknown workspace marker')
        self.jobs_dir=self.root/'jobs';self.jobs_dir.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.changed=threading.Condition(self.lock);self.revision=0
        self.executor=ThreadPoolExecutor(max_workers=1);self.docx_python=docx_python
        for state in self.jobs():
            if state['status'] in ('queued','running'):
                state.update(status='interrupted',error='Processing was interrupted. Retry creates a fresh attempt and preserves this run.')
                for step in state['steps']:
                    if step['status']=='running':step.update(status='failed',finished_at=now())
                self.save(state)

    def directory(self,job_id):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('invalid job identity')
        return self.jobs_dir/job_id

    def save(self,state):
        with self.lock:
            state['updated_at']=now();directory=self.directory(state['id']);directory.mkdir(exist_ok=True)
            temp=directory/'state.tmp';temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(directory/'state.json')
            self.revision+=1;self.changed.notify_all()

    def get(self,job_id):
        with self.lock:return json.loads((self.directory(job_id)/'state.json').read_text())

    def jobs(self):
        with self.lock:
            return sorted([json.loads(p.read_text()) for p in self.jobs_dir.glob('*/state.json')],key=lambda s:s['created_at'],reverse=True)

    def describe(self,state):
        stages={s['id']:s['status'] for s in state['steps']};base=self.directory(state['id']);items=[]
        for key,icon,label,stage,relative in ARTIFACTS:
            file=base/relative
            if key=='word' and not state.get('word_requested',bool(self.docx_python)) and not file.is_file():continue
            available=stages.get(stage)=='completed' and file.is_file() and base in file.resolve().parents
            items.append({'id':key,'icon':icon,'label':label,'stage':stage,'available':available,
              'bytes':file.stat().st_size if available else None,'url':f'/jobs/{state["id"]}/artifacts/{key}' if available else None})
        return {**state,'artifacts':items}

    def artifact_file(self,job_id,key):
        state=self.describe(self.get(job_id));artifact=next((a for a in state['artifacts'] if a['id']==key),None)
        if artifact is None or not artifact['available']:raise ValueError('artifact is not available yet')
        spec=next(a for a in ARTIFACTS if a[0]==key)
        file=self.directory(job_id)/spec[4]
        if self.directory(job_id) not in file.resolve().parents:raise ValueError('invalid artifact path')
        return file

    def submit(self,source,options,retry_of=None):
        from .legal_l1 import FAMILIES,read_manifest
        title=options.get('title','').strip();family=options.get('family','generic');ocr=options.get('ocr','auto');url=options.get('url','').strip()
        if not title or len(title)>240:raise ValueError('provide a document title of 1 to 240 characters')
        if family not in FAMILIES or ocr not in ('auto','off','vision','tesseract','rapidocr'):raise ValueError('invalid document type or OCR setting')
        with Path(source).open('rb') as stream:
            if b'%PDF-' not in stream.read(1024):raise ValueError('the selected file is not a PDF')
        job_id=uuid.uuid4().hex;directory=self.directory(job_id);directory.mkdir();shutil.copyfile(source,directory/'input.pdf')
        digest=hashlib.sha256((directory/'input.pdf').read_bytes()).hexdigest()
        declared={'id':'document','path':str(directory/'input.pdf'),'sha256':digest,'family':family,'title':title}
        if url:declared['url']=url
        manifest=directory/'source-manifest.json';manifest.write_text(json.dumps({'schema_version':'powerlex.legal-l1.v1','sources':[declared]}))
        try:read_manifest(manifest)
        except Exception:shutil.rmtree(directory);raise
        state={'id':job_id,'title':title,'filename':Path(options.get('filename','document.pdf')).name,'family':family,'ocr':ocr,'source_url':url,
          'source_pdf_sha256':digest,'created_at':now(),'status':'queued','error':None,'retry_of':retry_of,
          'steps':[{'id':key,'label':label,'status':'completed' if key=='receive' else 'pending'} for key,label in [('receive','PDF received'),('extract','Extract text and structure'),('verify','Verify source and coverage'),('export','Create readable documents')]],
          'metrics':None,'publication_eligible':False,'outputs':None,'word_requested':bool(self.docx_python)}
        self.save(state);self.executor.submit(self.process,job_id);return state

    def retry(self,job_id):
        previous=self.get(job_id)
        if previous['status'] not in ('failed','interrupted'):raise ValueError('only failed or interrupted runs can be retried')
        return self.submit(self.directory(job_id)/'input.pdf',{'title':previous['title'],'filename':previous['filename'],'family':previous['family'],'ocr':previous['ocr'],'url':previous['source_url']},retry_of=job_id)

    def process(self,job_id):
        state=self.get(job_id);directory=self.directory(job_id);package=directory/'l1/document'
        commands=[('extract',['legal-l1','--manifest',str(directory/'source-manifest.json'),'--output',str(directory/'l1'),'--ocr',state['ocr'],'--json']),
          ('verify',['verify-legal-l1','--output',str(package),'--json']),
          ('export',['export-readable','--package',str(package),'--output',str(directory/'readable'),'--json']+(['--docx-python',str(self.docx_python)] if self.docx_python else []))]
        try:
            for step_id,args in commands:
                step=next(s for s in state['steps'] if s['id']==step_id);step.update(status='running',started_at=now(),command=['pdf2data',*args]);state['status']='running';self.save(state)
                # Same installed/source-checkout CLI, not a parallel extraction implementation.
                result=subprocess.run([sys.executable,'-m','local_vector_batch',*args],capture_output=True,text=True,timeout=1800)
                (directory/(step_id+'-stdout.json')).write_text(result.stdout);(directory/(step_id+'-stderr.log')).write_text(result.stderr)
                step['exit_code']=result.returncode
                if result.returncode:raise ValueError((result.stderr or result.stdout or 'Processing failed')[-1600:])
                receipt=json.loads(result.stdout)
                if step_id=='extract' and receipt.get('failed'):raise ValueError('One or more source extractions failed; inspect extraction receipt')
                step.update(status='completed',finished_at=now());self.save(state)
                if step_id=='verify':
                    state['metrics']={'pages':receipt['pages'],'paragraphs':receipt['structure']['candidate_units'],'quality_flags':receipt['quality_counts'].get('repair_required',0)+receipt['quality_counts'].get('source_blocked',0),
                      'tables':len(json.loads((package/'extraction/tables.json').read_text())),'withheld_pages':0}
                    self.save(state)
            state['status']='processed_with_flags' if state['metrics']['quality_flags'] else 'processed'
            state['outputs']={name:f'/jobs/{job_id}/files/{name}' for name in ['document.html','layout.html','document.md','data.json','relations.json','quality.json','source.pdf']+(['document.docx'] if self.docx_python else [])}
            state['finished_at']=now();self.save(state)
        except Exception as error:
            state.update(status='failed',error=str(error)[:1800],finished_at=now())
            for step in state['steps']:
                if step['status']=='running':step.update(status='failed',finished_at=now())
            self.save(state)

def serve_workspace(root,port=8771,max_upload_mb=150,docx_python=None):
    if not 1<=port<=65535 or not 1<=max_upload_mb<=1000:raise ValueError('invalid workspace port or upload limit')
    from filelock import FileLock
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    guard=FileLock(root/'.workspace.lock');guard.acquire(timeout=0)
    workspace=Workspace(root,docx_python);token=secrets.token_urlsafe(32)
    page=Path(__file__).with_name('workspace.html').read_text().replace('__TOKEN__',token).replace('__MAX_MB__',str(max_upload_mb))
    class Handler(BaseHTTPRequestHandler):
        def authorized(self):
            host=f'127.0.0.1:{port}'
            return self.headers.get('Host')==host and self.headers.get('Origin') in (None,'http://'+host)
        def send(self,status,data,content_type='application/json',headers=None):
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            for key,value in (headers or {}).items():self.send_header(key,value)
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def json(self,status,value):self.send(status,json.dumps(value,ensure_ascii=False).encode())
        def do_GET(self):
            if not self.authorized():return self.json(403,{'error':'localhost only'})
            path=unquote(urlsplit(self.path).path)
            try:
                if path=='/':return self.send(200,page.encode(),'text/html; charset=utf-8')
                if path=='/ui.css':return self.send(200,Path(__file__).with_name('ui.css').read_bytes(),'text/css')
                if path=='/api/jobs':return self.json(200,{'jobs':[workspace.describe(j) for j in workspace.jobs()[:100]]})
                if path=='/api/events':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                    revision=-1
                    try:
                        while True:
                            with workspace.changed:
                                if revision==workspace.revision:workspace.changed.wait(timeout=12)
                                revision=workspace.revision
                                snapshot={'jobs':[workspace.describe(j) for j in workspace.jobs()[:100]]}
                            self.wfile.write(('event: jobs\ndata: '+json.dumps(snapshot,ensure_ascii=False)+'\n\n').encode());self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError,OSError):return
                match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})',path)
                if match:return self.json(200,workspace.describe(workspace.get(match[1])))
                match=re.fullmatch(r'/jobs/([a-f0-9]{32})/artifacts/([a-z]+)',path)
                if match:
                    file=workspace.artifact_file(match[1],match[2])
                    if match[2] in ('html','layout'):return self.send(302,b'',headers={'Location':f'/jobs/{match[1]}/files/{file.name}'})
                    return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                match=re.fullmatch(r'/jobs/([a-f0-9]{32})/files/(.+)',path)
                if match:
                    state=workspace.get(match[1])
                    if not any(s['id']=='export' and s['status']=='completed' for s in state['steps']):return self.json(409,{'error':'outputs are not available yet'})
                    base=workspace.directory(match[1])/'readable';file=(base/match[2]).resolve()
                    if base not in file.parents:return self.json(403,{'error':'invalid output path'})
                    return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                match=re.fullmatch(r'/jobs/([a-f0-9]{32})/download',path)
                if match:
                    state=workspace.get(match[1])
                    if not state['outputs']:return self.json(409,{'error':'outputs are not available yet'})
                    import io
                    buf=io.BytesIO();base=workspace.directory(match[1])/'readable'
                    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:
                        for file in base.rglob('*'):
                            if file.is_file():archive.write(file,file.relative_to(base))
                    return self.send(200,buf.getvalue(),'application/zip',{'Content-Disposition':'attachment; filename="pdf2data-output.zip"'})
                return self.json(404,{'error':'not found'})
            except (OSError,ValueError):return self.json(404,{'error':'output or job not found'})
        def do_POST(self):
            if not self.authorized() or self.headers.get('X-Local-Token')!=token:return self.json(403,{'error':'local workspace token required'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=max_upload_mb*1024*1024:return self.json(413,{'error':'file exceeds upload limit or is empty'})
                route=urlsplit(self.path);retry=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/retry',route.path)
                if retry:
                    if length>1024:return self.json(413,{'error':'retry request too large'})
                    self.rfile.read(length);return self.json(202,workspace.retry(retry[1]))
                if route.path!='/api/jobs':return self.json(404,{'error':'not found'})
                if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':return self.json(415,{'error':'select a PDF'})
                options={k:v[0] for k,v in parse_qs(route.query,keep_blank_values=True).items()}
                with tempfile.TemporaryDirectory(prefix='.upload-',dir=root) as tmp:
                    source=Path(tmp)/'input.pdf';remaining=length;self.connection.settimeout(60)
                    with source.open('wb') as stream:
                        while remaining:
                            chunk=self.rfile.read(min(1024*1024,remaining))
                            if not chunk:raise ValueError('upload interrupted; choose the PDF and retry')
                            stream.write(chunk);remaining-=len(chunk)
                    state=workspace.submit(source,options)
                return self.json(202,state)
            except (ValueError,OSError) as error:return self.json(400,{'error':str(error)[:240]})
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'PDF2Data workspace: http://127.0.0.1:{port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();workspace.executor.shutdown(wait=True);guard.release()
