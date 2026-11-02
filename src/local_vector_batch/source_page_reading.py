"""Bounded named AskMill image readings. Completion is never source approval."""
from pathlib import Path
import base64
import hashlib
import json
import os
import shutil
import subprocess

from .cached_l1 import inventory
from .hashing import file_sha256

SCHEMA='pdf2data.source-page-reading.v1'
FLAGS=dict(source_quality_approval_granted=False,publication_eligible=False,
           parent_modified=False,whole_page_quality_changed=False,legal_review='not_run')


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    with Path(path).open('x',encoding='utf-8')as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def require(value,message):
    if not value:raise ValueError(message)
def subset(expected,actual):
    return (isinstance(actual,dict)and all(k in actual and subset(v,actual[k])for k,v in expected.items()))if isinstance(expected,dict)else expected==actual
def outside(output,*inputs):
    output=Path(output).resolve()
    require(not output.exists()and all(not output.is_relative_to(Path(p).resolve())and not Path(p).resolve().is_relative_to(output)for p in inputs),'New immutable output outside all inputs required')
    return output


def invoke(command,path,timeout=150):
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=timeout,
                              env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
        value=dict(command=command,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    except subprocess.TimeoutExpired as error:
        value=dict(command=command,exit_code=None,stdout=str(error.stdout or''),stderr=str(error.stderr or''),status='caller_timeout')
    if path is not None:write(path,value)
    return value


def profile_snapshot(data,name):
    require(isinstance(data,dict),'AskMill profile configuration object required')
    profile=data.get('profiles',{}).get(name);leases=data.get('lease_paths')
    require(isinstance(profile,dict)and profile.get('provider')=='mlx-vlm'and profile.get('ownership')=='worker','Named local owned image worker required; no provider fallback')
    require('vision'in profile.get('capabilities',[])and isinstance(profile.get('revision'),str),'Pinned vision profile required')
    require(isinstance(profile.get('model_path'),str)and Path(profile['model_path']).is_dir(),'Existing local model directory required; downloads are prohibited')
    require(isinstance(leases,list)and 1<=len(leases)<=8 and all(isinstance(p,str)and Path(p).is_absolute()for p in leases),'Configured shared absolute host lease paths required')
    return dict(schema_version=data['schema_version'],profiles={name:profile},lease_paths=leases)


def run(package,page,prompt,profile,output,*,askmill='askmill',config=None,dpi=200,max_tokens=2048,timeout=120,scope='whole_page_literal_candidate'):
    from .legal_l1 import verify_document,rows
    from .field_witness import render
    import pypdfium2 as pdfium
    package,prompt=Path(package).resolve(),Path(prompt).resolve()
    output=outside(output,package,prompt,*([config]if config else[]))
    require(type(page)is int and page>0,'Positive physical page required')
    require(type(max_tokens)is int and 1<=max_tokens<=2048 and 0<timeout<=120,'Bounded reading requires max_tokens<=2048 and timeout<=120')
    require(type(dpi)is int and 120<=dpi<=300,'Render DPI must be 120..300')
    require(scope in{'whole_page_literal_candidate','visible_english_literal_candidate'},'Declare literal reading scope')
    text=prompt.read_text(encoding='utf-8');require(text.startswith('OCR:')and 4<=len(text)<=16000,'Explicit bounded literal OCR: prompt required')
    verify_document(package);before=inventory(package);selected=next((r for r in rows(package/'extraction/pages.jsonl')if r['page']==page),None)
    require(selected is not None,'Physical source page unavailable')
    executable=shutil.which(str(askmill));require(executable is not None,'AskMill executable unavailable')
    prefix=[executable]+(['--config',str(Path(config).resolve())]if config else[])
    listed=invoke(prefix+['list'],None)
    require(listed['exit_code']==0,'AskMill profile listing failed; receipt retained')
    snapshot=profile_snapshot(json.loads(listed['stdout']),profile)
    outside(output,snapshot['profiles'][profile]['model_path'],*snapshot['lease_paths'])
    output.mkdir(parents=True);(output/'invocations').mkdir();(output/'store').mkdir()
    write(output/'invocations/profiles.json',listed);write(output/'profile.json',snapshot)
    prefix=[executable,'--config',str(output/'profile.json'),'--store',str(output/'store')]
    doctor=invoke(prefix+['doctor','--profile',profile],output/'invocations/doctor.json')
    require(doctor['exit_code']==0 and json.loads(doctor['stdout']).get('status')=='configuration_valid','AskMill doctor failed; no inference attempted')
    with pdfium.PdfDocument(package/'original.pdf')as pdf:
        view=pdf[page-1]
        try:width,height=view.get_size()
        finally:view.close()
    region=dict(page=page,bbox_pdf_points=[0,0,width,height]);image,pixels=render(package,region,dpi)
    (output/'source.png').write_bytes(image);(output/'prompt.txt').write_bytes(prompt.read_bytes())
    request=dict(schema_version=SCHEMA,source_pdf_sha256=file_sha256(package/'original.pdf'),parent_manifest_sha256=file_sha256(package/'l1-package.json'),page=page,
        previous_page_text_sha256=selected['text_sha256'],selected_method=selected['method'],scope=scope,
        region=region,render_dpi=dpi,crop_bbox_pixels=pixels,source_image_sha256=sha(image),prompt_sha256=file_sha256(prompt),
        profile=profile,profile_sha256=file_sha256(output/'profile.json'),askmill_executable_sha256=file_sha256(executable),
        max_tokens=max_tokens,timeout=timeout,source_files=before,**FLAGS)
    from importlib.metadata import version
    request['renderer_versions']={name:version(name)for name in('pypdfium2','pillow')}
    request['producer_implementation_sha256']=file_sha256(Path(__file__))
    write(output/'request.json',request)
    command=prefix+['run','--profile',profile,'--input',str(output/'prompt.txt'),'--image',str(output/'source.png'),'--max-tokens',str(max_tokens),'--timeout',str(timeout)]
    call=invoke(command,output/'invocations/run.json',timeout+30)
    try:result=json.loads(call['stdout'])
    except (ValueError,TypeError):result=dict(status='runner_failed',complete=False,text='')
    if not isinstance(result,dict)or not isinstance(result.get('text',''),str):result=dict(status='runner_failed',complete=False,text='')
    write(output/'result.json',result)
    (output/'reading.txt').write_bytes(result.get('text','').encode('utf-8'))
    require(inventory(package)==before and file_sha256(prompt)==request['prompt_sha256'],'Immutable source/prompt changed during reading')
    complete=call['exit_code']==0 and result.get('schema_version')=='askmill.result.v1'and result.get('status')=='completed'and result.get('complete')is True and bool(result.get('text','').strip())
    write(output/'manifest.json',dict(schema_version=SCHEMA,files=inventory(output),status='candidate'if complete else'held_incomplete',runner_calls=1,
        fresh_inference_calls=(int(not result.get('replayed',False))if result.get('schema_version')=='askmill.result.v1'else None),**FLAGS))
    verified=verify(output,package)
    return {**verified,'runner_calls_performed':1,'inference_calls_performed':verified['recorded_fresh_inference_calls']}


def verify(bundle,package):
    require(package is not None,'Explicit parent package required for original raster verification')
    bundle=Path(bundle);manifest=read(bundle/'manifest.json');request=read(bundle/'request.json');result=read(bundle/'result.json')
    require(manifest.get('schema_version')==request.get('schema_version')==SCHEMA,'Unknown source reading schema')
    files=inventory(bundle);files.pop('manifest.json',None);require(files==manifest['files'],'Reading artifact inventory differs')
    require(all(manifest.get(k)==v and request.get(k)==v for k,v in FLAGS.items()),'Reading cannot grant source approval')
    require(file_sha256(bundle/'source.png')==request['source_image_sha256']and file_sha256(bundle/'prompt.txt')==request['prompt_sha256']and file_sha256(bundle/'profile.json')==request['profile_sha256'],'Reading input identity differs')
    snapshot=read(bundle/'profile.json');profile=snapshot['profiles'][request['profile']]
    call=read(bundle/'invocations/run.json')
    complete=call['exit_code']==0 and result.get('schema_version')=='askmill.result.v1'and result.get('status')=='completed'and result.get('complete')is True and bool(result.get('text','').strip())
    require(manifest['status']==('candidate'if complete else'held_incomplete'),'Reading terminal status differs from runner result')
    require(manifest.get('runner_calls',manifest.get('actual_inference_attempts'))==1,'Reading must retain one runner invocation')
    fresh=int(not result.get('replayed',False))if result.get('schema_version')=='askmill.result.v1'else None
    require('fresh_inference_calls'not in manifest or manifest['fresh_inference_calls']==fresh,'Reading inference count differs from runner receipt')
    require((bundle/'reading.txt').read_bytes().decode('utf-8')==result.get('text',''),'Literal reading differs from raw model output')
    require(json.loads(call['stdout'])==result if result.get('schema_version')=='askmill.result.v1'else True,'Raw runner invocation/result differs')
    if result.get('schema_version')=='askmill.result.v1':
        require(result.get('status')in{'completed','incomplete'}and result.get('domain_acceptance')=='unverified','Unknown or improperly approved runner output')
        require(result.get('model')==profile['model']and result.get('revision')==profile['revision'],'Reading model/revision differs from selected profile')
        key=result.get('receipt_id');require(isinstance(key,str)and len(key)==64 and all(c in '0123456789abcdef'for c in key),'AskMill receipt identity must be a SHA256, never a path')
        record=read(bundle/'store'/(key+'.json'));content=record['content']
        require(sha(canonical(content))==record['sha256']and content['key']==result['receipt_id'],'AskMill receipt integrity differs')
        require(sha(canonical(dict(request=content['request'],profile=content['profile'],runtime=content['runtime'])))==content['key'],'AskMill request/profile/runtime identity differs')
        old=content['result'];require(all(result.get(k)==v for k,v in old.items()if k!='replayed'),'AskMill raw result differs')
        captured=content['request'];require(captured['prompt']==(bundle/'prompt.txt').read_text(encoding='utf-8')and captured['max_tokens']==request['max_tokens']and captured['timeout']==request['timeout'],'AskMill bounded prompt/request differs')
        images=captured['images'];require(len(images)==1 and images[0]['sha256']==request['source_image_sha256']and base64.b64decode(images[0]['base64'])==(bundle/'source.png').read_bytes(),'AskMill source image differs')
        require(subset(profile,content['profile']),'AskMill selected profile differs')
    if package is not None:
        from .legal_l1 import verify_document
        from .field_witness import render
        package=Path(package);verify_document(package)
        from .legal_l1 import rows
        import pypdfium2 as pdfium
        number=request['page'];require(type(number)is int and number>0 and request['region']['page']==number,'Reading physical page identity differs')
        selected=next((p for p in rows(package/'extraction/pages.jsonl')if p['page']==number),None)
        require(selected is not None and selected['text_sha256']==request['previous_page_text_sha256']and selected['method']==request['selected_method'],'Reading selected parent page differs')
        with pdfium.PdfDocument(package/'original.pdf')as pdf:
            view=pdf[number-1]
            try:width,height=view.get_size()
            finally:view.close()
        require(request['region']['bbox_pdf_points']==[0,0,width,height],'Reading must retain the complete physical source frame')
        require(inventory(package)==request['source_files']and file_sha256(package/'l1-package.json')==request['parent_manifest_sha256']and file_sha256(package/'original.pdf')==request['source_pdf_sha256'],'Reading parent/source differs')
        image,pixels=render(package,request['region'],request['render_dpi'])
        require(image==(bundle/'source.png').read_bytes()and pixels==request['crop_bbox_pixels'],'Reading original raster/geometry differs')
    return dict(integrity='passed',status=manifest['status'],output=str(bundle),page=request['page'],source_pdf_sha256=request['source_pdf_sha256'],
                reading_sha256=file_sha256(bundle/'reading.txt'),complete=complete,original_raster_reconstructed=True,runner_calls_performed=0,inference_calls_performed=0,
                recorded_runner_calls=manifest.get('runner_calls',manifest.get('actual_inference_attempts')),
                recorded_fresh_inference_calls=(int(not result.get('replayed',False))if result.get('schema_version')=='askmill.result.v1'else None),**FLAGS)
