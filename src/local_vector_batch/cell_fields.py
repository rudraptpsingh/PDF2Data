"""Configured extraction over exact native cell text; page offsets are never invented."""
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from .field_rules import load as load_fields, flags, write
from .hashing import file_sha256

SCHEMA='pdf2data.configured-cell-fields.v1'
SCHEMA_V2='pdf2data.configured-cell-fields.v2'
SOURCE_FILES=('original.pdf','source-receipt.json','l1-package.json','extraction/pages.jsonl','extraction/tables.json','quality.json')
CODE_FILES=('cell_fields.py','field_rules.py','formula_observations.py')

def decode(raw):
    def pairs(items):
        out={}
        for key,value in items:
            if key in out:raise ValueError('Duplicate JSON key '+key)
            out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON '+x)))

def digest(obj):return sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def implementation():
    return {name:file_sha256(Path(__file__).with_name(name))for name in CODE_FILES}

def load_rules(path):
    decode(Path(path).read_bytes())
    rules=load_fields(path)
    if rules.get('cell_fields_schema_version',SCHEMA)not in(SCHEMA,SCHEMA_V2):raise ValueError('Use explicit cell fields v1 or v2 schema')
    for f in rules['fields']:
        if f.get('extractor','regex')!='regex' or f['type']=='formula' or f.get('scope','all')!='all':raise ValueError('Cell fields require regex and all cell-local scope; formulas need a separate verified representation')
        columns=f.get('cell_columns')
        if not isinstance(columns,list)or not columns or any(type(c)is not int or c<0 for c in columns)or len(columns)!=len(set(columns)):raise ValueError('Declare distinct nonnegative cell_columns')
        for key in ('cell_rows',):
            if key in f and(not isinstance(f[key],list)or not f[key]or any(type(c)is not int or c<0 for c in f[key])):raise ValueError('cell_rows must be nonempty nonnegative integers')
        if 'table_ids'in f and(not isinstance(f['table_ids'],list)or not f['table_ids']or any(not isinstance(v,str)or not v for v in f['table_ids'])):raise ValueError('table_ids must be nonempty identifiers')
    return rules

def source_data(package):
    receipt=decode((package/'source-receipt.json').read_bytes())
    return dict(source_pdf_sha256=receipt['actual_sha256'],source_package_sha256=file_sha256(package/'l1-package.json'),
                declared_family=receipt['declared'].get('family','generic'),
                pages=[decode(line)for line in(package/'extraction/pages.jsonl').read_text().split('\n')if line.strip()],
                tables=decode((package/'extraction/tables.json').read_bytes()),quality=decode((package/'quality.json').read_bytes()))

def cells(table,index):
    physical=table.get('cell_spans',[])
    if physical:
        for k,c in enumerate(physical):
            yield dict(source_pointer=f'/tables/{index}/cell_spans/{k}',cell_id=c.get('id'),row=c['row'],column=c['column'],
                       row_span=c.get('row_span',1),column_span=c.get('column_span',1),bbox=c.get('bbox'),raw=c.get('text'),source_basis='physical_cell_metadata')
    else:
        for row,values in enumerate(table.get('cells',[])):
            for column,value in enumerate(values):
                yield dict(source_pointer=f'/tables/{index}/cells/{row}/{column}',cell_id=None,row=row,column=column,
                           row_span=1,column_span=1,bbox=None,raw=value,source_basis='grid_only_geometry_unavailable')

