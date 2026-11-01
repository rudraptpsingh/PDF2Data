from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import pytest
from local_vector_batch import table_records as m

HEADER=['Company','Application','MW','CoD','Start date','End date','Days','Charge Rs']
ROLES=dict(zip(['company','application','capacity','cod','start','end','days','charge'],range(8)))
FIRST=['Company A','123456789 & 987654321','200','*02-03-2026','01-07-2025','30-09-2025','92','37500000']
NEXT=['','','','','01-10-2025','31-10-2025','31','12914600']


def table(tid,page,rows,box=None):
    box=box or[0,700 if page==1 else 20,200,780 if page==1 else 160]
    width=(box[2]-box[0])/max(map(len,rows));height=(box[3]-box[1])/len(rows)
    cells=[dict(id=f'{tid}:r{r}:c{c}',row=r,column=c,row_span=1,column_span=1,text=value,
                bbox=[c*width,box[1]+r*height,(c+1)*width,box[1]+(r+1)*height])for r,row in enumerate(rows)for c,value in enumerate(row)]
    return dict(id=tid,page=page,table_version_id=tid+':v1',bbox=box,cells=deepcopy(rows),cell_spans=cells,status='needs_review')


def data(tables):
    pages=[]
    for n in sorted({t['page']for t in tables}):
        text='native page '+str(n);pages.append(dict(page=n,text=text,text_sha256=sha256(text.encode()).hexdigest(),pdf_width=200,pdf_height=800))
    return dict(source_pdf_sha256='a'*64,source_package_sha256='b'*64,declared_family='financial_evidence',pages=pages,tables=tables,quality={})


def rule(ids):
    return dict(schema_version=m.RULES,id='test.hierarchy',version='1',table_ids=ids,header_rows={ids[0]:[0]},roles=ROLES,
                levels=[dict(id='company',marker_roles=['company']),dict(id='application',marker_roles=['application']),dict(id='tranche',marker_roles=['capacity','cod'],match='all')],
                period_roles={k:k for k in ('start','end','days','charge')},status_literals=['To be revoked'])


def compute(d,r=None):
    return m.compute(d,r or rule([t['id']for t in d['tables']]),'c'*64)


def test_companies_multiple_ids_partial_tranches_and_status_do_not_duplicate_periods():
    b=['Company B','555555555','115.5','24-08-2025','11-08-2024','10-11-2024','92','29200000']
    remaining=['','','1.5','14-12-2025','To be revoked','','','']
    d=data([table('t1',1,[HEADER,FIRST,NEXT,['']*8,b,remaining])]);before=deepcopy(d);v=compute(d)
    assert d==before
    assert v['counts']['charge_periods']==3 and v['counts']['status_events']==1
    assert [sum(g['level']==level for g in v['groups'])for level in ('company','application','tranche')]==[2,2,3]
    periods=[r for r in v['rows']if r['kind']=='charge_period']
    assert periods[0]['group_context_candidates']==periods[1]['group_context_candidates']
    assert periods[1]['group_context_candidates']['company']!=periods[2]['group_context_candidates']['company']
    event=next(r for r in v['rows']if r['kind']=='status_event')
    assert event['group_context_candidates']['application']==periods[-1]['group_context_candidates']['application']
    assert event['group_context_candidates']['tranche']!=periods[-1]['group_context_candidates']['tranche']
    assert not event['complete_period'] and not v['calculation_allowed']and not v['entity_identity_approved']


def test_repeated_header_cross_page_periods_keep_full_source_chain():
    d=data([table('t1',1,[HEADER,FIRST]),table('t2',2,[HEADER,NEXT],[0,20,200,780]),table('t3',3,[HEADER,NEXT])])
    v=compute(d);assert v['counts']['charge_periods']==3 and len(v['groups'])==3
    assert all(c['state']=='continuation_candidate_requires_review'for c in v['continuation_candidates'])
    row=next(r for r in v['rows']if r['physical_page']==3 and r['kind']=='charge_period')
    links=[l for l in v['memberships']if l['row_id']==row['id']]
    assert all(l['basis']=='cross_page_continuation_candidate'and len(l['continuation_candidate_chain'])==2 for l in links)
    assert all(not l['raw_cells_filled']and not l['semantic_ownership_approved']for l in links)
    assert d['tables'][2]['cells'][1][0]==''
    assert not v['continuation_candidates'][0]['intervening_body_content_checked']


