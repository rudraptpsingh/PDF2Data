"""Versioned, source-anchored field rules. Configuration never grants approval."""
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .document_blocks import digest,span
from .document_metadata import field_geometry
from .hashing import file_sha256

SCHEMA='pdf2data.extraction-rules.v1'
FIELDS_V1='pdf2data.configured-fields.v1'
FIELDS_V2='pdf2data.configured-fields.v2'
FIELDS_V3='pdf2data.configured-fields.v3'
TYPES={'text','date','quantity','reference','formula'}
SOURCE_FILES=('extraction/pages.jsonl','layout.jsonl','extraction/tables.json','structure.json','quality.json')
WORD_QUANTITIES={'english_cardinal_0_100_v1','english_cardinal_percent_0_100_v1'}


def quantity_config(field):
    if 'quantity_normalization' not in field:return
    mode=field['quantity_normalization']
    if field['type']!='quantity' or not isinstance(mode,str) or mode not in WORD_QUANTITIES:
        raise ValueError('Unknown quantity normalization or non-quantity field')
    if mode=='english_cardinal_percent_0_100_v1' and 'unit' not in re.compile(field.get('pattern',''),flags(field)).groupindex:
        raise ValueError('Word percent normalization requires a printed named unit group inside value')


def quantity_candidate(literal,field,unit=None):
    """Exact magnitude only. No unit inference, comparison parsing or approval."""
    mode=field.get('quantity_normalization')
    if mode is None:
        from .formula_observations import stated_number
        n=stated_number(literal)
        return dict(numerator=str(n.numerator),denominator=str(n.denominator))
    quantity_config(field)
    words=literal
    if mode=='english_cardinal_percent_0_100_v1':
        # Require the separately anchored printed witness, not a configured hint.
        if not unit or not re.fullmatch(r'(?:percent|per\s+cent|%)',unit,re.IGNORECASE) or not literal.endswith(unit):
            raise ValueError('Unsupported printed percent unit')
        words=literal[:-len(unit)].rstrip()
        if not words or (unit!='%' and literal[len(words):len(literal)-len(unit)]==''):
            raise ValueError('Word percent needs a separated printed unit')
    small='zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
    tens='twenty thirty forty fifty sixty seventy eighty ninety'.split()
    numbers={word:i for i,word in enumerate(small)}
    numbers.update({word:(i+2)*10 for i,word in enumerate(tens)})
    # Only ordinary cardinal forms: no OCR repair, punctuation, ranges, mixed
    # digits, fractions, signs, conjunctions, locale or larger-scale guesses.
    if not re.fullmatch(r'[A-Za-z]+(?:\s+[A-Za-z]+|-[A-Za-z]+)?',words):
        raise ValueError('Unsupported English cardinal literal')
    tokens=re.split(r'\s+|-',words.lower())
    if len(tokens)==1 and tokens[0] in numbers:n=numbers[tokens[0]]
    elif len(tokens)==2 and tokens[0] in tens and tokens[1] in small[1:10]:n=numbers[tokens[0]]+numbers[tokens[1]]
    elif tokens==['one','hundred'] and '-' not in words:n=100
    else:raise ValueError('Unsupported English cardinal literal')
    return dict(numerator=str(n),denominator='1')


def quantity_from_match(match,field):
    if field.get('quantity_normalization')=='english_cardinal_percent_0_100_v1':
        a,b=match.span('value');x,y=match.span('unit')
        if not a < x < y == b:raise ValueError('Printed percent witness must end the value span')
    return quantity_candidate(match.group('value'),field,match.groupdict().get('unit'))


def implementation():
    root=Path(__file__).parent
    return {name:file_sha256(root/name)for name in ('field_rules.py','formula_observations.py','document_blocks.py','document_metadata.py','multipart_fields.py')}