def compute(data,rules,rules_sha):
    rows=[];counts={};pages={p['page']:p for p in data['pages']}
    for page in pages.values():
        if sha256(page['text'].encode()).hexdigest()!=page['text_sha256']:raise ValueError('Page text SHA256 differs')
    for field in rules['fields']:
        found=[];families=field.get('families',rules.get('families',[]))
        if families and data['declared_family']not in families:
            counts[field['id']]=dict(status='not_applicable_to_declared_family',matches=0);continue
        pattern=re.compile(field['pattern'],flags(field))
        for i,table in enumerate(data['tables']):
            if field.get('pages')and table['page']not in field['pages']:continue
            if field.get('table_ids')and table['id']not in field['table_ids']:continue
            if table['page']not in pages:raise ValueError('Cell table page absent')
            for cell in cells(table,i):
                if cell['column']not in field['cell_columns']or(field.get('cell_rows')and cell['row']not in field['cell_rows']):continue
                text=cell['raw']
                if not isinstance(text,str)or not text:continue
                literal_sha=sha256(text.encode()).hexdigest()
                def span(a,b):return dict(char_start=a,char_length=b-a,exact_quote=text[a:b],offset_encoding='unicode_codepoint')
                for match in pattern.finditer(text):
                    a,b=match.span('value')
                    if a<0 or a==b:continue
                    literal=text[a:b];typed=None;issues=[]
                    if field['type']=='date':
                        try:typed=datetime.strptime(literal,field['date_format']).date().isoformat()
                        except(KeyError,ValueError):issues.append('date_does_not_match_configured_format')
                    elif field['type']=='quantity':
                        from .formula_observations import stated_number
                        try:
                            n=stated_number(literal);typed=dict(numerator=str(n.numerator),denominator=str(n.denominator))
                        except(ValueError,ArithmeticError):issues.append('ambiguous_numeric_literal')
                    unit=match.groupdict().get('unit');unit_span=span(*match.span('unit'))if unit else None
                    locator={k:v for k,v in cell.items()if k!='raw'}
                    locator.update(table_id=table['id'],table_version_id=table.get('table_version_id'),table_geometry_sha256=table.get('table_geometry_sha256'),
                                   physical_page=table['page'],page_text_sha256=pages[table['page']]['text_sha256'],cell_text_sha256=literal_sha,
                                   table_status=table.get('status'),bbox_coordinates=table.get('bbox_coordinates'))
                    row=dict(field_id=field['id'],type=field['type'],value_literal=literal,typed_candidate=typed,
                             unit_literal=unit,unit_cell_span=unit_span,configured_unit_hint=field.get('unit'),
                             cell_locator=locator,cell_source_span=span(a,b),match_cell_span=span(match.start(),match.end()),
                             rules_sha256=rules_sha,source_pdf_sha256=data['source_pdf_sha256'],source_package_sha256=data['source_package_sha256'],
                             source_quality=next((q['outcome']for q in data.get('quality',{}).get('results',[])if q['page']==table['page']),'unknown'),
                             status='candidate',content_verified=False,calculation_allowed=False,semantic_scope_verified=False,
                             validation_issues=issues,selected_page_offset_assigned=False)
                    if rules.get('cell_fields_schema_version',SCHEMA)==SCHEMA_V2:
                        for dimension in('currency','scale'):
                            captured=match.groupdict().get(dimension)or None
                            row[dimension+'_literal']=captured
                            row[dimension+'_cell_span']=span(*match.span(dimension))if captured else None
                        row.update(currency_code_candidate=None,scale_multiplier_candidate=None,dimensional_normalization_allowed=False)
                    row['id']='cell-field-'+digest([rules_sha,field['id'],row['source_pdf_sha256'],row['source_package_sha256'],locator,row['cell_source_span']])[:24]
                    found.append(row)
                    if len(rows)+len(found)>50000:raise ValueError('More than 50000 cell fields; narrow rules')
        rows+=found;counts[field['id']]=dict(status='candidates_found'if found else'not_detected_not_proven_absent',matches=len(found))
    result=dict(schema_version=rules.get('cell_fields_schema_version',SCHEMA),source_pdf_sha256=data['source_pdf_sha256'],source_package_sha256=data['source_package_sha256'],
                rule_set_id=rules['id'],rule_version=rules['version'],rules_sha256=rules_sha,field_catalog=rules['fields'],
                observations=rows,field_status=counts,declared_family=data['declared_family'],family_is_verified_legal_scope=False,
                source_quality_approval_granted=False,publication_eligible=False,calculation_allowed=False,
                scope='explicit_cell_column_and_optional_table_page_row_filters_no_fill_down_or_continuation_inference')
    if result['schema_version']==SCHEMA_V2:result['producer_implementation']=implementation()
    return result