def test_new_company_and_new_partial_tranche_reset_only_their_own_scope():
    partial=['','','11.8','26-11-2025','12-10-2025','31-10-2025','20','565975.2']
    v=compute(data([table('t1',1,[HEADER,FIRST]),table('t2',2,[HEADER,partial])]))
    periods=[r for r in v['rows']if r['kind']=='charge_period'];a,b=[r['group_context_candidates']for r in periods]
    assert a['company']==b['company']and a['application']==b['application']and a['tranche']!=b['tranche']
    other=deepcopy(FIRST);other[:2]=['Other company','888888888']
    v=compute(data([table('t1',1,[HEADER,FIRST]),table('t2',2,[HEADER,other])]))
    periods=[r for r in v['rows']if r['kind']=='charge_period']
    assert all(periods[0]['group_context_candidates'][k]!=periods[1]['group_context_candidates'][k]for k in ('company','application','tranche'))


def test_missing_page_competing_fragments_and_changed_units_do_not_carry_context():
    cases=[data([table('t1',1,[HEADER,FIRST]),table('t3',3,[HEADER,NEXT])]),
           data([table('t1',1,[HEADER,FIRST]),table('t2',2,[HEADER,NEXT]),table('other',2,[HEADER,NEXT])])]
    changed=HEADER[:-1]+['Charge kWh'];d=data([table('t1',1,[HEADER,FIRST]),table('t2',2,[changed,NEXT])]);r=rule(['t1','t2']);r['header_rows']['t2']=[0]
    cases.append(d)
    for i,d in enumerate(cases):
        v=compute(d,r if i==2 else None)
        later=[row for row in v['rows']if row['physical_page']>1 and row['kind']=='charge_period']
        assert later and all(row['group_context_candidates']=={}for row in later)
        if i==1:assert all(c['state']=='ambiguous_continuation'for c in v['continuation_candidates'])
        if i==2:assert v['continuation_candidates'][0]['reason']=='new_or_conflicting_header'


def test_explicit_column_map_handles_reordered_header_without_rewriting_cells():
    header=deepcopy(HEADER);header[0],header[1]=header[1],header[0]
    nextrow=deepcopy(NEXT);d=data([table('t1',1,[HEADER,FIRST]),table('t2',2,[header,nextrow])]);r=rule(['t1','t2'])
    mapping=deepcopy(ROLES);mapping['company'],mapping['application']=1,0;r['column_maps']={'t2':mapping}
    v=compute(d,r);assert v['continuation_candidates'][0]['state']=='continuation_candidate_requires_review'
    assert len(v['groups'])==3 and v['counts']['charge_periods']==2


def test_open_cell_bridge_keeps_fragments_and_rejects_unrelated_column_shape():
    rows=[['Petition','Capacity','Conditional amount'],['276/MP/2025','200','3.75 (if no capacity as on']]
    a=table('t1',1,rows);b=table('t2',2,[['','OR150 MW','30.6.2025) OR 2.815 (if50 MW)']])
    c=table('different',2,[['Period','Rate'],['Jan','2292']])
    r=dict(schema_version=m.RULES,id='split',version='1',table_ids=['t1','t2','different'],header_rows={'t1':[0]},
           roles={'petition':0,'capacity':1,'conditional':2},levels=[dict(id='petition',marker_roles=['petition'])])
    v=compute(data([a,b,c]),r)
    match=next(c for c in v['continuation_candidates']if c['to_table_id']=='t2');assert match['state']=='continuation_candidate_requires_review'
    assert match['open_cell_bridges'][0]['role']=='conditional'
    row=next(x for x in v['rows']if x['table_id']=='t2');assert row['group_context_candidates']
    assert next(x for x in v['continuation_candidates']if x['to_table_id']=='different')['state']=='unresolved_continuation'
    assert all(x['kind']=='profile_column_scope_unavailable'for x in v['rows']if x['table_id']=='different')


