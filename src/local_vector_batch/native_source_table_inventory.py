"""Additive source table views for the historical one-row extraction omission.

Original archived tables remain byte-identical observations. A separately
versioned child view accounts for every fresh source grid; it grants no quality
until the final original-source decoder and all ordinary region gates pass.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path

CONTRACT='pdf2data.additive-source-table-inventory.v1'


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(value if isinstance(value,bytes)else value.encode()).hexdigest()
def box(table):return [round(v,2)for v in table['bbox']]
def shape(table):
    return {k:(box(table)if k=='bbox'else table[k])for k in ('bbox','table_geometry_sha256','row_count','column_count','unresolved_grid_slots')}
def cells(table):return [{k:c[k]for k in ('row','column','row_span','column_span','bbox','text')}for c in table['cell_spans']]


def historical_one_row_filter(origin):
    path=Path(origin)/'src/local_vector_batch/pdf_ingest.py';raw=path.read_bytes();tree=ast.parse(raw)
    functions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n.name=='extract_pdf']
    if len(functions)!=1:return None
    expected=ast.dump(ast.parse('if len(cells) < 2:\n    continue').body[0],include_attributes=False)
    guards=[n for n in ast.walk(functions[0])if isinstance(n,ast.If)and ast.dump(n,include_attributes=False)==expected]
    if len(guards)!=1:return None
    return {'implementation_sha256':sha(raw),'function':'extract_pdf','omission_predicate':'len(cells) < 2',
        'minimum_recorded_matrix_rows':2,'guard_line':guards[0].lineno,'source_code_executed':False}


def build_inventory_view(snapshot,origin):
    raw=snapshot['raw_tables'];fresh=snapshot['tables']
    if len(raw)==len(fresh):return None
    guard=historical_one_row_filter(origin)
    if guard is None or len(fresh)<=len(raw):return None
    additions=[];matched=set()
    for table in fresh:
        matches=[(i,t)for i,t in enumerate(raw)if box(t)==box(table)]
        if matches:
            if len(matches)!=1:raise ValueError('Original table membership is ambiguous')
            i,original=matches[0]
            if i in matched or shape(original)!=shape(table)or cells(original)!=cells(table):
                raise ValueError('Original table changed during additive inventory capture')
            matched.add(i);continue
        matrix=table.get('native_raw_matrix')
        if (table['row_count']!=1 or table['column_count']<2 or table['unresolved_grid_slots']or
                not isinstance(matrix,list)or len(matrix)!=1 or len(matrix[0])!=table['column_count']or
                not any(isinstance(v,str)and v.strip()for v in matrix[0])):
            raise ValueError('Additional table is outside the historical one-row omission rule')
        for other in [*raw,*additions]:
            a,b=table['bbox'],other['bbox']
            if min(a[2],b[2])>max(a[0],b[0])and min(a[3],b[3])>max(a[1],b[1]):
                raise ValueError('Additive source table overlaps another preserved grid')
        additions.append({k:copy.deepcopy(table[k])for k in ('id','page','source_pdf_sha256','bbox','table_version_id',
            'table_geometry_sha256','row_count','column_count','unresolved_grid_slots','cell_spans')})
        additions[-1].update(cells=copy.deepcopy(matrix),extraction_basis='native_pdf_geometry',
            observation_role='additive_fresh_source_table_not_in_archived_raw_inventory')
    if matched!=set(range(len(raw))):raise ValueError('Additive source view cannot omit an archived table')
    view={'contract_version':CONTRACT,'source_pdf_sha256':snapshot['source_pdf_sha256'],'page':snapshot['page'],
        'page_text_sha256':snapshot['page_text_sha256'],'original_raw_inventory_sha256':sha(canonical(raw)),
        'original_raw_table_count':len(raw),'complete_fresh_table_count':len(fresh),
        'fresh_geometry_sequence':[shape(t)for t in fresh],'origin_table_filter':guard,
        'additive_tables':additions,'original_raw_inventory_modified':False,'source_quality_clearance':False,
        'legal_review':'not_run','answer_publication_eligible':False,'implementation_sha256':sha(Path(__file__).read_bytes())}
    view['view_version_sha256']=sha(canonical(view));return view


def effective_source_tables(snapshot):
    raw=snapshot['raw_tables'];view=snapshot.get('source_table_inventory_view')
    if view is None:return raw
    expected={'contract_version':CONTRACT,'source_pdf_sha256':snapshot['source_pdf_sha256'],'page':snapshot['page'],
        'page_text_sha256':snapshot['page_text_sha256'],'original_raw_inventory_sha256':sha(canonical(raw)),
        'original_raw_table_count':len(raw),'complete_fresh_table_count':len(snapshot['tables']),
        'fresh_geometry_sequence':[shape(t)for t in snapshot['tables']],
        'original_raw_inventory_modified':False,'source_quality_clearance':False,'legal_review':'not_run',
        'answer_publication_eligible':False,'implementation_sha256':sha(Path(__file__).read_bytes())}
    if any(view.get(k)!=v for k,v in expected.items())or view.get('view_version_sha256')!=sha(canonical({k:v for k,v in view.items()if k!='view_version_sha256'})):
        raise ValueError('Additive source table view identity differs')
    guard=view.get('origin_table_filter',{})
    if guard.get('omission_predicate')!='len(cells) < 2'or guard.get('minimum_recorded_matrix_rows')!=2:
        raise ValueError('Unknown historical table omission predicate')
    combined=[*raw,*view['additive_tables']]
    if len(combined)!=len(snapshot['tables'])or len(view['additive_tables'])!=len(combined)-len(raw):
        raise ValueError('Additive source table inventory is incomplete')
    for addition in view['additive_tables']:
        if (addition.get('observation_role')!='additive_fresh_source_table_not_in_archived_raw_inventory'or
                addition['row_count']!=1 or addition['column_count']<2):
            raise ValueError('Unknown additive table source observation')
    # Closed geometry and full matrix/cell validation follows in validate_feedback.
    # Final admission independently captures the original PDF again, not this JSON.
    return combined
