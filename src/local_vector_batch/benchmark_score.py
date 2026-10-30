"""Thin subprocess: execute pinned upstream rule classes without model packages.

No evaluator source is copied or modified. Coverage-adjusted results are reported
separately from raw official-rule pass rates; neither is a leaderboard submission.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import defaultdict,Counter
import json,sys,time,hashlib,platform,importlib.metadata


def main(request_path):
    request=json.loads(Path(request_path).read_text());output=Path(request['output']);upstream=Path(request['upstream'])
    sys.path.insert(0,str(upstream))
    import olmocr.bench.katex.render as renderer
    renderer.equation_cache=renderer.EquationCache(str(output/'equations.sqlite'))
    from olmocr.bench.tests import load_single_test,BaselineTest
    probe=renderer.render_equation('x^{2}+1',use_cache=False)
    if probe is None or probe.error:raise RuntimeError('official math renderer preflight failed')
    suite=json.loads(Path(request['suite']).read_text());run=json.loads((Path(request['run'])/'run.json').read_text())
    cases={c['pdf']:c for c in run['cases']};mode=request['mode'];results=[]
    items=list(suite['rules'])
    existing={(i['rule']['pdf'],i['rule']['page']) for i in items if i['rule']['type']=='baseline'}
    for case in suite['cases']:
        for page in range(1,case['pages']+1):
            if (case['pdf'],page) in existing:continue
            items.append({'category':'baseline','rule':{'id':'pdf2data-baseline-'+case['id']+'-'+str(page),'pdf':case['pdf'],'page':page,'type':'baseline'}})
    start=time.perf_counter()
    def evaluate(entry):
        rule=entry['rule'];case=cases.get(rule['pdf']);row={'id':rule['id'],'pdf':rule['pdf'],'page':rule['page'],'category':entry['category'],'type':rule['type'],
                                                       'raw_pass':False,'coverage_adjusted_pass':False,'status':'evaluated'}
        if not case or case['status']!='completed':return row|{'status':'extraction_error'}
        artifact=next((a for a in case['artifacts'] if a['page']==rule['page'] and a['mode']==mode),None)
        if not artifact:return row|{'status':'missing_prediction'}
        path=Path(request['run'])/artifact['path']
        if not path.resolve().is_relative_to(Path(request['run']).resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']:return row|{'status':'prediction_integrity_error'}
        text=path.read_text()
        try:
            test=load_single_test(rule)
            if rule['type']=='math' and getattr(test.reference_render,'error',None):raise ValueError('reference equation failed to render')
        except Exception as error:return row|{'status':'annotation_or_renderer_error','error_type':type(error).__name__}
        try:
            passed,explanation=test.run(text)
            return row|{'raw_pass':bool(passed),'coverage_adjusted_pass':bool(passed) and rule['page'] not in case.get('empty_nonblank_pages',[]),
                        'empty_nonblank':rule['page'] in case.get('empty_nonblank_pages',[]),'explanation':explanation}
        except Exception as error:return row|{'status':'rule_runtime_error','error_type':type(error).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(evaluate,item) for item in items]):results.append(future.result())
    groups=defaultdict(list)
    for row in results:groups[row['category']].append(row)
    categories={key:{'tests':len(values),'raw_pass':sum(r['raw_pass'] for r in values),'adjusted_pass':sum(r['coverage_adjusted_pass'] for r in values),
                     'raw_rate':sum(r['raw_pass'] for r in values)/len(values),'adjusted_rate':sum(r['coverage_adjusted_pass'] for r in values)/len(values)} for key,values in sorted(groups.items())}
    errors=Counter(r['status'] for r in results if r['status']!='evaluated')
    versions={name:importlib.metadata.version(name) for name in ('pypdf','playwright','beautifulsoup4','rapidfuzz','fuzzysearch','numpy')}
    report={'schema_version':'pdf2data.official-rules-score.v1','suite_sha256':suite['suite_sha256'],'dataset':suite['dataset'],'dataset_revision':suite['dataset_revision'],'complete_public_dataset':suite.get('complete_public_dataset',False),'profile':run['config']['profile'],'mode':mode,
            'public_documents':len(suite['cases']),'public_rules':len(suite['rules']),'tests_including_baseline':len(results),'categories':categories,
            'raw_category_macro':sum(c['raw_rate'] for c in categories.values())/len(categories),
            'coverage_adjusted_category_macro':sum(c['adjusted_rate'] for c in categories.values())/len(categories),
            'errors':dict(errors),'valid_evaluation':not any(errors[k] for k in ('annotation_or_renderer_error','rule_runtime_error','prediction_integrity_error')),
            'rule_outcomes':dict(Counter(r['status'] for r in results)),
            'evaluation_seconds':time.perf_counter()-start,'extraction_wall_seconds':run['wall_seconds_this_invocation'],
            'runtime':{'python':sys.version,'platform':platform.platform(),'dependencies':versions,'math_cache':'fresh isolated equations.sqlite; not extraction timing'},
            'method':'pinned upstream load_single_test/run; macro average including per-page baseline; one repeat',
            'leaderboard_submission':False,'publication_eligible':False}
    (output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(results,key=lambda r:(r['category'],r['id']))))
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main(sys.argv[1])