def test_split_period_is_not_joined_into_an_invented_complete_row():
    left=deepcopy(FIRST);left[5:] = ['', '', ''];right=['','','','','','30-09-2025','92','37500000']
    v=compute(data([table('t1',1,[HEADER,left]),table('t2',2,[HEADER,right])]))
    assert v['counts']['charge_periods']==0 and v['counts']['incomplete_periods']==2
    assert all(not row['complete_period']for row in v['rows'])


def test_partial_new_tranche_marker_does_not_keep_previous_capacity_owner():
    partial=deepcopy(NEXT);partial[3]='26-11-2025'
    v=compute(data([table('t1',1,[HEADER,FIRST,partial,NEXT])]))
    later=[r for r in v['rows']if r['row']>=2]
    assert all('tranche'not in r['group_context_candidates']for r in later)
    assert any(g['kind']=='partial_group_marker'for g in v['gaps'])
    assert all('tranche'in r['unresolved_group_levels']for r in later)


def test_merged_cell_candidate_is_distinct_from_sparse_membership_and_null_zero_false_survive():
    d=data([table('t1',1,[HEADER,FIRST,NEXT])]);t=d['tables'][0]
    company=next(c for c in t['cell_spans']if c['row']==1 and c['column']==0);company['row_span']=2
    t['cell_spans']=[c for c in t['cell_spans']if not(c['row']==2 and c['column']==0)];t['cells'][2][0]=None
    v=compute(d);row=next(r for r in v['rows']if r['row']==2)
    membership=next(x for x in v['memberships']if x['row_id']==row['id']and x['level']=='company')
    assert membership['basis']=='source_merged_cell_candidate'and row['role_cell_refs']['company']==[]
    d=data([table('t1',1,[HEADER,FIRST,[None,False,0,'','','','',0]])]);v=compute(d)
    values=[c['raw_value']for c in v['source_cells']]
    assert any(x is None for x in values)and any(x is False for x in values)and any(type(x)is int and x==0 for x in values)


def test_summary_is_retained_once_and_not_repeated_per_period():
    roles={**ROLES,'gross':8,'credit':9,'balance':10};rows=[HEADER+['Gross','Credit','Balance'],FIRST+['100','75','25'],NEXT+['','','']]
    r=rule(['t1']);r.update(roles=roles,summary_roles=['gross','credit','balance'],summary_level='application')
    v=compute(data([table('t1',1,rows)]),r)
    assert len(v['summary_observations'])==1 and v['counts']['charge_periods']==2
    assert set(v['summary_observations'][0]['role_cell_refs'])=={'gross','credit','balance'}


@pytest.mark.parametrize('change',['duplicate','unknown','bad_role','bad_period','budget','missing'])
def test_invalid_rules_and_bounds_fail_without_partial_output(change):
    d=data([table('t1',1,[HEADER,FIRST,NEXT])]);r=rule(['t1'])
    if change=='duplicate':r['table_ids']=['t1','t1']
    elif change=='unknown':r['levels'][0]['marker_roles']=['unknown']
    elif change=='bad_role':r['roles']={**ROLES,'bad':True}
    elif change=='bad_period':r['period_roles']['start']=[]
    elif change=='budget':r['max_rows']=1
    else:r['table_ids']=['absent']
    with pytest.raises(ValueError):compute(d,r)


def test_rule_and_source_changes_rekey_ids_without_changing_raw_cells():
    d=data([table('t1',1,[HEADER,FIRST,NEXT])]);r=rule(['t1']);a=compute(d,r)
    b=m.compute(d,r,'d'*64);assert a['rows'][1]['id']!=b['rows'][1]['id']
    assert [c['raw_value']for c in a['source_cells']]==[c['raw_value']for c in b['source_cells']]
    assert a==compute(deepcopy(d),deepcopy(r))


