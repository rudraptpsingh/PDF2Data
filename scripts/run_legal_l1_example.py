"""Reproduce the real legal-source pilot from the archived research PDFs.

PYTHONPATH=src python scripts/run_legal_l1_example.py --source-root PATH --output out/legal-l1-real
No live database, internet fetch or LLM calls. Missing archive files fail.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from local_vector_batch.legal_l1 import SCHEMA, run, verify_document, rows, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();sources=[]
    groups=[
        ('sharing-fifth-2026/sources','download-manifest.json',{
            'DN-SIST-5rd_Amend.pdf':('sharing-july-draft','draft',7),
            'Supplementary-Draft-Notification2026.pdf':('sharing-october-supplement','draft',3),
            'PN-supplementary-notification-sharing-5th.pdf':('sharing-october-notice','notice',1),
            'EM_SIST-5rdAmend.pdf':('sharing-july-em','explanatory_memorandum',22),
            'EM-supplementry-sharing-5th.pdf':('sharing-october-em','explanatory_memorandum',6)}),
        ('litigation-2026-10-30','source-manifest.json',{
            '26-MP-2026_300726.pdf':('serentica-cerc-rop','record_of_proceedings',2),
            'APTEL-IA626-Appeal142-2026-09-23.pdf':('serentica-aptel-interim','interim_order',16)}),
        ('full-legal-workflow','source-manifest.json',{
            '389-MP-2018.pdf':('cerc-amount-conflict','judgment',35),
            '379-TD-2023.pdf':('serentica-india2-licence','approval',6)})]
    for folder,manifest,mapping in groups:
        directory=(args.source_root/folder).resolve()
        for original in json.loads((directory/manifest).read_text()):
            if original['file'] not in mapping:continue
            identity,family,count=mapping[original['file']]
            sources.append({'id':identity,'path':str(directory/original['file']), 'sha256':original['sha256'],
                'url':original['url'],'family':family,'expected_pages':count})
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=args.output/'input-manifest.json'
    write_json(manifest,{'schema_version':SCHEMA,'sources':sources,'families':[{'id':'sharing-fifth-draft-context','required_source_ids':[s['id'] for s in sources if s['id'].startswith('sharing-')]}]})
    started=time.perf_counter();report=run(manifest,args.output/'documents',resume=args.resume)
    if report['failed']:print(json.dumps(report,indent=2));return 1
    docs=args.output/'documents'
    aptel=json.loads((docs/'serentica-aptel-interim/structure.json').read_text())
    rop=json.loads((docs/'serentica-cerc-rop/structure.json').read_text())
    unit=next(u for u in aptel['units'] if u['label']=='27')
    rop_unit=next(u for u in rop['units'] if u['label']=='2')
    aptel_text='\n'.join(a['exact_quote'] for a in unit['spans'])
    rop_text='\n'.join(a['exact_quote'] for a in rop_unit['spans'])
    checks={
        'all_nine_sources':report['passed']==9,
        'all_98_physical_pages':sum(d['pages'] for d in report['documents'])==98,
        'all_declared_family_members_supplied':report['all_required_members_supplied'],
        'aptel_paragraph27_crosses_pages15_16':[a['page'] for a in unit['spans']]==[15,16],
        'aptel_condition_and_consequence_retained':all(t in aptel_text for t in ['9,17,50,000','two weeks','revoke','outcome of the Appeal','72.6']),
        'aptel_margins_not_in_paragraph27':'Page 15 of 16' not in aptel_text and 'Order in IA No.' not in aptel_text,
        'rop_paragraph2_crosses_pages1_2':[a['page'] for a in rop_unit['spans']]==[1,2],
        'rop_rejoinder_condition_and_sequence_retained':all(t in rop_text for t in ['if any','two weeks thereafter','replies, two weeks']),
        'rop_footer_not_in_paragraph2':'RoP in Petition' not in rop_text,
        'no_legal_claims_promoted':all(d['structure']['verified_claims']==0 for d in report['documents']),
    }
    regulation=json.loads((docs/'sharing-october-supplement/structure.json').read_text())
    reg_unit=next(u for u in regulation['units'] if u['label']=='1')
    reg_text='\n'.join(a['exact_quote'] for a in reg_unit['spans'])
    checks['supplement_complete_three_page_candidate']=[a['page'] for a in reg_unit['spans']]==[1,2,3]
    checks['supplement_deadlines_conditions_retained']=all(t in reg_text for t in ['31.03.2027','30.04.2027','thirty days','two months','shall not be eligible','shall cease to apply'])
    checks['supplement_page_numbers_are_separate_regions']=sum(r['kind']=='footer_candidate' for r in regulation['regions'])==3
    conflict=json.loads((docs/'cerc-amount-conflict/structure.json').read_text())
    labels=[u['label'] for u in conflict['units']]
    checks['duplicate_paragraph31_distinct_ids']=labels.count('31')>=2 and len({u['id'] for u in conflict['units']})==len(conflict['units'])
    conflict_pages=rows(docs/'cerc-amount-conflict/extraction/pages.jsonl')
    checks['conflicting_money_literals_preserved']='4,68,02,06,946' in conflict_pages[1]['text'] and '4.68' in conflict_pages[34]['text']
    # Independently reopen every captured original and all output joins.
    for source in sources:verify_document(docs/source['id'])
    result={'schema_version':SCHEMA,'executed_at_utc':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':round(time.perf_counter()-started,3),'checks':checks,
        'passed':sum(checks.values()),'failed':sum(not v for v in checks.values()),
        'documents':report['documents'],'selected_examples':{'aptel_paragraph27':unit,'rop_paragraph2':rop_unit,'supplement_clause1':reg_unit},
        'scope':'Offline original-to-candidate pipeline; no verified legal record, client impact, database, remote ANN or generated answer.'}
    write_json(args.output/'execution-receipt.json',result)
    write_json(args.output/'real-output-example.json',{'source':next(s for s in sources if s['id']=='serentica-aptel-interim'),
        'source_validation':next(d for d in report['documents'] if d['id']=='serentica-aptel-interim'),
        'candidate_unit':unit,'exact_children':[c for c in aptel['children'] if c['parent_provision_version_id']==unit['id']],
        'next_stage_contract':{'legal_review':'required','candidate_not_verified':True,'later_case_status':'unknown','payment_compliance':'unknown','financial_impact':'not_computed'}})
    write_json(args.output/'real-regulation-output-example.json',{'source':next(s for s in sources if s['id']=='sharing-october-supplement'),
        'source_validation':next(d for d in report['documents'] if d['id']=='sharing-october-supplement'),
        'candidate_unit':reg_unit,'exact_children':[c for c in regulation['children'] if c['parent_provision_version_id']==reg_unit['id']],
        'raw_table_artifact':str(docs/'sharing-october-supplement/extraction/tables.json'),
        'next_stage_contract':{'legal_status':'draft_candidate_not_enacted_law','legal_review':'required','table_6_semantics':'requires_row_column_and_unit_review',
            'deadline_role_assignment':'requires_review_not_just_date_detection','client_eligibility':'not_assessed','money_savings':'not_computed'}})
    print(json.dumps({'checks':checks,'passed':result['passed'],'failed':result['failed'],'elapsed_seconds':result['elapsed_seconds'],'receipt':str(args.output/'execution-receipt.json')},indent=2))
    return 1 if result['failed'] else 0

if __name__=='__main__':raise SystemExit(main())
