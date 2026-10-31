"""Source-bound financial-year/table candidates. Never an applicability engine.

Rules nominate exact same-table row/column/caption associations; semantic review
and source quality retain their existing gates. No cell is filled or translated.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

CONTRACT = 'pdf2data.structured-table-observations.v1'
def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()
def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
def load_rules(path=None):
    path=Path(path) if path else Path(__file__).with_name('structured_table_observation_rules.json')
    rules=json.loads(path.read_text())
    if rules.get('schema_version')!='pdf2data.structured-table-observation-rules.v1':
        raise ValueError('Unknown structured observation rule schema')
    return rules,digest(path.read_bytes())

def financial_year_range(raw, rules=None, calendar_policy=None):
    rules=rules or load_rules()[0];rule=rules['financial_year_range']
    prefix='(?:'+'|'.join(re.escape(p) for p in rule['prefixes'])+')'
    separator='(?:'+'|'.join(re.escape(p) for p in rule['separators'])+')'
    year=r'([0-9]{4})\s*[-–]\s*([0-9]{2}|[0-9]{4})'
    match=re.fullmatch(r'\s*'+prefix+r'\s*'+year+r'\s+'+separator+r'\s+'+prefix+r'\s*'+year+r'\s*',raw,re.I)
    if not match:return None
    a,ae,b,be=match.groups();a,b=int(a),int(b)
    def valid_end(start,end):return int(end)==((start+1)%100 if len(end)==2 else start+1)
    if not valid_end(a,ae)or not valid_end(b,be)or not(rule['year_minimum']<=a<=b<=rule['year_maximum'])or b-a+1>rule['maximum_inclusive_years']:
        raise ValueError('Inconsistent or unsupported financial-year endpoints')
    result={'kind':'financialYearRange','raw_text':raw,'start_financial_year':f'{a}-{(a+1)%100:02d}','end_financial_year':f'{b}-{(b+1)%100:02d}','inclusive_financial_years':b-a+1,'calendar_interval':None,'semantic_state':'candidate_requires_review'}
    if calendar_policy is not None:
        policy=rules['calendar_policies'].get(calendar_policy)
        if not policy:raise ValueError('Explicit calendar policy is not configured')
        result['calendar_interval']={'start':date(a,policy['start_month'],policy['start_day']).isoformat(),'end_inclusive':date(b+policy['end_year_offset'],policy['end_month'],policy['end_day']).isoformat(),'policy_id':calendar_policy,'derivation':'explicit_policy_not_literal_source_dates'}
    return result

def page_spans(raw,page_text):
    """Exact selected-page segments; never fabricate a contiguous cross-column quote."""
    spans=[]
    for line in raw.splitlines():
        positions=[m.start() for m in re.finditer(re.escape(line),page_text)] if line else []
        if len(positions)!=1:return {'status':'not_uniquely_locatable','spans':[]}
        start=positions[0];spans.append({'char_start':start,'char_length':len(line),'exact_quote':line,'offset_encoding':'codepoint'})
    return {'status':'exact_unique_segments','spans':spans}

def evidence(raw,box,page_text,**extra):
    return {'raw_text':raw,'raw_text_sha256':digest(raw),'bbox':list(box),'bbox_coordinates':'PDF points, top-left','cell_text_span':{'char_start':0,'char_length':len(raw),'offset_encoding':'codepoint','exact_quote':raw},'selected_page_evidence':page_spans(raw,page_text),**extra}

def table_observations(table, page_text, lines, *, source_pdf_sha256, rules=None, calendar_policy=None):
    """Consume existing explicit cell slots and source text-line geometry."""
    rules=rules or load_rules()[0];assoc=rules['table_associations'];cells=table.get('cell_spans',[])
    if table.get('unresolved_grid_slots'):return []
    slots={}
    for cell in cells:
        slot=(cell['row'],cell['column'])
        if slot in slots:return []
        if assoc['require_single_slot_cells'] and (cell.get('row_span',1)!=1 or cell.get('column_span',1)!=1):return []
        slots[slot]=cell
    headers={col:cell for (row,col),cell in slots.items()if row==assoc['header_row']}
    captions=[];table_box=table['bbox']
    for line in lines:
        box=[line[k]for k in ('x0','top','x1','bottom')]
        if not(0<=table_box[1]-box[3]<=assoc['maximum_caption_gap_points'] and box[2]>table_box[0] and box[0]<table_box[2]):continue
        for unit in rules['units']:
            if any(re.fullmatch(pattern,line['text'],re.I)for pattern in unit['patterns']):captions.append((table_box[1]-box[3],line,unit))
    if not captions:return []
    minimum=min(item[0]for item in captions);nearest=[item for item in captions if abs(item[0]-minimum)<.1]
    if len(nearest)!=1:return []
    _,caption,unit=nearest[0];out=[]
    for (row,col),duration in sorted(slots.items()):
        if row<=assoc['header_row']or col!=assoc['range_column']:continue
        period=financial_year_range(duration['text'],rules,calendar_policy)
        if period is None:continue
        for (value_row,value_col),value in sorted(slots.items()):
            if value_row!=row or value_col==col or value_col not in headers:continue
            header=headers[value_col]
            if not header['text'].strip()or not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',value['text'].strip()):continue
            binding={'source_pdf_sha256':source_pdf_sha256,'page':table['page'],'page_text_sha256':digest(page_text),'table_id':table['id'],'table_version_id':table.get('table_version_id'),'table_geometry_sha256':table.get('table_geometry_sha256'),'row_zero_based':row,'value_column_zero_based':value_col}
            fields={name:evidence(cell['text'],cell['bbox'],page_text,cell_id=cell.get('id'))for name,cell in [('period',duration),('quantity',value),('subject_header',header)]}
            fields['unit_caption']=evidence(caption['text'],[caption[k]for k in('x0','top','x1','bottom')],page_text)
            record={'kind':'table_financial_year_quantity_candidate','source':binding,'period':period,'quantity':{'decimal_value':str(Decimal(value['text'].strip())),'unit_id':unit['id'],'currency':unit['currency'],'scale':unit['scale'],'denominator':unit['denominator'],'aggregation':'not_inferred','annual_distribution':'not_inferred'},'subject':{'raw_header':header['text'],'normalization':'none'},'associations':{'subject':'same_column_header_candidate','period':'same_row_range_cell_candidate','unit':'nearest_explicit_caption_above_same_table_candidate'},'evidence':fields,'source_quality_clearance':False,'semantic_review_required':True,'legal_applicability':None,'verified_client_fact':False,'publication_eligible':False,'deadline':None,'period_total':None,'arithmetic_performed':[]}
            record['observation_id']='tableobs:'+digest(canonical(record));out.append(record)
    return out

def extract_package(package,pages,*,calendar_policy=None,rules_path=None):
    """Read an immutable L1 package and independently reopen each selected PDF cell."""
    import pdfplumber
    package=Path(package);rules,rule_sha=load_rules(rules_path)
    manifest=json.loads((package/'l1-package.json').read_text());pdf_path=package/'original.pdf'
    with pdf_path.open('rb')as stream:pdf_sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if manifest['artifacts'].get('original.pdf')!=pdf_sha:raise ValueError('Original PDF hash mismatch')
    for name in ('layout.jsonl','source-receipt.json','quality.json'):
        if manifest['artifacts'].get(name)!=digest((package/name).read_bytes()):raise ValueError('L1 source artifact hash mismatch: '+name)
    quality={row['page']:row for row in json.loads((package/'quality.json').read_text()).get('results',[])}
    selected={row['page']:row for row in map(json.loads,(package/'extraction/pages.jsonl').read_text().splitlines())}
    layout={r['page']:r for r in map(json.loads,(package/'layout.jsonl').read_text().splitlines())}
    tables_path=package/'extraction/tables.json';tables=json.loads(tables_path.read_text());observations=[];holds=[]
    with pdfplumber.open(pdf_path)as pdf:
        for page in sorted(set(pages)):
            if type(page)is not int or not 1<=page<=len(pdf.pages):raise ValueError('Invalid physical page')
            native=pdf.pages[page-1];text=selected[page]['text']
            if selected[page].get('text_sha256')!=digest(text)or layout[page]['page_text_sha256']!=digest(text):raise ValueError('Selected page text differs from sealed layout hash')
            for table in tables:
                if table['page']!=page:continue
                records=table_observations(table,text,native.extract_text_lines(),source_pdf_sha256=pdf_sha,rules=rules,calendar_policy=calendar_policy)
                for record in records:
                    for key in ('period','quantity','subject_header'):
                        item=record['evidence'][key];fresh=native.crop(item['bbox']).extract_text()or''
                        if fresh!=item['raw_text']:raise ValueError('Fresh original PDF cell differs from stored raw cell')
                        item['fresh_native_read_sha256']=digest(fresh)
                    record['source']['l1_manifest_sha256']=digest((package/'l1-package.json').read_bytes());record['source']['table_candidate_artifact_sha256']=digest(tables_path.read_bytes());record['source']['table_grid_review']='not_run_fresh_cell_read_is_not_grid_approval';observations.append(record)
            holds.append({'page':page,'parser_page_status':selected[page].get('status'),'source_quality_outcome':quality.get(page,{}).get('outcome'),'source_quality_reassessed':False})
    return {'contract_version':CONTRACT,'rule_configuration_sha256':rule_sha,'implementation_sha256':digest(Path(__file__).read_bytes()),'source_pdf_sha256':pdf_sha,'package_path':str(package.resolve()),'observations':observations,'source_quality':holds,'legal_review':'not_run','publication_eligible':False,'remote_mutations':False}



def compare_parallel_candidates(left,right):
    """Arithmetic/range agreement is a comparison, never proof of translation."""
    keys=('start_financial_year','end_financial_year','inclusive_financial_years')
    matches=all(left['period'][key]==right['period'][key]for key in keys)and left['quantity']==right['quantity']
    return {'left_observation_id':left['observation_id'],'right_observation_id':right['observation_id'],'same_pdf':left['source']['source_pdf_sha256']==right['source']['source_pdf_sha256'],'range_quantity_unit_agree':matches,'subject_translation_verified':False,'legal_equivalence_verified':False,'publication_eligible':False}

def verify_observations(receipt,package,*,rules_path=None):
    rules,rule_sha=load_rules(rules_path)
    if receipt.get('contract_version')!=CONTRACT or receipt.get('rule_configuration_sha256')!=rule_sha:raise ValueError('Observation contract or rule profile changed')
    if receipt.get('implementation_sha256')!=digest(Path(__file__).read_bytes()):raise ValueError('Observation implementation profile changed')
    policies={row['period']['calendar_interval']['policy_id'] for row in receipt['observations']if row['period']['calendar_interval']is not None}
    if len(policies)>1:raise ValueError('Mixed calendar policy receipt')
    pages=[row['page']for row in receipt['source_quality']]
    fresh=extract_package(package,pages,calendar_policy=next(iter(policies),None),rules_path=rules_path)
    if canonical(fresh)!=canonical(receipt):raise ValueError('Fresh source observation differs from submitted receipt')
    return {'status':'verified_source_candidates','observations':len(fresh['observations']),'source_quality_cleared':False,'legal_review':'not_run','publication_eligible':False}

def write_observations(package,pages,output,*,calendar_policy=None,rules_path=None):
    output=Path(output)
    if output.exists()or output.resolve().is_relative_to(Path(package).resolve()):raise ValueError('Observation output must be new and outside source L1 package')
    result=extract_package(package,pages,calendar_policy=calendar_policy,rules_path=rules_path)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return {'contract_version':CONTRACT,'observations':len(result['observations']),'output':str(output),'publication_eligible':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--package',required=True,type=Path);parser.add_argument('--pages',required=True,nargs='+',type=int);parser.add_argument('--calendar-policy');parser.add_argument('--rules',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();print(json.dumps(write_observations(args.package,args.pages,args.output,calendar_policy=args.calendar_policy,rules_path=args.rules)))
if __name__=='__main__':main()
