"""Controlled lexical evidence-retrieval diagnostic, not an answer accuracy eval."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import re


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def tokens(text):
    # Keep explicit provision references intact; 1.5 must not become common '1'
    # plus '5'. The same tokenizer and function-word filter serve all variants.
    stop={'a','an','the','is','are','was','were','be','been','to','of','and','or','in','on','at','for','with','by','as','it','its','what','which','how','do','does','under'}
    return [t for t in re.findall(r'\d+(?:\.\d+)+|\w+',text.casefold()) if t not in stop]


def rank(units,question):
    counts=[Counter(tokens(u['text'])) for u in units];sizes=[sum(c.values()) for c in counts]
    avg=sum(sizes)/max(1,len(sizes));freq=Counter(t for c in counts for t in c)
    query=set(tokens(question));scored=[]
    for unit,c,length in zip(units,counts,sizes):
        score=sum(math.log(1+(len(units)-freq[t]+.5)/(freq[t]+.5))*c[t]*2.2/
                  (c[t]+1.2*(.25+.75*length/max(avg,1))) for t in query if c[t])
        if score:scored.append((score,unit))
    return [u for score,u in sorted(scored,key=lambda pair:(-pair[0],pair[1]['id']))]


def choose(units,question,max_chars,seed_k,closure=None):
    ranked=rank(units,question);byid={u['id']:u for u in units};selected=[];omitted=[];used=0
    def add(unit,required=False):
        nonlocal used
        cost=len(unit['text'])+len(unit['region_id'])+4
        if unit['id'] in selected:return
        if used+cost<=max_chars:selected.append(unit['id']);used+=cost
        elif required:omitted.append(unit['region_id'])
    for unit in ranked[:seed_k]:add(unit,True)
    if closure:
        for bid in closure([u['id'] for u in ranked[:seed_k]]):
            if bid in byid:add(byid[bid],True)
    for unit in ranked[seed_k:]:add(unit)
    chosen=[byid[i] for i in selected]
    return dict(selected_regions=[u['region_id'] for u in chosen],chars=used,
                required_candidate_omissions=omitted,evidence=chosen)


def evaluate(raw_path,structured,cases_path,output,max_chars=6000,seed_k=2):
    if not 500<=max_chars<=200000 or not 1<=seed_k<=10:raise ValueError('invalid retrieval budget')
    structured,output=Path(structured),Path(output)
    if output.exists():raise ValueError('evaluation output already exists')
    raw,manifest,cases=load(raw_path),load(structured/'manifest.json'),load(cases_path)
    if manifest['input_document_sha256']!=sha(raw_path):raise ValueError('raw and structured versions differ')
    if cases['source_pdf_sha256']!=raw['source_pdf_sha256']:raise ValueError('cases belong to another source')
    for name,digest in manifest['files'].items():
        path=(structured/name).resolve()
        if not path.is_relative_to(structured.resolve()) or sha(path)!=digest:raise ValueError('structured artifact changed')
    data=load(structured/'data.json');projections=load(structured/'search-projections.json')['units']
    projected_by_region={u['region_id']:u for u in projections}
    raw_units=[dict(id=projected_by_region[b['region_id']]['id'] if b['region_id'] in projected_by_region else b['region_id'],
                    region_id=b['region_id'],text=b['text'],page=p['page'])
               for p in raw['pages'] for b in p['blocks'] if b['scope']=='body']
    blocks={b['id']:b for b in data['document_blocks']['blocks']}
    groups={n['id']:n for n in data['structural_candidates']['nodes']}
    related={bid:[] for bid in blocks}
    for edge in data['structural_candidates']['edges']:
        if edge['type'] in ('continues','caption_of','explains','list_child_of'):
            if edge['from'] in blocks and edge['to'] in blocks:
                related[edge['from']].append(edge['to']);related[edge['to']].append(edge['from'])
    def closure(seeds):
        result=[]
        for bid in seeds:
            block=blocks[bid]
            for gid in [block.get('quote_id'),block.get('illustration_id'),block.get('scenario_id'),*block['parent_unit_ids']]:
                if gid in groups:result.extend(groups[gid]['member_ids'])
            result.extend(related[bid])
        return list(dict.fromkeys(result))
    ids={u['region_id'] for u in raw_units};results=[]
    if len({c['id'] for c in cases['cases']})!=len(cases['cases']):raise ValueError('duplicate evaluation case')
    for case in cases['cases']:
        required=set(case['required_regions'])
        if not required or not required<=ids:raise ValueError('invalid source-review case anchors')
        variants={}
        for name,units,expand in [('A_flat',raw_units,None),('B_projection',projections,None),('C_structure_expansion',projections,closure)]:
            selected=choose(units,case['question'],max_chars,seed_k,expand)
            found=required&set(selected['selected_regions'])
            variants[name]={**selected,'required_regions_retrieved':sorted(found),'required_region_recall':len(found)/len(required),
                            'complete_evidence':found==required}
        results.append(dict(id=case['id'],question=case['question'],required_regions=sorted(required),variants=variants))
    summary={name:dict(complete_evidence=sum(r['variants'][name]['complete_evidence'] for r in results),
                       cases=len(results),mean_required_region_recall=sum(r['variants'][name]['required_region_recall'] for r in results)/len(results),
                       mean_context_chars=sum(r['variants'][name]['chars'] for r in results)/len(results))
             for name in ('A_flat','B_projection','C_structure_expansion')}
    report=dict(schema_version='pdf2data.structure-retrieval-eval.v1',raw_sha256=sha(raw_path),
                structured_manifest_sha256=sha(structured/'manifest.json'),cases_sha256=sha(cases_path),
                implementation_sha256=sha(__file__),max_context_chars=max_chars,seed_k=seed_k,
                method='same_BM25_k1_1.2_b_0.75_with_budget_filling_no_clipping',
                annotation_provenance=cases['provenance'],summary=summary,results=results,
                generator_used=False,semantic_retrieval_used=False,answer_accuracy_measured=False,
                independent_holdout=False,publication_eligible=False)
    output.mkdir(parents=True);(output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    md=['# Source-structure retrieval diagnostic','','Development fixture; lexical retrieval only. No generation, dense search or legal correctness score.',
        '',f'Same context cap: {max_chars} characters, including region identifiers. No selected unit is clipped. Actual consumption differs.',
        '','| Variant | Complete evidence | Mean required-region recall | Mean context chars |','| --- | ---: | ---: | ---: |']
    for name,s in summary.items():md.append(f'| {name} | {s["complete_evidence"]}/{s["cases"]} | {s["mean_required_region_recall"]:.3f} | {s["mean_context_chars"]:.0f} |')
    md+=['','| Case | A | B | C |','| --- | --- | --- | --- |']
    for row in results:md.append('| '+row['id']+' | '+' | '.join(str(row['variants'][name]['complete_evidence']) for name in summary)+' |')
    (output/'comparison.md').write_text('\n'.join(md)+'\n')
    return dict(output=str(output.resolve()),summary=summary,answer_accuracy_measured=False)