def _decode(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate field JSON key: '+key)
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite field JSON '+x)))


def capture_config(rules,field,compiled=None):
    version=rules.get('fields_schema_version',FIELDS_V1)
    if version not in (FIELDS_V1,FIELDS_V2,FIELDS_V3):raise ValueError('Unknown configured fields schema')
    captures=field.get('capture_groups',[])
    if not isinstance(captures,list)or len(captures)>32 or any(not isinstance(n,str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',n)for n in captures)or len(captures)!=len(set(captures)):
        raise ValueError('Use at most 32 unique named capture groups')
    if 'capture_groups'in field and version not in (FIELDS_V2,FIELDS_V3):raise ValueError('Named captures require opt-in configured-fields.v2 or v3')
    if captures:
        if field.get('extractor','regex')!='regex':raise ValueError('Named captures require the regex extractor')
        compiled=compiled or re.compile(field['pattern'],flags(field))
        if any(n=='value'or n not in compiled.groupindex for n in captures):raise ValueError('Capture names must be declared regex groups other than value')
    return captures


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def source_data(package,source_sha):
    from .document_blocks import build_blocks
    def lines(name):return [json.loads(l)for l in(package/name).open(encoding='utf-8')if l.strip()]
    def value(name):return json.loads((package/name).read_text(encoding='utf-8'))
    data=dict(source_pdf_sha256=source_sha,pages=lines('extraction/pages.jsonl'),layout=lines('layout.jsonl'),
              tables=value('extraction/tables.json'),structure=value('structure.json'),quality=value('quality.json'))
    for page in data['pages']:
        if sha256(page['text'].encode()).hexdigest()!=page['text_sha256']:raise ValueError('Selected source text hash differs')
    data['document_blocks']=build_blocks(data)
    return data


def load(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('fields_schema_version')in (FIELDS_V2,FIELDS_V3):value=_decode(Path(path).read_bytes())
    if value.get('schema_version')!=SCHEMA or not isinstance(value.get('id'),str)or not value['id']or not isinstance(value.get('version'),str)or not value['version']:raise ValueError('Rules need schema_version, id and nonempty string version')
    if not isinstance(value.get('families',[]),list)or any(not isinstance(v,str)for v in value.get('families',[])):raise ValueError('Families must be a list of declared category names')
    fields=value.get('fields');ids=[]
    if not isinstance(fields,list)or not 1<=len(fields)<=200:raise ValueError('Configure 1..200 fields')
    for field in fields:
        if not isinstance(field,dict)or not isinstance(field.get('id'),str)or not isinstance(field.get('type'),str):raise ValueError('Each field needs string id and type')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}',field['id'])or field['type']not in TYPES:raise ValueError('Invalid field id or type')
        if not isinstance(field.get('pages',[]),list)or any(type(p)is not int or p<1 for p in field.get('pages',[])):raise ValueError('Pages must be positive physical page numbers')
        if not isinstance(field.get('families',[]),list)or any(not isinstance(v,str)for v in field.get('families',[])):raise ValueError('Field families must be category names')
        if value.get('fields_schema_version')==FIELDS_V3:
            from .multipart_fields import validate_field
            validate_field(field)
        elif any(n in field for n in ('native_unit_ids','native_unit_labels')):raise ValueError('Native unit selectors require explicit fields v3')
        elif field.get('scope','all')not in ('all','body_candidate','header_candidate','footer_candidate'):raise ValueError('Unknown source-region scope')
        if field.get('extractor','regex')=='formulas':
            if field['type']!='formula':raise ValueError('Formula extractor needs type formula')
            if field.get('scope','all')!='all':raise ValueError('Formula region scopes need a verified component graph; use all and explicit pages')
        elif field.get('extractor','regex')=='regex':
            pattern=field.get('pattern','')
            if not isinstance(pattern,str)or len(pattern)>2000:raise ValueError('Use a regex string of at most 2000 characters')
            try:compiled=re.compile(pattern,flags(field))
            except re.error as error:raise ValueError('Invalid field regex: '+str(error))from None
            if 'value'not in compiled.groupindex:raise ValueError('Regex needs a named (?P<value>...) group')
        else:raise ValueError('Use regex or formulas extractor')
        capture_config(value,field)
        quantity_config(field)
        ids.append(field['id'])
    if len(ids)!=len(set(ids)):raise ValueError('Field IDs must be unique')
    return value


def flags(field):
    result=0
    for name in field.get('flags',[]):
        if name not in ('IGNORECASE','MULTILINE','DOTALL'):raise ValueError('Unsupported regex flag')
        result|=getattr(re,name)
    return result


def catalog(path,search=None):
    rules=load(path);fields=rules['fields']
    if search:fields=[f for f in fields if search.casefold()in json.dumps(f,ensure_ascii=False).casefold()]
    return dict(schema_version=SCHEMA,id=rules['id'],version=rules['version'],rules_sha256=file_sha256(Path(path)),
                fields=fields,field_contract='candidate_only_source_quotes_and_spans_required_no_legal_or_calculation_approval')


def compute(data,rules,rules_sha,family):
    version=rules.get('fields_schema_version',FIELDS_V1)
    if version not in (FIELDS_V1,FIELDS_V2,FIELDS_V3):raise ValueError('Unknown configured fields schema')
    if version==FIELDS_V3:
        from .multipart_fields import compute as multipart_compute
        return multipart_compute(data,rules,rules_sha,family,implementation())
    if version==FIELDS_V2 and not re.fullmatch(r'[a-f0-9]{64}',data.get('source_package_sha256','')):raise ValueError('Configured fields v2 requires exact source package SHA256')
    layouts={p['page']:p for p in data['layout']};fields=[];counts={};formula_records=None
    for field in rules['fields']:
        capture_names=capture_config(rules,field)
        applicable=field.get('families',rules.get('families',[]));found=[]
        if applicable and family not in applicable:
            counts[field['id']]=dict(status='not_applicable_to_declared_family',matches=0);continue
        if field.get('extractor','regex')=='formulas':
            if formula_records is None:
                from .formula_observations import extract_formulas
                formula_records=extract_formulas(data)['formulas']
            for f in formula_records:
                if field.get('pages')and f['source_span']['page']not in field['pages']:continue
                f.setdefault('answer_gate',dict(structured_math_allowed=False,calculation_allowed=False,reasons=['formula_structure_not_proven_by_configured_field_match']))
                found.append(dict(value_literal=f['literal'],source_span=f['source_span'],geometry=f['geometry'],
                                  formula_observation=f,typed_candidate=None,validation_issues=['formula_semantics_require_independent_verification']))
        else:
            pattern=re.compile(field['pattern'],flags(field))
            for page in data['pages']:
                if field.get('pages')and page['page']not in field['pages']:continue
                scope=field.get('scope','all')
                ranges=[(0,len(page['text']))]if scope=='all'else[(r['span']['char_start'],r['span']['char_start']+r['span']['char_length'])for r in data['structure']['regions']if r['span']['page']==page['page']and r['kind']==scope]
                for lo,hi in ranges:
                    for match in pattern.finditer(page['text'],lo,hi):
                        a,b=match.span('value')
                        if a<0 or a==b:continue
                        literal=page['text'][a:b];typed=None;issues=[]
                        if field['type']=='date':
                            if not field.get('date_format'):issues.append('date_format_not_configured')
                            else:
                                try:typed=datetime.strptime(literal,field['date_format']).date().isoformat()
                                except ValueError:issues.append('date_does_not_match_declared_format')
                        elif field['type']=='quantity':
                            try:
                                typed=quantity_from_match(match,field)
                            except (ValueError,ArithmeticError):issues.append('unsupported_word_numeric_literal' if field.get('quantity_normalization') else 'ambiguous_numeric_literal')
                        groups=match.groupdict();unit=groups.get('unit')
                        item=dict(value_literal=literal,typed_candidate=typed,source_span=span(page,a,b),
                                  match_source_span=span(page,match.start(),match.end()),unit_literal=unit,
                                  configured_unit_hint=field.get('unit'),validation_issues=issues,
                                  geometry=field_geometry(page,layouts.get(page['page'],{}),a,b))
                        if unit:
                            u,v=match.span('unit');item['unit_source_span']=span(page,u,v)
                        if version==FIELDS_V2:
                            captures={}
                            for name in capture_names:
                                x,y=match.span(name)
                                if x<0 or x==y:
                                    captures[name]=dict(value_literal=None,source_span=None,status='not_matched')
                                    continue
                                if not a<=x<y<=b:raise ValueError('Named capture lies outside the value span; widen the explicit value group')
                                captures[name]=dict(value_literal=page['text'][x:y],source_span=span(page,x,y),status='matched')
                            item['captures']=captures
                            item['capture_association']='configured_same_value_match_candidate_not_semantic_ownership'
                        found.append(item)
        for item in found:
            s=item['source_span']
            item.update(id='field-'+digest([data['source_pdf_sha256'],rules_sha,field['id'],s])[:24],field_id=field['id'],
                        type=field['type'],rule_set_id=rules['id'],rule_version=rules['version'],rules_sha256=rules_sha,
                        source_pdf_sha256=data['source_pdf_sha256'],status='candidate',content_verified=False,calculation_allowed=False,
                        source_quality=next((q['outcome']for q in data.get('quality',{}).get('results',[])if q['page']==s['page']),'unknown'))
            fields.append(item)
            if len(fields)>50000:raise ValueError('More than 50000 field candidates; narrow rules instead of clipping output')
        counts[field['id']]=dict(status='candidates_found'if found else'not_detected_not_proven_absent',matches=len(found))
    result=dict(schema_version=version,source_pdf_sha256=data['source_pdf_sha256'],declared_family=family,
                family_is_verified_legal_scope=False,rule_set_id=rules['id'],rule_version=rules['version'],rules_sha256=rules_sha,
                field_catalog=rules['fields'],field_status=counts,observations=fields,publication_eligible=False)
    if version==FIELDS_V2:
        result.update(source_package_sha256=data['source_package_sha256'],producer_implementation=implementation(),semantic_ownership_approved=False)
        for item in fields:
            item.setdefault('captures',{})
            item.setdefault('capture_association','configured_same_value_match_candidate_not_semantic_ownership')
    return result


def extract(package,rules_path,output,timeout=30):
    from .legal_l1 import verify_document
    package=Path(package).resolve();rules_path=Path(rules_path).resolve();output=Path(output).resolve()
    rules=load(rules_path)
    if output.exists()or output.is_relative_to(package)or package.is_relative_to(output):raise ValueError('Use a new output directory outside immutable source packages')
    if not 1<=timeout<=600:raise ValueError('Use a rule timeout of 1..600 seconds')
    v2=rules.get('fields_schema_version',FIELDS_V1)in (FIELDS_V2,FIELDS_V3)
    verify_document(package);names=(*SOURCE_FILES,'original.pdf','source-receipt.json',*(('l1-package.json',)if v2 else()));hashes={n:file_sha256(package/n)for n in names};rules_sha=file_sha256(rules_path);impl=implementation()if v2 else None
    with tempfile.TemporaryDirectory(prefix='pdf2data-fields-')as temp:
        inp=Path(temp)/'request.json';out=Path(temp)/'output.json';write(inp,dict(package=str(package),rules=str(rules_path),rules_sha256=rules_sha))
        try:worker=subprocess.run([sys.executable,'-m','local_vector_batch.field_rules','--worker',str(inp),str(out)],env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])},capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired:raise ValueError('Rule evaluation exceeded time budget; no partial fields exported')from None
        if worker.returncode or not out.exists():raise ValueError('Rule worker failed: '+worker.stderr[-1000:])
        if out.stat().st_size>128*1024*1024:raise ValueError('Fields exceed 128 MB; narrow rules')
        result=json.loads(out.read_text(encoding='utf-8'))
    if file_sha256(rules_path)!=rules_sha or any(file_sha256(package/n)!=h for n,h in hashes.items()):raise ValueError('Rules or source changed during extraction')
    if v2 and(implementation()!=impl or result.get('producer_implementation')!=impl):raise ValueError('Field producer implementation changed during extraction')
    output.mkdir(parents=True);(output/'rules.json').write_bytes(rules_path.read_bytes());write(output/'fields.json',result)
    manifest=dict(schema_version='pdf2data.field-bundle.v3'if rules.get('fields_schema_version')==FIELDS_V3 else'pdf2data.field-bundle.v2'if v2 else'pdf2data.field-bundle.v1',source_files=hashes,source_package=str(package),
                  files={n:file_sha256(output/n)for n in ('rules.json','fields.json')},publication_eligible=False)
    if v2:manifest.update(producer_implementation=impl,source_quality_approval_granted=False,semantic_ownership_approved=False)
    write(output/'manifest.json',manifest)
    return dict(output=str(output),fields=len(rules['fields']),observations=len(result['observations']),field_status=result['field_status'],source_quality_changed=False,publication_eligible=False)


def verify(bundle,timeout=30):
    from .legal_l1 import verify_document
    bundle=Path(bundle).resolve();manifest=_decode((bundle/'manifest.json').read_bytes())
    if manifest.get('schema_version')not in ('pdf2data.field-bundle.v2','pdf2data.field-bundle.v3'):raise ValueError('Full field re-derivation requires a v2 or v3 bundle')
    if manifest.get('publication_eligible')is not False or manifest.get('source_quality_approval_granted')is not False or manifest.get('semantic_ownership_approved')is not False:raise ValueError('Field manifest cannot grant approval')
    if set(manifest.get('files',{}))!={'rules.json','fields.json'}or any(file_sha256(bundle/n)!=h for n,h in manifest['files'].items()):raise ValueError('Field bundle changed')
    if manifest.get('producer_implementation')!=implementation():raise ValueError('Use the captured field producer implementation')
    package=Path(manifest['source_package']);verify_document(package)
    expected={*SOURCE_FILES,'original.pdf','source-receipt.json','l1-package.json'}
    if set(manifest.get('source_files',{}))!=expected or any(file_sha256(package/n)!=h for n,h in manifest['source_files'].items()):raise ValueError('Field source package changed')
    rules=load(bundle/'rules.json')
    version=FIELDS_V3 if manifest['schema_version']=='pdf2data.field-bundle.v3'else FIELDS_V2
    if rules.get('fields_schema_version')!=version:raise ValueError('Field bundle/rules schema version differs')
    # Use the same bounded subprocess as extraction; a pathological rule cannot
    # escape its explicit worker timeout during verification.
    with tempfile.TemporaryDirectory(prefix='pdf2data-field-verify-')as temp:
        rebuilt=Path(temp)/'rebuilt';extract(package,bundle/'rules.json',rebuilt,timeout)
        original=_decode((bundle/'fields.json').read_bytes());derived=_decode((rebuilt/'fields.json').read_bytes())
    if digest(original)!=digest(derived):raise ValueError('Field bundle differs from native re-derivation')
    if any(file_sha256(bundle/n)!=h for n,h in manifest['files'].items())or any(file_sha256(package/n)!=h for n,h in manifest['source_files'].items())or implementation()!=manifest['producer_implementation']:raise ValueError('Field source/rules/producer changed during verification')
    return dict(schema_version=version,integrity='passed',observations=len(original['observations']),semantic_ownership_approved=False,publication_eligible=False)


def query(bundle,field=None,text=None,offset=0,limit=50):
    if offset<0 or not 1<=limit<=1000:raise ValueError('Use offset >=0 and limit 1..1000')
    if isinstance(bundle,list):
        # Verify every bundle before returning combined source-identified rows.
        results=[query(p,field,text,0,1000)for p in bundle]
        if any(r['next_offset']is not None for r in results):raise ValueError('Narrow the search; one bundle has more than 1000 matches')
        rows=[o for r in results for o in r['observations']]
        return dict(total=len(rows),offset=offset,next_offset=offset+limit if offset+limit<len(rows)else None,
                    observations=rows[offset:offset+limit],field_catalog=[f for r in results for f in r['field_catalog']],publication_eligible=False)
    bundle=Path(bundle).resolve();manifest=json.loads((bundle/'manifest.json').read_text())
    if any(file_sha256(bundle/n)!=h for n,h in manifest['files'].items()):raise ValueError('Field bundle changed')
    if offset<0 or not 1<=limit<=1000:raise ValueError('Use offset >=0 and limit 1..1000')
    data=json.loads((bundle/'fields.json').read_text())
    if data.get('schema_version')in (FIELDS_V2,FIELDS_V3)or manifest.get('schema_version')in ('pdf2data.field-bundle.v2','pdf2data.field-bundle.v3'):verify(bundle)
    rows=[r for r in data['observations']if (not field or r['field_id']==field)and(not text or text.casefold()in r['value_literal'].casefold())]
    if data['rules_sha256']!=manifest['files']['rules.json']or data['source_pdf_sha256']!=manifest['source_files']['original.pdf']:raise ValueError('Field/rules/source identity differs')
    return dict(total=len(rows),offset=offset,next_offset=offset+limit if offset+limit<len(rows)else None,
                observations=rows[offset:offset+limit],field_catalog=data['field_catalog'],publication_eligible=False)


def evaluate(bundle,references,output):
    bundle=Path(bundle).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new evaluation output directory')
    # Query verifies the frozen field/rule artifacts before evaluation.
    query(bundle)
    data=json.loads((bundle/'fields.json').read_text());manifest=json.loads((bundle/'manifest.json').read_text())
    if data.get('schema_version')==FIELDS_V3:
        from .multipart_fields import evaluate as multipart_evaluate
        return multipart_evaluate(bundle,references,output)
    reference=json.loads(Path(references).read_text(encoding='utf-8'))
    if reference.get('source_pdf_sha256')!=data['source_pdf_sha256']or reference.get('rules_sha256')!=data['rules_sha256']:raise ValueError('Reference source/rules version differs')
    if not reference.get('reviewer')or reference.get('reference_scope')not in ('human_source_review','development_fixture'):raise ValueError('Declare reviewer and reference provenance')
    package=Path(manifest['source_package']);pages={p['page']:p for p in source_data(package,data['source_pdf_sha256'])['pages']}
    if any(file_sha256(package/n)!=h for n,h in manifest['source_files'].items()):raise ValueError('Evaluation source changed after field extraction')
    known={f['id']for f in data['field_catalog']};rows=[];seen=set()
    for field in reference['fields']:
        name=field['field_id']
        if name not in known or name in seen:raise ValueError('Reference field is unknown or duplicated')
        seen.add(name);expected=set();normalization=[];units=[]
        observed={(r['source_span']['page'],r['source_span']['char_start'],r['source_span']['char_length'],r['value_literal']):r for r in data['observations']if r['field_id']==name}
        for s in field['expected']:
            p=pages[s['page']];literal=s['exact_quote'];a=s['char_start']
            if a<0 or not literal or p['text_sha256']!=s['page_text_sha256']or p['text'][a:a+len(literal)]!=literal:raise ValueError('Reference quote is not anchored to source')
            key=(s['page'],a,len(literal),literal);expected.add(key);actual_record=observed.get(key)
            if actual_record is not None:
                if 'typed_candidate'in s and s['typed_candidate']!=actual_record['typed_candidate']:normalization.append(dict(source=list(key),expected=s['typed_candidate'],actual=actual_record['typed_candidate']))
                if 'unit_literal'in s and s['unit_literal']!=actual_record.get('unit_literal'):units.append(dict(source=list(key),expected=s['unit_literal'],actual=actual_record.get('unit_literal')))
        actual=set(observed)
        rows.append(dict(field_id=name,true_positive=len(expected&actual),false_positive=len(actual-expected),false_negative=len(expected-actual),
                         missing=[list(v)for v in sorted(expected-actual)],extra=[list(v)for v in sorted(actual-expected)],normalization_mismatches=normalization,unit_mismatches=units))
    if not rows:raise ValueError('Label at least one field, including explicit empty negative cases')
    tp=sum(r['true_positive']for r in rows);fp=sum(r['false_positive']for r in rows);fn=sum(r['false_negative']for r in rows)
    result=dict(schema_version='pdf2data.field-evaluation.v1',fields_evaluated=len(rows),unlabelled_fields=len(known-seen),
                true_positive=tp,false_positive=fp,false_negative=fn,precision=tp/(tp+fp)if tp+fp else 1.0,
                recall=tp/(tp+fn)if tp+fn else 1.0,passed=not(fp or fn or any(r['normalization_mismatches']or r['unit_mismatches']for r in rows)),results=rows,reference_scope=reference['reference_scope'],
                reviewer=reference['reviewer'],references_sha256=file_sha256(Path(references)),rules_sha256=data['rules_sha256'],
                source_pdf_sha256=data['source_pdf_sha256'],semantic_formula_accuracy_measured=False,publication_eligible=False)
    output.mkdir(parents=True);write(output/'evaluation.json',result);return result


def configure(parser):
    sub=parser.add_subparsers(dest='field_operation',required=True)
    initialization=sub.add_parser('init');initialization.add_argument('--output',type=Path,required=True)
    listing=sub.add_parser('catalog');listing.add_argument('--rules',type=Path,required=True);listing.add_argument('--search')
    extraction=sub.add_parser('extract');extraction.add_argument('--rules',type=Path,required=True);extraction.add_argument('--package',type=Path,required=True);extraction.add_argument('--output',type=Path,required=True);extraction.add_argument('--timeout',type=int,default=30)
    checking=sub.add_parser('verify');checking.add_argument('--bundle',type=Path,required=True);checking.add_argument('--timeout',type=int,default=30)
    searching=sub.add_parser('query');searching.add_argument('--bundle',type=Path,action='append',required=True);searching.add_argument('--field');searching.add_argument('--text');searching.add_argument('--offset',type=int,default=0);searching.add_argument('--limit',type=int,default=50)
    evaluation=sub.add_parser('evaluate');evaluation.add_argument('--bundle',type=Path,required=True);evaluation.add_argument('--references',type=Path,required=True);evaluation.add_argument('--output',type=Path,required=True)
    assertions=sub.add_parser('check',help='test verified extracted fields using declarative JSON checks');assertions.add_argument('--bundle',type=Path,required=True);assertions.add_argument('--checks',type=Path,required=True);assertions.add_argument('--output',type=Path,required=True);assertions.add_argument('--timeout',type=int,default=30)
    for child in (initialization,listing,extraction,checking,searching,evaluation,assertions):child.add_argument('--json',action='store_true')


def execute(args):
    if args.field_operation=='init':
        if args.output.exists():raise ValueError('Rules file already exists; choose a new path')
        template=Path(__file__).with_name('extraction_rules')/'document-v1.json';write(args.output,load(template))
        return dict(output=str(args.output),fields=4,next_step='Edit field IDs, patterns, family scopes and date formats; then run fields extract')
    if args.field_operation=='catalog':return catalog(args.rules,args.search)
    if args.field_operation=='extract':return extract(args.package,args.rules,args.output,args.timeout)
    if args.field_operation=='verify':return verify(args.bundle,args.timeout)
    if args.field_operation=='evaluate':return evaluate(args.bundle,args.references,args.output)
    if args.field_operation=='check':
        from .field_checks import check
        return check(args.bundle,args.checks,args.output,args.timeout)
    return query(args.bundle,args.field,args.text,args.offset,args.limit)


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise SystemExit('Use pdf2data fields')
    request=json.loads(Path(sys.argv[2]).read_text());package=Path(request['package']);rules_path=Path(request['rules'])
    if file_sha256(rules_path)!=request['rules_sha256']:raise ValueError('Rules changed')
    receipt=json.loads((package/'source-receipt.json').read_text());data=source_data(package,receipt['actual_sha256']);rules=load(rules_path)
    if rules.get('fields_schema_version')in (FIELDS_V2,FIELDS_V3):data['source_package_sha256']=file_sha256(package/'l1-package.json')
    write(Path(sys.argv[3]),compute(data,rules,request['rules_sha256'],receipt['declared'].get('family','generic')))
