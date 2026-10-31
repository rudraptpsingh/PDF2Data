"""Run the PDF2Data structure CLI over an existing immutable reading batch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    p=argparse.ArgumentParser();p.add_argument('--documents',type=Path,required=True);p.add_argument('--layout',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise ValueError('batch output exists')
    args.output.mkdir(parents=True);results=[];start=time.perf_counter()
    launcher=Path(__file__).resolve().parents[1]/'pdf2data'
    for document in sorted(args.documents.glob('*/document.json')):
        before=hashlib.sha256(document.read_bytes()).hexdigest();dest=args.output/document.parent.name
        command=[str(launcher),'pdf-structure-export','--document',str(document.resolve()),'--layout',str(args.layout.resolve()),'--output',str(dest.resolve()),'--json']
        done=subprocess.run(command,env={**os.environ,'PDF2DATA_PYTHON':sys.executable},capture_output=True,text=True,timeout=120)
        result=dict(id=document.parent.name,command=command,returncode=done.returncode,raw_document_sha256=before,
                    input_unchanged=hashlib.sha256(document.read_bytes()).hexdigest()==before,stderr=done.stderr)
        if done.returncode==0:result['receipt']=json.loads(done.stdout)
        else:result['stdout']=done.stdout
        results.append(result)
        report=dict(documents=len(results),successful=sum(r['returncode']==0 for r in results),
                    errors=sum(r['returncode']!=0 for r in results),all_inputs_unchanged=all(r['input_unchanged'] for r in results),
                    wall_seconds=time.perf_counter()-start,semantic_fidelity_evaluated=False,results=results)
        (args.output/'batch.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(dict(id=result['id'],returncode=done.returncode)),flush=True)
    if not results:raise ValueError('no immutable document exports found')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))

if __name__=='__main__':main()