def test_formal_schemas_and_reversed_date_gap():
    jsonschema=pytest.importorskip('jsonschema');root=Path(__file__).parents[1]
    d=data([table('t1',1,[HEADER,FIRST])]);r=rule(['t1']);v=compute(d,r)
    jsonschema.Draft202012Validator(json.loads((root/'schemas/table-record-rules.schema.json').read_text())).validate(r)
    validator=jsonschema.Draft202012Validator(json.loads((root/'schemas/table-records.schema.json').read_text()));validator.validate(v)
    for key,value in [('publication_eligible',True),('entity_identity_approved',0)]:
        changed=deepcopy(v);changed[key]=value
        with pytest.raises(jsonschema.ValidationError):validator.validate(changed)
    row=deepcopy(FIRST);row[4],row[5]=row[5],row[4]
    v=compute(data([table('t1',1,[HEADER,row])]))
    assert any(g['kind']=='reversed_period_date_candidates'for g in v['gaps'])


def test_bundle_replay_denies_rehashed_tampering_and_approval(tmp_path,monkeypatch):
    d=data([table('t1',1,[HEADER,FIRST])]);rules=tmp_path/'rules.json';rules.write_text(json.dumps(rule(['t1'])))
    package=tmp_path/'package';package.mkdir()
    for n in m.SOURCE_FILES:
        p=package/n;p.parent.mkdir(exist_ok=True,parents=True);p.write_text('source')
    def run(p,r,t):return m.compute(d,m.load_rules(r),m.file_sha256(r)),{n:m.file_sha256(package/n)for n in m.SOURCE_FILES},m.implementation()
    monkeypatch.setattr(m,'_run',run);out=tmp_path/'out';m.extract(package,rules,out);assert m.verify(out)['valid']
    packet=json.loads((out/'table-records.json').read_text());packet['entity_identity_approved']=True;m.write(out/'table-records.json',packet)
    manifest=json.loads((out/'manifest.json').read_text());manifest['files']['table-records.json']=m.file_sha256(out/'table-records.json');m.write(out/'manifest.json',manifest)
    with pytest.raises(ValueError):m.verify(out)
    with pytest.raises(ValueError):m.extract(package,rules,out)


def test_duplicate_json_and_timeout_leave_no_output(tmp_path,monkeypatch):
    p=tmp_path/'rules.json';p.write_text('{"id":"a","id":"b"}')
    with pytest.raises(ValueError):m.load_rules(p)
    p.write_text(json.dumps(rule(['t1'])));package=tmp_path/'package';package.mkdir()
    for n in m.SOURCE_FILES:
        q=package/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_text('source')
    from local_vector_batch import legal_l1
    monkeypatch.setattr(legal_l1,'verify_document',lambda p:None)
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('worker',1)
    monkeypatch.setattr(m.subprocess,'run',timeout)
    with pytest.raises(ValueError,match='time budget'):m.extract(package,p,tmp_path/'out',1)
    assert not(tmp_path/'out').exists()


def test_existing_borderless_grid_without_geometry_retains_cells_but_cannot_bridge_pages():
    a=table('t1',1,[HEADER,FIRST]);b=table('t2',2,[HEADER,NEXT])
    for fragment in (a,b):
        fragment.pop('cell_spans');fragment.pop('bbox')
    source=data([a,b]);before=deepcopy(source);result=compute(source)
    assert source==before
    assert result['counts']['charge_periods']==2
    assert len(result['source_cells'])==32
    assert all(c['locator']['bbox']is None for c in result['source_cells'])
    assert all(c['state']=='unresolved_continuation' for c in result['continuation_candidates'])
    later=next(row for row in result['rows']if row['physical_page']==2 and row['kind']=='charge_period')
    assert not later['group_context_candidates']
    assert set(later['unresolved_group_levels'])=={'company','application','tranche'}
