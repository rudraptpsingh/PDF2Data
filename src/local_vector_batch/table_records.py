"""Configured hierarchical row groups, with source cells and uncertain continuations.

No native blank-cell rewrite, semantic ownership, legal entity or financial approval.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from .cell_fields import decode, digest, source_data, cells, SOURCE_FILES
from .field_rules import write
from .hashing import file_sha256
from .document_continuations import _columns
from .table_headers import first_row_header_candidate

SCHEMA = 'pdf2data.table-records.v1'
RULES = 'pdf2data.table-record-rules.v1'
CODE_FILES = ('table_records.py','cell_fields.py','field_rules.py','document_continuations.py','table_headers.py')


def require(value, message):
    if not value: raise ValueError(message)


def implementation():
    return {n:file_sha256(Path(__file__).with_name(n)) for n in CODE_FILES}


def validate_rules(rules):
    require(isinstance(rules,dict) and rules.get('schema_version')==RULES,'Table record rules v1 required')
    for k in ('id','version'):
        require(isinstance(rules.get(k),str) and 0<len(rules[k])<=128,'Bounded rule ID/version required')
    ids=rules.get('table_ids');require(isinstance(ids,list) and 0<len(ids)<=100 and all(isinstance(v,str)and v for v in ids)and len(set(ids))==len(ids),'Distinct table_ids required')
    roles=rules.get('roles');require(isinstance(roles,dict) and 0<len(roles)<=64,'Role-to-column map required')
    def mapping(m):
        require(isinstance(m,dict) and set(m)==set(roles) and all(isinstance(k,str)and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',k)and type(v)is int and 0<=v<=1000 for k,v in m.items()),'Invalid role column mapping')
    mapping(roles)
    maps=rules.get('column_maps',{});require(isinstance(maps,dict) and set(maps)<=set(ids),'Unknown table column map')
    for m in maps.values():mapping(m)
    headers=rules.get('header_rows',{});require(isinstance(headers,dict) and set(headers)<=set(ids),'Invalid table header declarations')
    for rows in headers.values():require(isinstance(rows,list) and all(type(v)is int and v>=0 for v in rows) and len(set(rows))==len(rows),'Invalid header rows')
    levels=rules.get('levels');require(isinstance(levels,list) and 0<len(levels)<=8,'Hierarchy levels required')
    names=[]
    for level in levels:
        require(isinstance(level,dict) and isinstance(level.get('id'),str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',level['id']),'Invalid group level')
        markers=level.get('marker_roles');require(isinstance(markers,list) and markers and all(isinstance(r,str)and r in roles for r in markers),'Unknown group marker role')
        require(level.get('match','any')in('any','all'),'Unsupported group marker mode');names.append(level['id'])
    require(len(set(names))==len(names),'Duplicate group level')
    period=rules.get('period_roles')
    if period is not None:require(isinstance(period,dict) and set(period)=={'start','end','days','charge'} and all(isinstance(r,str)and r in roles for r in period.values()),'Four period roles required')
    summary=rules.get('summary_roles',[]);require(isinstance(summary,list) and all(isinstance(r,str)and r in roles for r in summary),'Unknown summary role')
    if summary:require(rules.get('summary_level')in names,'Summary group level required')
    statuses=rules.get('status_literals',[]);require(isinstance(statuses,list) and len(statuses)<=32 and all(isinstance(s,str)and 0<len(s)<=128 for s in statuses),'Invalid status literals')
    for k,default,maximum in [('max_rows',10000,100000),('max_cells',100000,1000000)]:
        v=rules.get(k,default);require(type(v)is int and 1<=v<=maximum,'Invalid '+k)
    return rules


def load_rules(path):
    return validate_rules(decode(Path(path).read_bytes()))


def _key(value):
    return ' '.join(str(value).split()).casefold() if value is not None else ''


def _present(value):
    return value is not None and value!=''


def _date(value):
    if not isinstance(value,str):return None
    for fmt in ('%d-%m-%Y','%d.%m.%Y'):
        try:return datetime.strptime(value,fmt).date().isoformat()
        except ValueError:pass
    return None


def _number(value, integer=False):
    text=str(value) if not isinstance(value,bool) else ''
    return text if re.fullmatch(r'[0-9]+' if integer else r'[0-9]+(?:\.[0-9]+)?',text) else None


def compute(data,rules,rules_sha):
    validate_rules(rules)
    for h in (rules_sha,data.get('source_pdf_sha256'),data.get('source_package_sha256')):require(isinstance(h,str)and re.fullmatch(r'[a-f0-9]{64}',h),'Source/rules SHA256 required')
    pages={p['page']:p for p in data['pages']}
    for p in pages.values():require(sha256(p['text'].encode()).hexdigest()==p['text_sha256'],'Page text differs')
    tables={};table_index={}
    for i,t in enumerate(data['tables']):
        require(t['id']not in tables,'Duplicate native table ID');tables[t['id']]=t;table_index[t['id']]=i
    require(set(rules['table_ids'])<=set(tables),'Configured table absent from supplied source')
    selected=sorted((tables[t]for t in rules['table_ids']),key=lambda t:(t['page'],(t.get('bbox')or[0,0,0,0])[1],t['id']))
    source_cells=[];by_table={};header_signatures=[];header_ids={}
    quality={q['page']:q['outcome']for q in data.get('quality',{}).get('results',[])}
    roles_for=lambda t:rules.get('column_maps',{}).get(t['id'],rules['roles'])
    def signature(t,row):
        grid=t.get('cells',[])
        return {r:_key(grid[row][c])if row<len(grid)and c<len(grid[row])else'' for r,c in roles_for(t).items()}
    for t in selected:
        require(t['page']in pages,'Table page absent')
        lookup={};by_table[t['id']]=lookup;header_ids[t['id']]=set(rules.get('header_rows',{}).get(t['id'],[]))
        for c in cells(t,table_index[t['id']]):
            require(type(c['row'])is int and c['row']>=0 and type(c['column'])is int and c['column']>=0,'Invalid native cell coordinates')
            raw=c['raw'];text=raw if isinstance(raw,str)else None
            locator={k:deepcopy(v)for k,v in c.items()if k!='raw'}
            locator.update(physical_page=t['page'],table_id=t['id'],table_version_id=t.get('table_version_id'),
                           page_text_sha256=pages[t['page']]['text_sha256'],table_geometry_sha256=t.get('table_geometry_sha256'),
                           table_status=t.get('status'),bbox_coordinates=t.get('bbox_coordinates'))
            cell=dict(id='table-record-cell-'+digest([data['source_pdf_sha256'],data['source_package_sha256'],locator])[:24],
                      locator=locator,raw_value=deepcopy(raw),text_sha256=sha256(text.encode()).hexdigest()if text is not None else None,
                      cell_text_span=dict(char_start=0,char_length=len(text),exact_quote=text,offset_encoding='unicode_codepoint')if text else None,
                      source_quality=quality.get(t['page'],'unknown'),selected_page_offset_assigned=False)
            source_cells.append(cell);lookup.setdefault((c['row'],c['column']),[]).append(cell)
        require(len(source_cells)<=rules.get('max_cells',100000),'Table cells exceed explicit budget')
        for row in header_ids[t['id']]:
            require(row<len(t.get('cells',[])),'Configured header row absent')
            if t is selected[0]:header_signatures.append(signature(t,row))
    for t in selected:
        for row in range(len(t.get('cells',[]))):
            if signature(t,row)in header_signatures:header_ids[t['id']].add(row)
    continuations=[]
    def open_bridge(a,b):
        if not a.get('cells')or not b.get('cells'):return []
        x=a['cells'][-1];y=b['cells'][0];matches=[]
        for role,column in roles_for(a).items():
            other=roles_for(b)[role]
            if column>=len(x)or other>=len(y):continue
            before=str(x[column]or'');after=str(y[other]or'')
            if before.count('(')>before.count(')')and ')'in after and re.match(r'^\s*(?:[0-9]|\))',after):
                matches.append(dict(role=role,from_cell_refs=[c['id']for c in by_table[a['id']].get((len(a['cells'])-1,column),[])],
                                    to_cell_refs=[c['id']for c in by_table[b['id']].get((0,other),[])]))
        return matches
    for a in selected:
        for b in selected:
            if b['page']!=a['page']+1:continue
            pa=pages[a['page']];pb=pages[b['page']];ab=a.get('bbox');bb=b.get('bbox')
            ac=_columns(a,pa.get('pdf_width'));bc=_columns(b,pb.get('pdf_width'));reason=None
            geometry=bool(ab and bb and pa.get('pdf_height')and pb.get('pdf_height')and ac and bc)
            if not geometry:reason='source_geometry_unavailable'
            elif ab[3]/pa['pdf_height']<.7 or bb[1]/pb['pdf_height']>.25:reason='not_at_adjacent_page_boundary'
            elif len(ac)!=len(bc)or max(abs(x-y)for x,y in zip(ac,bc))>.012:reason='column_geometry_differs'
            elif max(map(len,a.get('cells',[])),default=0)!=max(map(len,b.get('cells',[])),default=0):reason='column_count_differs'
            repeated_rows=[r for r in header_ids[b['id']]if signature(b,r)in header_signatures]
            repeated=bool(repeated_rows);bridge=open_bridge(a,b)
            if reason is None and not header_signatures:reason='header_origin_not_established'
            if reason is None and any(signature(b,r)not in header_signatures for r in header_ids[b['id']]):reason='new_or_conflicting_header'
            dense_first_row=bool(b.get('cells'))and sum(bool(_key(v))for v in b['cells'][0])*2>=len(rules['roles'])
            if reason is None and dense_first_row and first_row_header_candidate(b.get('cells',[]))is not None and not repeated and not bridge:reason='new_or_conflicting_header'
            if reason is None and not repeated and not bridge:reason='no_repeated_header_or_open_cell_bridge'
            record=dict(from_table_id=a['id'],to_table_id=b['id'],from_page=a['page'],to_page=b['page'],
                        repeated_header_rows=sorted(repeated_rows),open_cell_bridges=bridge,
                        state='continuation_candidate_requires_review'if reason is None else'unresolved_continuation',reason=reason,
                        intervening_body_content_checked=False,semantic_ownership_approved=False)
            record['id']='table-record-continuation-'+digest([data['source_pdf_sha256'],data['source_package_sha256'],rules_sha,record])[:24]
            continuations.append(record)
    usable=[c for c in continuations if c['state']=='continuation_candidate_requires_review']
    for c in usable:
        if sum(x['to_table_id']==c['to_table_id']for x in usable)>1 or sum(x['from_table_id']==c['from_table_id']for x in usable)>1:
            c['state']='ambiguous_continuation';c['reason']='competing_table_fragments'
    for c in continuations:
        c.pop('id');c['id']='table-record-continuation-'+digest([data['source_pdf_sha256'],data['source_package_sha256'],rules_sha,c])[:24]
    incoming={c['to_table_id']:c for c in continuations if c['state']=='continuation_candidate_requires_review'}
    groups=[];memberships=[];rows=[];summaries=[];gaps=[];end_context={};group_index={};cell_index={c['id']:c for c in source_cells}
    def value(refs):
        return cell_index[refs[0]]['raw_value']if len(refs)==1 else None
    for t in selected:
        cont=incoming.get(t['id']);active=deepcopy(end_context.get(cont['from_table_id'],{}))if cont else{}
        lookup=by_table[t['id']]
        for ordinal,grid in enumerate(t.get('cells',[])):
            require(len(rows)<rules.get('max_rows',10000),'Table rows exceed explicit budget')
            role_cells={r:[c['id']for c in lookup.get((ordinal,column),[])]for r,column in roles_for(t).items()}
            rid='table-record-row-'+digest([data['source_pdf_sha256'],data['source_package_sha256'],rules_sha,t['id'],t.get('table_version_id'),ordinal])[:24]
            incompatible=max(roles_for(t).values())>=max(map(len,t.get('cells',[])),default=0)
            kind='profile_column_scope_unavailable'if incompatible else'header'if ordinal in header_ids[t['id']]else'blank'if not any(_present(v)for v in grid)else'data'
            row=dict(id=rid,physical_page=t['page'],table_id=t['id'],table_version_id=t.get('table_version_id'),row=ordinal,
                     role_cell_refs=role_cells,kind=kind,group_context_candidates={},membership_ids=[],complete_period=False,unresolved_group_levels=[])
            if kind not in ('header','blank','profile_column_scope_unavailable'):
                for depth,level in enumerate(rules['levels']):
                    evidence=[ref for role in level['marker_roles']for ref in role_cells[role]if _present(cell_index[ref]['raw_value'])]
                    present=[_present(value(role_cells[role]))for role in level['marker_roles']]
                    trigger=all(present)if level.get('match','any')=='all'else any(present)
                    if level.get('match','any')=='all'and any(present)and not all(present):
                        for later in rules['levels'][depth:]:active.pop(later['id'],None)
                        gaps.append(dict(kind='partial_group_marker',row_id=rid,level=level['id'],marker_cell_refs=evidence))
                    if not trigger:continue
                    for later in rules['levels'][depth:]:active.pop(later['id'],None)
                    parent=active.get(rules['levels'][depth-1]['id'])if depth else None
                    gid='table-record-group-'+digest([rid,level['id'],evidence])[:24]
                    group=dict(id=gid,level=level['id'],parent_group_candidate_id=parent,marker_row_id=rid,marker_table_id=t['id'],marker_cell_refs=evidence,
                               required_parent_level=rules['levels'][depth-1]['id']if depth else None,parent_context_resolved_as_candidate=bool(parent)if depth else True,
                               state='configured_group_candidate_requires_review',semantic_ownership_approved=False)
                    groups.append(group);group_index[gid]=group;active[level['id']]=gid
                row['group_context_candidates']=deepcopy(active)
                row['unresolved_group_levels']=[level['id']for level in rules['levels']if level['id']not in active]
                if row['unresolved_group_levels']:gaps.append(dict(kind='group_context_unresolved',row_id=rid,levels=row['unresolved_group_levels']))
                for level,gid in active.items():
                    group=group_index[gid];marker_table=group['marker_table_id']
                    basis='direct_marker_row'if group['marker_row_id']==rid else'cross_page_continuation_candidate'if marker_table!=t['id']else'configured_sparse_block_candidate'
                    if basis=='configured_sparse_block_candidate'and any(cell_index[ref]['locator']['row_span']>1 and cell_index[ref]['locator']['row']<=ordinal<cell_index[ref]['locator']['row']+cell_index[ref]['locator']['row_span']for ref in group['marker_cell_refs']):
                        basis='source_merged_cell_candidate'
                    chain=[];cursor=t['id']
                    while cursor!=marker_table and cursor in incoming:
                        step=incoming[cursor];chain.append(step['id']);cursor=step['from_table_id']
                    membership=dict(id='table-record-membership-'+digest([rid,gid,basis])[:24],row_id=rid,group_candidate_id=gid,level=level,basis=basis,
                                    continuation_candidate_id=cont['id']if marker_table!=t['id']and cont else None,
                                    continuation_candidate_chain=list(reversed(chain)),
                                    semantic_ownership_approved=False,raw_cells_filled=False)
                    memberships.append(membership);row['membership_ids'].append(membership['id'])
                period=rules.get('period_roles');vals={r:value(role_cells[role])for r,role in(period or{}).items()}
                statuses=[ref for refs in role_cells.values()for ref in refs if _key(cell_index[ref]['raw_value'])in{_key(s)for s in rules.get('status_literals',[])}]
                if statuses:row.update(kind='status_event',status_cell_refs=sorted(set(statuses)))
                elif period and any(_present(v)for v in vals.values()):
                    parsed=dict(start=_date(vals['start']),end=_date(vals['end']),days=_number(vals['days'],True),charge=_number(vals['charge']))
                    complete=all(v is not None for v in parsed.values())
                    row.update(kind='charge_period'if complete else'incomplete_period_fragment',complete_period=complete,typed_period_candidates=parsed)
                    if not complete:gaps.append(dict(kind='incomplete_period_fields',row_id=rid,missing_or_invalid=[k for k,v in parsed.items()if v is None]))
                    if parsed['start']is not None and parsed['end']is not None and parsed['start']>parsed['end']:
                        gaps.append(dict(kind='reversed_period_date_candidates',row_id=rid))
                summary_cells={role:role_cells[role]for role in rules.get('summary_roles',[])if _present(value(role_cells[role]))}
                if summary_cells and row['kind']!='status_event':summaries.append(dict(id='table-record-summary-'+digest([rid,summary_cells])[:24],row_id=rid,
                    owner_group_candidate_id=active.get(rules['summary_level']),role_cell_refs=summary_cells,semantic_ownership_approved=False))
            rows.append(row)
            if row['kind']=='profile_column_scope_unavailable':gaps.append(dict(kind='profile_column_scope_unavailable',row_id=rid,table_id=t['id']))
        end_context[t['id']]=deepcopy(active)
    for c in continuations:
        if c['state']!='continuation_candidate_requires_review':gaps.append(dict(kind='unresolved_continuation',continuation_candidate_id=c['id'],reason=c['reason']))
    return dict(schema_version=SCHEMA,source_pdf_sha256=data['source_pdf_sha256'],source_package_sha256=data['source_package_sha256'],rules_sha256=rules_sha,
                rule_set_id=rules['id'],rule_version=rules['version'],rule_catalog=deepcopy(rules),producer_implementation=implementation(),
                source_cells=source_cells,rows=rows,groups=groups,memberships=memberships,summary_observations=summaries,continuation_candidates=continuations,gaps=gaps,
                counts=dict(tables=len(selected),source_cells=len(source_cells),rows=len(rows),groups=len(groups),
                            charge_periods=sum(r['kind']=='charge_period'for r in rows),status_events=sum(r['kind']=='status_event'for r in rows),
                            incomplete_periods=sum(r['kind']=='incomplete_period_fragment'for r in rows),gap_records=len(gaps),
                            unresolved_context_rows=sum(bool(r['unresolved_group_levels'])for r in rows),unreviewed_memberships=len(memberships)),
                semantic_ownership_approved=False,entity_identity_approved=False,source_quality_approval_granted=False,publication_eligible=False,calculation_allowed=False,
                limitations=['Configured roles and sparse blocks are source-backed candidates, not approved semantic ownership.',
                             'Continuation candidates require review; intervening body content is not independently checked by this protocol.',
                             'Native blank/null cells remain unchanged. Header units, totals, legal entities and current client facts are not inherited.'])


def _run(package,rules_path,timeout):
    from .legal_l1 import verify_document
    require(type(timeout)is int and 1<=timeout<=600,'Use timeout 1..600 seconds')
    verify_document(package);load_rules(rules_path)
    before={n:file_sha256(package/n)for n in SOURCE_FILES};rules_sha=file_sha256(rules_path);code=implementation()
    with tempfile.TemporaryDirectory(prefix='pdf2data-table-records-')as tmp:
        request=Path(tmp)/'request.json';output=Path(tmp)/'output.json'
        write(request,dict(package=str(package),rules=str(rules_path),rules_sha256=rules_sha))
        try:worker=subprocess.run([sys.executable,'-m','local_vector_batch.table_records','--worker',str(request),str(output)],env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])},capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired:raise ValueError('Table record evaluation exceeded time budget; no partial output')from None
        require(worker.returncode==0 and output.exists(),'Table record worker failed: '+worker.stderr[-1000:])
        require(output.stat().st_size<=128*1024*1024,'Table records exceed128MB; narrow rules')
        result=decode(output.read_bytes())
    require(file_sha256(rules_path)==rules_sha and before=={n:file_sha256(package/n)for n in SOURCE_FILES}and code==implementation(),'Table record source/rules/implementation changed')
    return result,before,code


def extract(package,rules_path,output,timeout=30):
    package=Path(package).resolve();rules_path=Path(rules_path).resolve();output=Path(output).resolve()
    require(not output.exists()and not output.is_relative_to(package)and not package.is_relative_to(output),'Use a new output outside immutable source package')
    result,source_files,code=_run(package,rules_path,timeout)
    output.mkdir(parents=True);(output/'rules.json').write_bytes(rules_path.read_bytes());write(output/'table-records.json',result)
    write(output/'manifest.json',dict(schema_version='pdf2data.table-record-bundle.v1',source_package=str(package),source_files=source_files,
        implementation_files_sha256=code,files={n:file_sha256(output/n)for n in('rules.json','table-records.json')},publication_eligible=False))
    return dict(schema_version=SCHEMA,output=str(output),counts=result['counts'],source_quality_changed=False,publication_eligible=False)


def verify(bundle,timeout=30):
    bundle=Path(bundle).resolve();manifest=decode((bundle/'manifest.json').read_bytes())
    require(manifest.get('schema_version')=='pdf2data.table-record-bundle.v1','Unsupported table record bundle')
    require(manifest.get('publication_eligible')is False,'Table record bundle cannot grant publication approval')
    require(set(manifest.get('files',{}))=={'rules.json','table-records.json'}and set(manifest.get('source_files',{}))==set(SOURCE_FILES),'Incomplete table record manifest')
    require(manifest.get('implementation_files_sha256')==implementation(),'Table record implementation differs')
    require(all(file_sha256(bundle/n)==h for n,h in manifest['files'].items()),'Table record artifact changed')
    package=Path(manifest['source_package']);require(all(file_sha256(package/n)==h for n,h in manifest['source_files'].items()),'Table record source changed')
    expected,hashes,code=_run(package,bundle/'rules.json',timeout);actual=decode((bundle/'table-records.json').read_bytes())
    require(digest(expected)==digest(actual)and hashes==manifest['source_files']and code==manifest['implementation_files_sha256'],'Table record output differs from source re-derivation')
    return dict(schema_version='pdf2data.table-record-verification.v1',valid=True,counts=actual['counts'],publication_eligible=False)


def configure(parser):
    sub=parser.add_subparsers(dest='table_record_operation',required=True)
    p=sub.add_parser('extract');p.add_argument('--package',type=Path,required=True);p.add_argument('--rules',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--bundle',type=Path,required=True)
    for child in(p,v):child.add_argument('--timeout',type=int,default=30);child.add_argument('--json',action='store_true')


def execute(args):
    return verify(args.bundle,args.timeout)if args.table_record_operation=='verify'else extract(args.package,args.rules,args.output,args.timeout)


if __name__=='__main__':
    require(len(sys.argv)==4 and sys.argv[1]=='--worker','Use pdf2data table-records')
    request=decode(Path(sys.argv[2]).read_bytes());rules=Path(request['rules'])
    require(file_sha256(rules)==request['rules_sha256'],'Rules changed')
    write(Path(sys.argv[3]),compute(source_data(Path(request['package'])),load_rules(rules),request['rules_sha256']))