def _run(package,rules_path,timeout):
    from .legal_l1 import verify_document
    if type(timeout)is not int or not 1<=timeout<=600:raise ValueError('Use timeout 1..600 seconds')
    try:verify_document(package)
    except ModuleNotFoundError as error:raise ValueError('Install PDF2Data[pdf] for source package verification; missing '+str(error.name))from None
    load_rules(rules_path)
    before={n:file_sha256(package/n)for n in SOURCE_FILES};rules_sha=file_sha256(rules_path);code=implementation()
    with tempfile.TemporaryDirectory(prefix='pdf2data-cell-fields-')as tmp:
        request=Path(tmp)/'request.json';output=Path(tmp)/'output.json'
        write(request,dict(package=str(package),rules=str(rules_path),rules_sha256=rules_sha))
        try:worker=subprocess.run([sys.executable,'-m','local_vector_batch.cell_fields','--worker',str(request),str(output)],env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])},capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired:raise ValueError('Cell rule evaluation exceeded time budget; no partial output')from None
        if worker.returncode or not output.exists():raise ValueError('Cell rule worker failed: '+worker.stderr[-1000:])
        if output.stat().st_size>128*1024*1024:raise ValueError('Cell fields exceed 128 MB; narrow rules')
        result=decode(output.read_bytes())
    if file_sha256(rules_path)!=rules_sha or before!={n:file_sha256(package/n)for n in SOURCE_FILES}or code!=implementation():raise ValueError('Source, rules or implementation changed during extraction')
    return result,before,code

def extract(package,rules_path,output,timeout=30):
    package=Path(package).resolve();rules_path=Path(rules_path).resolve();output=Path(output).resolve()
    if output.exists()or output.is_relative_to(package)or package.is_relative_to(output):raise ValueError('Use a new output outside immutable source package')
    result,source_files,code=_run(package,rules_path,timeout)
    output.mkdir(parents=True);(output/'rules.json').write_bytes(rules_path.read_bytes());write(output/'cell-fields.json',result)
    write(output/'manifest.json',dict(schema_version='pdf2data.cell-field-bundle.v1',source_package=str(package),source_files=source_files,
                                   implementation_files_sha256=code,files={n:file_sha256(output/n)for n in('rules.json','cell-fields.json')},publication_eligible=False))
    return dict(schema_version=result['schema_version'],output=str(output),observations=len(result['observations']),field_status=result['field_status'],source_quality_changed=False,publication_eligible=False)

def verify(bundle,timeout=30):
    bundle=Path(bundle).resolve();manifest=decode((bundle/'manifest.json').read_bytes())
    if manifest.get('schema_version')!='pdf2data.cell-field-bundle.v1':raise ValueError('Unsupported cell field bundle')
    if set(manifest.get('files',{}))!={'rules.json','cell-fields.json'}or set(manifest.get('source_files',{}))!=set(SOURCE_FILES):raise ValueError('Incomplete cell field manifest')
    if manifest.get('implementation_files_sha256')!=implementation():raise ValueError('Cell field implementation differs')
    if any(file_sha256(bundle/n)!=h for n,h in manifest['files'].items()):raise ValueError('Cell field artifact changed')
    package=Path(manifest['source_package'])
    if any(file_sha256(package/n)!=h for n,h in manifest['source_files'].items()):raise ValueError('Cell field source changed')
    expected,hashes,code=_run(package,bundle/'rules.json',timeout)
    actual=decode((bundle/'cell-fields.json').read_bytes())
    if digest(expected)!=digest(actual)or hashes!=manifest['source_files']or code!=manifest['implementation_files_sha256']:raise ValueError('Cell field output differs from source re-derivation')
    return dict(schema_version='pdf2data.cell-field-verification.v1',valid=True,source_pdf_sha256=actual['source_pdf_sha256'],source_package_sha256=actual['source_package_sha256'],observations=len(actual['observations']),publication_eligible=False)

def configure(parser):
    sub=parser.add_subparsers(dest='cell_field_operation',required=True)
    p=sub.add_parser('extract');p.add_argument('--package',type=Path,required=True);p.add_argument('--rules',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--bundle',type=Path,required=True)
    for child in(p,v):child.add_argument('--timeout',type=int,default=30);child.add_argument('--json',action='store_true')

def execute(args):
    if args.cell_field_operation=='verify':return verify(args.bundle,args.timeout)
    return extract(args.package,args.rules,args.output,args.timeout)

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise SystemExit('Use pdf2data cell-fields')
    request=decode(Path(sys.argv[2]).read_bytes());package=Path(request['package']);rules_path=Path(request['rules'])
    if file_sha256(rules_path)!=request['rules_sha256']:raise ValueError('Rules changed')
    write(Path(sys.argv[3]),compute(source_data(package),load_rules(rules_path),request['rules_sha256']))
