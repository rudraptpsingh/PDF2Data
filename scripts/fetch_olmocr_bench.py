"""Download the pinned public inputs; extraction still runs through pdf2data CLI.

Install huggingface-hub==0.36.2 separately. Downloads are resumable, serial and
bounded on rate limits. Dataset and source-document licences still apply.
"""
from pathlib import Path
import argparse,hashlib,json,time
from huggingface_hub import hf_hub_download

REVISION='54a96a6fb6a2bd3b297e59869491db4d3625b711'
CATEGORIES=('arxiv_math','headers_footers','long_tiny_text','multi_column','old_scans','old_scans_math','table_tests')

def fetch(root,per_category,seed):
    def download(name):
        for attempt in range(4):
            try:return Path(hf_hub_download(repo_id='allenai/olmOCR-bench',repo_type='dataset',revision=REVISION,filename=name,local_dir=root))
            except Exception as error:
                status=getattr(getattr(error,'response',None),'status_code',None)
                if status!=429 or attempt==3:raise
                print(json.dumps({'event':'rate_limit_backoff','seconds':60,'attempt':attempt+1}),flush=True);time.sleep(60)
    chosen=set()
    for category in CATEGORIES:
        path=download('bench_data/'+category+'.jsonl')
        names={json.loads(line)['pdf'] for line in path.read_text().split('\n') if line.strip()}
        ranked=sorted(names,key=lambda n:hashlib.sha256((seed+'\0'+n).encode()).hexdigest())
        chosen.update(ranked if per_category is None else ranked[:per_category])
    for index,name in enumerate(sorted(chosen),1):
        download('bench_data/pdfs/'+name)
        print(json.dumps({'event':'downloaded','documents':index,'total':len(chosen)}),flush=True)
    print(json.dumps({'dataset_revision':REVISION,'downloaded_documents':len(chosen),'dataset_root':str((root/'bench_data').resolve())}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    group=parser.add_mutually_exclusive_group();group.add_argument('--per-category',type=int,default=10);group.add_argument('--full',action='store_true')
    parser.add_argument('--seed',default='pdf2data-v1');args=parser.parse_args()
    if args.per_category<1:parser.error('--per-category must be positive')
    fetch(args.output,None if args.full else args.per_category,args.seed)
