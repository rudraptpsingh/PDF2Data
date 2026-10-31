"""Source-first review links and per-PDF outcome changes on a frozen suite."""
import argparse
from collections import Counter
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys


def load(path):return json.loads(Path(path).read_text())
def rows(path):return [json.loads(line) for line in Path(path).read_text().split('\n') if line.strip()]


def main():
    p=argparse.ArgumentParser()
    for name in ('suite','before-score','after-score','structured','layout','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();suite=load(args.suite);before=load(args.before_score/'report.json');after=load(args.after_score/'report.json')
    if not before['valid_evaluation'] or not after['valid_evaluation'] or before['suite_sha256']!=suite['suite_sha256'] or after['suite_sha256']!=suite['suite_sha256']:raise ValueError('incompatible source score suites')
    if args.output.exists():raise ValueError('source map exists')
    old={r['id']:r for r in rows(args.before_score/'results.jsonl')};new={r['id']:r for r in rows(args.after_score/'results.jsonl')}
    if set(old)!=set(new):raise ValueError('benchmark denominators differ')
    args.output.mkdir(parents=True);mapped=[];launcher=Path(__file__).resolve().parents[1]/'pdf2data'
    def href(path):return escape(os.path.relpath(path.resolve(),args.output.resolve()),quote=True)
    for case in suite['cases']:
        export=args.structured/case['id'];raw=export/'raw-region-document.json';dest=args.output/'pages'/case['id']
        command=[str(launcher),'pdf-document-map','--document',str(raw.resolve()),'--layout',str(args.layout.resolve()),'--output',str(dest.resolve()),'--json']
        done=subprocess.run(command,env={**os.environ,'PDF2DATA_PYTHON':sys.executable},capture_output=True,text=True,timeout=120)
        if done.returncode:raise ValueError('source map failed: '+case['id']+' '+done.stderr+' '+done.stdout)
        ids=[i for i,r in old.items() if r['pdf']==case['pdf']]
        changed=[dict(id=i,before=old[i]['coverage_adjusted_pass'],after=new[i]['coverage_adjusted_pass'],explanation=new[i].get('explanation','')) for i in ids if old[i]['coverage_adjusted_pass']!=new[i]['coverage_adjusted_pass']]
        manifest=load(export/'manifest.json')
        mapped.append(dict(id=case['id'],pdf=case['pdf'],categories=case['categories'],tests=len(ids),
                           before_pass=sum(old[i]['coverage_adjusted_pass'] for i in ids),after_pass=sum(new[i]['coverage_adjusted_pass'] for i in ids),
                           changes=changed,failed_reading_regions=manifest['failed_reading_regions'],visual_review_regions=manifest['visual_review_regions'],
                           source_map=str((dest/'index.html').resolve()),structured_document=str((export/'document.html').resolve()),source_pdf=str((export/'source.pdf').resolve()),
                           mapping=json.loads(done.stdout)))
    report=dict(suite_sha256=suite['suite_sha256'],documents=len(mapped),outcomes=len(old),
                before_macro=before['coverage_adjusted_category_macro'],after_macro=after['coverage_adjusted_category_macro'],
                improved_outcomes=sum(c['after'] and not c['before'] for r in mapped for c in r['changes']),
                regressed_outcomes=sum(c['before'] and not c['after'] for r in mapped for c in r['changes']),
                recognition_rerun=False,rows=mapped)
    (args.output/'map.json').write_text(json.dumps(report,indent=2)+'\n')
    html=['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PDF2Data benchmark source map</title>',
          '<style>body{font:14px/1.5 system-ui;max-width:1300px;margin:30px auto;padding:20px;background:#f4f6f4;color:#243b30}table{border-collapse:collapse;width:100%;background:white}td,th{padding:10px;border-bottom:1px solid #e0e7e1;text-align:left}thead{position:sticky;top:0;background:#e9efea}a{color:#24684c}small{color:#65746b}.links{white-space:nowrap}.gain{background:#e8f7eb}.loss{background:#fff0e8}input{font:inherit;width:100%;padding:12px;margin:16px 0;border:1px solid #cad8cc;border-radius:8px}</style>',
          '<h1>PDF2Data · 70 original PDFs → extracted outputs</h1>',
          f'<p>Same frozen suite · {len(old)} outcomes · prior {report["before_macro"]:.2%} → shared structure {report["after_macro"]:.2%} category macro.</p>',
          f'<p>{report["improved_outcomes"]} checks improved; {report["regressed_outcomes"]} regressed. Recognition was reused. Original OCR failures remain counted. Per-document fractions below are individual rule counts, not the category-macro score.</p>',
          '<input id="filter" aria-label="Filter documents" placeholder="Filter filename or category"><table><thead><tr><th>Original test PDF</th><th>Before</th><th>After</th><th>Review</th><th>Source ↔ output</th></tr></thead><tbody>']
    for row in mapped:
        status=f'{row["failed_reading_regions"]} failed reads; {row["visual_review_regions"]} visual regions' if row['failed_reading_regions'] or row['visual_review_regions'] else 'Unreviewed structure candidate'
        cls='gain' if row['after_pass']>row['before_pass'] else 'loss' if row['after_pass']<row['before_pass'] else ''
        html.append('<tr class="'+cls+'"><td>'+escape(row['pdf'])+'<br><small>'+escape(', '.join(row['categories']))+'</small></td>'+f'<td>{row["before_pass"]}/{row["tests"]}</td><td>{row["after_pass"]}/{row["tests"]}</td><td>'+escape(status)+'</td><td class="links"><a href="'+href(Path(row['source_map']))+'">Map</a> · <a href="'+href(Path(row['structured_document']))+'?mode=source">Compare</a> · <a href="'+href(Path(row['source_pdf']))+'">PDF</a></td></tr>')
    html.append('</tbody></table><p>Mapping proves artifact correspondence; structural/semantic completeness is not certified. Source review remains required.</p><script>document.getElementById("filter").oninput=function(){const q=this.value.toLowerCase();for(const row of document.querySelectorAll("tbody tr"))row.hidden=!row.textContent.toLowerCase().includes(q)}</script>')
    (args.output/'index.html').write_text(''.join(html))
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))

if __name__=='__main__':main()
