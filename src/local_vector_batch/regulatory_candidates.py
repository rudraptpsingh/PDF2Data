"""Native, source-bound regulatory observations; no consumer-domain dependency."""
from collections import Counter
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
from .field_rules import SOURCE_FILES,source_data,write
from .hashing import file_sha256
from .jsonl import jsonl_physical_lines

SCHEMA='pdf2data.regulatory-candidates.v1'
PROFILE='pdf2data.regulatory-profile.v1'
GROUPS=('amendments','conditions','values','transitions','references','commencements')


def load_profile(path=None):
    try:return _load_profile(path)
    except re.error as exc:raise ValueError('Invalid regulatory profile regex: '+str(exc))from exc


def _load_profile(path=None):
    path=Path(path)if path else Path(__file__).with_name('regulatory_profile.json')
    if path.stat().st_size>256*1024:raise ValueError('Regulatory profiles must fit within 256 KB')
    p=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(p,dict)or p.get('schema_version')!=PROFILE or not isinstance(p.get('id'),str)or not p['id']or not isinstance(p.get('version'),str)or not p['version']:raise ValueError('Unknown regulatory profile')
    for name in ('operation_pattern','subitem_pattern','reference_pattern','condition_pattern','quantity_pattern','actor_pattern','event_pattern','transition_pattern','commencement_pattern','draft_marker_pattern'):
        if not isinstance(p.get(name),str)or len(p[name])>4000:raise ValueError('Invalid profile pattern: '+name)
        re.compile(p[name],re.I)
    captures={'operation_pattern':{'operation'},'subitem_pattern':{'label'},'quantity_pattern':{'value','unit'},
              'factor_pattern':{'value','basis'},'date_pattern':{'value'},'parent_instrument_pattern':{'instrument','alias'},
              'title_pattern':{'title'},'transition_cohort_pattern':{'cohort'}}
    for name,required in captures.items():
        if name not in p:continue
        if not isinstance(p[name],str)or len(p[name])>4000:raise ValueError('Invalid profile pattern: '+name)
        if not required<=re.compile(p[name],re.I).groupindex.keys():raise ValueError('Missing named captures in '+name)
    if not isinstance(p.get('boolean_patterns',[]),list)or len(p.get('boolean_patterns',[]))>32:raise ValueError('Invalid Boolean pattern budget')
    for pattern in p.get('boolean_patterns',[]):
        if not isinstance(pattern,str)or len(pattern)>4000:raise ValueError('Invalid Boolean pattern')
        compiled=re.compile(pattern,re.I)
        if not {'left','operator','right'}<=compiled.groupindex.keys():raise ValueError('Boolean patterns require source left/operator/right captures')
    if not isinstance(p.get('comparisons',[]),list)or len(p.get('comparisons',[]))>64:raise ValueError('Invalid comparison pattern budget')
    for rule in p.get('comparisons',[]):
        if not isinstance(rule,dict)or not isinstance(rule.get('pattern'),str)or len(rule['pattern'])>4000 or not isinstance(rule.get('candidate'),str):raise ValueError('Invalid comparison rule')
        re.compile(rule['pattern'],re.I)
    if not isinstance(p.get('operation_names'),dict)or any(v not in ('insert','add','substitute','delete','unknown')for v in p['operation_names'].values()):raise ValueError('Invalid operation names')
    if type(p.get('max_records'))is not int or not 1<=p['max_records']<=100000:raise ValueError('Invalid record budget')
    return p,path


def init_profile(output):
    output=Path(output)
    with output.open('xb')as stream:stream.write(Path(__file__).with_name('regulatory_profile.json').read_bytes())
    return dict(schema_version=PROFILE,output=str(output.resolve()),profile_sha256=file_sha256(output))


class Unit:
    """Map contiguous projection offsets to one or more unchanged native spans."""
    def __init__(self,unit,pages,index,children):
        self.native=unit;self.pages=pages;self.index=index;self.text='';self.parts=[]
        self.child_ids=[c['id']for c in children if c.get('parent_provision_version_id')==unit['id']]
        for s in unit['spans']:
            page=pages[s['page']];a=s['char_start'];b=a+s['char_length']
            if page['text_sha256']!=s['page_text_sha256']or page['text'][a:b]!=s['exact_quote']:raise ValueError('Native unit span changed')
            lo=len(self.text);self.text+=s['exact_quote'];self.parts.append((lo,len(self.text),s))

    def evidence(self,a,b):
        if not 0<=a<b<=len(self.text):raise ValueError('Candidate source interval outside native unit')
        result=[]
        for lo,hi,s in self.parts:
            x,y=max(a,lo),min(b,hi)
            if x<y:result.append(span(self.pages[s['page']],s['char_start']+x-lo,s['char_start']+y-lo))
        if ''.join(s['exact_quote']for s in result)!=self.text[a:b]:raise ValueError('Candidate lost source characters')
        return result

    def part(self,a,b,**extra):
        return dict(literal=self.text[a:b],source_spans=self.evidence(a,b),**extra)


def quote_regions(text):
    """Nested curly quotes; incomplete quotes never become complete payloads."""
    stack=[];out=[];straight=None
    for i,ch in enumerate(text):
        if ch=='“':stack.append(i)
        elif ch=='”'and stack:
            a=stack.pop()
            if not stack:out.append((a,i+1,True))
        elif ch=='"'and not stack:
            if straight is None:straight=i
            else:out.append((straight,i+1,True));straight=None
    out.extend((a,len(text),False)for a in stack[:1])
    if straight is not None:out.append((straight,len(text),False))
    return sorted(out)


def frames(unit,profile):
    matches=list(re.finditer(profile['subitem_pattern'],unit.text))
    if not matches:return [(0,len(unit.text),None)]
    return [(m.start(),matches[i+1].start()if i+1<len(matches)else len(unit.text),m.group('label'))for i,m in enumerate(matches)]


def fragment(unit,lo,quote):
    a,b,complete=quote
    if not complete:return None
    return unit.part(lo+a+1,lo+b-1,quote_state='closed_literal_not_semantic_approval')if b-a>2 else None


def build(data,profile,profile_sha,source_package_sha):
    pages={p['page']:p for p in data['pages']};units=[Unit(u,pages,i,data['structure']['children'])for i,u in enumerate(data['structure']['units'])]
    records=[];coverage=[];quality=data.get('quality',{})
    patterns={name:re.compile(profile[name+'_pattern'],re.I)for name in ('operation','reference','condition','quantity','actor','event','transition','commencement','draft_marker')}
    role=[];parents=[];document_observations=[]
    for unit in units[:1]:
        role=[unit.part(m.start(),m.end())for m in patterns['draft_marker'].finditer(unit.text)]
        for m in re.finditer(profile.get('parent_instrument_pattern',r'(?!)'),unit.text,re.I):
            parents.append(dict(native_unit_id=unit.native['id'],instrument=unit.part(m.start('instrument'),m.end('instrument')),
                                alias_declaration=unit.part(m.start('alias'),m.end('alias')),target_id=None,
                                resolution='declared_document_alias_candidate_external_version_unresolved'))
    for unit in units:
        for m in re.finditer(profile.get('title_pattern',r'(?!)'),unit.text,re.I):
            document_observations.append(dict(kind='title_and_proposal_version_candidate',native_unit_id=unit.native['id'],
                                             evidence=unit.part(m.start('title'),m.end('title')),reviewed=False))

    def emit(group,u,a,b,**body):
        evidence=u.evidence(a,b)
        assertion=body.pop('assertion_role_candidate','proposed'if role and group in ('amendments','commencements')else'unknown')
        record=dict(id='regobs-'+digest([data['source_pdf_sha256'],source_package_sha,profile_sha,group,u.native['id'],a,b,body])[:28],
            group=group,source_pdf_sha256=data['source_pdf_sha256'],source_package_sha256=source_package_sha,
            native_unit_id=u.native['id'],native_unit_label=u.native['label'],native_unit_pointer='/units/'+str(u.index),native_child_ids=u.child_ids,
            literal=u.text[a:b],source_spans=evidence,profile_id=profile['id'],profile_version=profile['version'],profile_sha256=profile_sha,
            status='candidate',assertion_role=dict(candidate=assertion,evidence=role if assertion=='proposed'else[],reviewed=False),
            source_document_role=dict(candidate='draft'if role else'unknown',evidence=role,reviewed=False),
            required_native_unit_ids=list(dict.fromkeys([u.native['id'],*(p['native_unit_id']for p in parents)])),
            native_declared_dependencies=u.native.get('dependencies',[]),
            source_quality_observations=[q for q in quality.get('results',[])if q['page']in {s['page']for s in evidence}],
            semantic_review_required=True,source_quality_approval_granted=False,legal_answer_eligible=False,calculation_allowed=False,publication_eligible=False,**body)
        records.append(record)
        if len(records)>profile['max_records']:raise ValueError('Record budget exceeded; output is not clipped')
        return record

    def references(u,a,b):
        return [u.part(a+m.start(),a+m.end(),target_id=None,resolution='unresolved',role_candidate='provision_or_instrument_mention')for m in patterns['reference'].finditer(u.text[a:b])]

    def quantities(u,a,b):
        result=[]
        for m in patterns['quantity'].finditer(u.text[a:b]):
            x,y=a+m.start(),a+m.end();raw=m.group('value');typed=None;issues=[]
            from .formula_observations import stated_number
            normalized=profile.get('word_numbers',{}).get(raw.casefold(),raw)
            try:
                n=stated_number(normalized);typed=dict(numerator=str(n.numerator),denominator=str(n.denominator))
            except (ValueError,ArithmeticError):issues.append('unsupported_numeric_literal')
            nearby=u.text[max(a,x-45):min(b,y+45)];comparisons=[]
            for rule in profile.get('comparisons',[]):
                for c in re.finditer(rule['pattern'],nearby,re.I):
                    offset=max(a,x-45)+c.start();comparisons.append(u.part(offset,offset+len(c.group()),candidate=rule['candidate'],ownership='nearby_not_proven'))
            before=u.text[max(a,x-12):x];currency=re.search(r'(?:Rs\.?|₹)\s*$',before,re.I)
            anchored=[]
            for rule in profile.get('comparisons',[]):
                prefix=u.text[a:x];suffix=u.text[y:b]
                c=re.search(r'(?:'+rule['pattern']+r')\s*$',prefix,re.I)
                if c:anchored.append(u.part(a+c.start(),a+c.end(),candidate=rule['candidate'],ownership='adjacent_printed_operator_candidate'))
                c=re.match(r'\s*(?:'+rule['pattern']+r')',suffix,re.I)
                if c:anchored.append(u.part(y+c.start(),y+c.end(),candidate=rule['candidate'],ownership='adjacent_printed_operator_candidate'))
            item=u.part(x,y,raw_value=u.part(a+m.start('value'),a+m.end('value')),typed_candidate=typed,
                        unit=u.part(a+m.start('unit'),a+m.end('unit')),comparison_candidates=comparisons,
                        adjacent_comparison_candidates=anchored,
                        currency_literal=currency.group().strip()if currency else None,currency_code_candidate=None,
                        currency_source=u.part(max(a,x-12)+currency.start(),max(a,x-12)+currency.end())if currency else None,
                        scale_literal=m.groupdict().get('scale'),scale_candidate=profile.get('scale_candidates',{}).get((m.groupdict().get('scale')or'').lower()),
                        scale_source=u.part(a+m.start('scale'),a+m.end('scale'))if m.groupdict().get('scale')else None,
                        validation_issues=issues,actor_candidates=[u.part(a+t.start(),a+t.end())for t in patterns['actor'].finditer(u.text[a:b])],
                        trigger_candidates=[u.part(a+t.start(),a+t.end(),role='unresolved_event')for t in patterns['event'].finditer(u.text[a:b])],
                        ownership='same_source_frame_candidate',old_new_role='unresolved',aggregation='not_inferred',absolute_date=None)
            item['kind']='rate'if m.group('unit').startswith('/')else'duration'if re.fullmatch(r'years?|months?|weeks?|days?',m.group('unit'),re.I)else'ratio'if m.group('unit')=='%'else'quantity'
            result.append(item)
        for m in re.finditer(profile.get('factor_pattern',r'(?!)'),u.text[a:b],re.I):
            raw=m.group('value');n=profile.get('word_numbers',{}).get(raw.casefold())
            result.append(u.part(a+m.start(),a+m.end(),kind='factor',raw_value=u.part(a+m.start('value'),a+m.end('value')),
                                 typed_candidate=dict(numerator=n,denominator='1')if n else None,basis=u.part(a+m.start('basis'),a+m.end('basis')),
                                 unit=None,comparison_candidates=[],adjacent_comparison_candidates=[],ownership='literal_factor_basis_candidate_not_dimensional_validation',absolute_date=None))
        for m in re.finditer(profile.get('date_pattern',r'(?!)'),u.text[a:b]):
            raw=m.group('value');normalized=None
            try:normalized=datetime.strptime(raw,profile['date_format']).date().isoformat()
            except (ValueError,KeyError):pass
            result.append(u.part(a+m.start(),a+m.end(),kind='date',raw_value=u.part(a+m.start('value'),a+m.end('value')),
                                 typed_candidate=normalized,unit=None,comparison_candidates=[],adjacent_comparison_candidates=[],
                                 date_role='unresolved',ownership='printed_date_only_not_effectiveness_or_deadline',absolute_date=None,
                                 validation_issues=[]if normalized else['date_normalization_not_established']))
        return result

    for u in units:
        start_count=len(records);unparsed=[]
        source_frames=frames(u,profile)
        heading_end=source_frames[0][0]
        heading_refs=references(u,0,heading_end)if heading_end else[]
        for m in patterns['reference'].finditer(u.text[:heading_end]):
            emit('references',u,m.start(),m.end(),subitem_label=None,reference_kind='unit_heading_mention',target_native_id=None,resolution='unresolved',legal_relation=None)
        if re.search(r'[\u0900-\u097f]',u.text)and 'hi'not in profile.get('language_scope',[]):
            unparsed.extend(dict(subitem=None,group=g,reasons=['source_script_outside_profile_scope'])for g in GROUPS)
        for a,b,label in source_frames:
            text=u.text[a:b];quotes=quote_regions(text)
            ops=[m for m in patterns['operation'].finditer(text)if not any(lo<m.start()<hi and complete for lo,hi,complete in quotes)]
            refs=references(u,a,b)
            for m in ops:
                op=profile['operation_names'].get(m.group('operation').lower(),'unknown')
                before=[q for q in quotes if q[1]<=m.start()and q[2]];after=[q for q in quotes if q[0]>=m.end()and q[2]]
                old=new=None;limitations=[];position=None
                if op=='substitute':
                    old=fragment(u,a,before[-1])if before else None
                    new=fragment(u,a,after[0])if after else None
                elif op in ('insert','add'):
                    # Words immediately preceding the cue are the inserted text;
                    # a later quoted phrase is only the location anchor.
                    prefix=text[:m.start()]
                    words_frame=bool(re.match(r'\s*\d+\.\d+\.\s*The\s+words?\b',prefix,re.I))
                    new=fragment(u,a,before[-1])if words_frame and before else fragment(u,a,after[0])if after else None
                    position='after'if re.match(r'\s+after\b',text[m.end():],re.I)else'before'if re.match(r'\s+before\b',text[m.end():],re.I)else'end_or_unresolved'
                elif op=='delete':old=fragment(u,a,before[-1])if before else None
                if op in ('substitute','delete')and old is None:limitations.append('old_text_not_printed_or_quote_unresolved')
                if op in ('substitute','insert','add')and new is None:limitations.append('replacement_text_not_printed_or_quote_unresolved')
                if any(not complete for _,_,complete in quotes):limitations.append('unbalanced_source_quote_not_repaired')
                # Target cues outside quoted payloads/anchors are retained
                # separately from references inside the proposed replacement.
                target=[]
                for r in patterns['reference'].finditer(text):
                    if any(lo<=r.start()<hi for lo,hi,complete in quotes if complete):continue
                    prefix=text[:r.start()];relation=re.search(r'\b(after|before|under|in|of)\s+(?:the\s+)?$',prefix,re.I)
                    reference_role='unclassified_operation_frame_reference'
                    plain_prefix=text[:m.start()]
                    for lo,hi,complete in reversed(quotes):
                        if complete and lo<len(plain_prefix):plain_prefix=plain_prefix[:lo]+' '*(min(hi,len(plain_prefix))-lo)+plain_prefix[min(hi,len(plain_prefix)):]
                    if op in ('insert','add')and r.start()<m.start()and re.search(r'\bnew\b',plain_prefix,re.I):reference_role='new_address_candidate'
                    elif relation and relation.group(1).lower()in ('after','before'):reference_role='location_anchor_candidate'
                    elif relation:reference_role='container_reference_candidate'
                    elif r.start()<m.start():reference_role='affected_reference_candidate'
                    target.append(u.part(a+r.start(),a+r.end(),target_id=None,resolution='unresolved',role_candidate=reference_role,
                                         relation_evidence=u.part(a+relation.start(),a+relation.end())if relation else None))
                head_regs={re.sub(r'\s+','',r['literal']).casefold()for r in heading_refs if re.match(r'Regulations?\b',r['literal'],re.I)}
                target_regs={re.sub(r'\s+','',r['literal']).casefold()for r in target if re.match(r'Regulations?\b',r['literal'],re.I)}
                if head_regs and target_regs and not head_regs&target_regs:limitations.append('heading_and_operation_references_differ_not_reconciled')
                emit('amendments',u,a,b,subitem_label=label,operation=op,operation_evidence=u.part(a+m.start(),a+m.end()),
                     target_reference_candidates=target,heading_reference_candidates=heading_refs,target_instrument_candidates=parents,target_instrument_resolution='unresolved',old_fragment=old,new_fragment=new,
                     location_relation_candidate=position,limitations=limitations,operation_ownership='lexical_amendment_frame_not_reviewed',
                     dependency_requirements=[dict(kind='target_instrument_or_provision',reference=r,resolution='unresolved',reason='principal_version_and_affected_provision_require_readback')for r in target if r['role_candidate']!='new_address_candidate'])
                if limitations:unparsed.append(dict(subitem=label,group='amendments',reasons=limitations))

            values=quantities(u,a,b)
            for q in values:
                # Bind value roles only by exact containment in an observed
                # old/new quote, never from the magnitude or a nearby label.
                roles=[]
                for record in records[start_count:]:
                    if record['group']!='amendments':continue
                    for role_name,key in [('earlier_text','old_fragment'),('replacement_text','new_fragment')]:
                        part=record.get(key)
                        if part and all(any(t['page']==s['page']and t['char_start']<=s['char_start']and s['char_start']+s['char_length']<=t['char_start']+t['char_length']for t in part['source_spans'])for s in q['source_spans']):roles.append(dict(candidate_id=record['id'],role=role_name,basis='exact_quote_containment'))
                kinds={r['role']for r in roles}
                assertion='proposed'if role and kinds=={'replacement_text'}else'quoted_earlier_text'if kinds=={'earlier_text'}else'unknown'
                emit('values',u,a,b,subitem_label=label,value=q,amendment_value_roles=roles,association_state='candidate_not_semantic_approval',assertion_role_candidate=assertion)

            conditions=[]
            for pattern in profile.get('boolean_patterns',[]):
                for m in re.finditer(pattern,text,re.I):
                    parts=[dict(kind='atom',source=u.part(a+m.start(name),a+m.end(name)),subject_candidate=None,property_candidate=None,comparison=None,operand=None,ownership='unresolved')for name in ('left','right')]
                    tree=dict(kind='all'if m.group('operator').lower()=='and'else'any',source=u.part(a+m.start(),a+m.end()),
                              connector=u.part(a+m.start('operator'),a+m.end('operator')),children=parts,semantic_state='source_enumeration_candidate_not_applicability_approval')
                    row=emit('conditions',u,a+m.start(),a+m.end(),subitem_label=label,introducer=tree['connector'],predicate_tree=tree,
                             operand_candidates=[],reference_candidates=refs,association_state='explicit_profile_matched_boolean_join_candidate')
                    conditions.append(row['id'])
            for i,m in enumerate(patterns['condition'].finditer(text)):
                end=min([z for z in [text.find(';',m.end()),text.find('.”',m.end())]if z>=0]or[len(text)])
                excluded=m.group().lower()in ('excluding','other than')
                # Any condition introduced inside a parenthesis stops at its
                # closing delimiter. Otherwise an inline "if" qualifier can
                # absorb an independent deadline following the parenthesis.
                # This is a lexical boundary, not proof of Boolean ownership.
                depth=text[:m.start()].count('(')-text[:m.start()].count(')')
                current=depth
                if depth>0:
                    for j in range(m.end(),end):
                        current+=int(text[j]=='(')-int(text[j]==')')
                        if current<depth:end=j;break
                end=min(end,len(text));literal=text[m.start():end]
                if not literal:continue
                child=dict(kind='atom',source=u.part(a+m.start(),a+end),subject_candidate=None,property_candidate=None,comparison=None,operand=None,ownership='unresolved')
                if excluded:
                    # A source exclusion is observed; its applicable subject
                    # and parent rule remain unresolved, not an asserted NOT.
                    child=dict(kind='not',source=u.part(a+m.start(),a+end),child=child,semantic_state='candidate_exclusion_scope_unresolved')
                contained=[]
                for q in values:
                    if all(any(s['page']==t['page']and t['char_start']<=s['char_start']and s['char_start']+s['char_length']<=t['char_start']+t['char_length']for t in u.evidence(a+m.start(),a+end))for s in q['source_spans']):
                        contained.append(q)
                atoms=[dict(kind='atom',source=dict(literal=q['literal'],source_spans=q['source_spans']),subject_candidate=None,
                            property_candidate=q.get('kind'),comparison=q['adjacent_comparison_candidates'][0]if len(q['adjacent_comparison_candidates'])==1 else None,
                            operand=dict(kind=q.get('kind'),literal=q['raw_value'],typed_candidate=q.get('typed_candidate'),unit=q.get('unit')),ownership='candidate_operand_scope_unresolved')for q in contained]
                tree=dict(kind='unknown',reason='natural_language_scope_and_boolean_ownership_not_proven',source=u.part(a+m.start(),a+end),observed_components=[child,*atoms])
                row=emit('conditions',u,a+m.start(),a+end,subitem_label=label,introducer=u.part(a+m.start(),a+m.end()),predicate_tree=tree,
                         operand_candidates=contained,
                         reference_candidates=refs,association_state='source_frame_only_not_complete_condition_scope')
                conditions.append(row['id'])
            if conditions:unparsed.append(dict(subitem=label,group='conditions',reasons=['condition_boolean_scope_requires_review']))

            for ref in patterns['reference'].finditer(text):
                raw=ref.group();kind='document_number_mention'if re.match(r'No\.',raw,re.I)else'instrument_edition_literal'if re.match(r'Regulations?,\s*\d{4}',raw,re.I)else'attachment_mention'if re.match(r'Annexure',raw,re.I)else'principal_alias_mention'if re.search(r'Principal',raw,re.I)else'provision_mention'
                emit('references',u,a+ref.start(),a+ref.end(),subitem_label=label,reference_kind=kind,target_native_id=None,resolution='unresolved',legal_relation=None)
            for m in patterns['commencement'].finditer(text):
                emit('commencements',u,a+m.start(),a+m.end(),subitem_label=label,event_candidate='publication'if re.search(r'publication',m.group(),re.I)else'unknown',
                     proposed_trigger=u.part(a+m.start(),a+m.end()),actual_publication_date=None,effectiveness_proved=False)
            if patterns['transition'].search(text):
                consequence=re.search(r'\bshall\s+(?!be\s+(?:added|inserted|substituted|deleted))[^”]{1,1200}',text,re.I)
                cohort=re.search(profile.get('transition_cohort_pattern',r'(?!)'),text,re.I)
                cohort_source=u.part(a+cohort.start('cohort'),a+cohort.end('cohort'))if cohort else None
                proposed=role and any(r['group']=='amendments'and r.get('new_fragment')and patterns['transition'].search(r['new_fragment']['literal'])for r in records[start_count:])
                emit('transitions',u,a,b,subitem_label=label,condition_candidate_ids=conditions,
                     event_candidates=[u.part(a+t.start(),a+t.end(),kind_candidate='unresolved_event')for t in patterns['event'].finditer(text)],
                     period_candidates=values,qualified_consequence=u.part(a+consequence.start(),a+consequence.end())if consequence else None,
                     cohort_predicate=dict(kind='unknown',reason='cohort_interpretation_requires_review',source=cohort_source)if cohort_source else None,
                     cohort_resolution='unresolved',actual_deadline=None,association_state='same_transition_frame_candidate',
                     deadline_window_candidates=[q for q in values if q.get('kind')=='duration'and any(c['candidate']=='within_window'for c in q['adjacent_comparison_candidates'])],
                     assertion_role_candidate='proposed'if proposed else'unknown')
        counts=Counter(r['group']for r in records[start_count:])
        coverage.append(dict(native_unit_id=u.native['id'],native_unit_pointer='/units/'+str(u.index),label=u.native['label'],native_child_ids=u.child_ids,
                             source_spans=u.native['spans'],groups={g:dict(status='review_gap'if any(x['group']==g for x in unparsed)else'extracted'if counts[g]else'not_detected',candidates=counts[g],absence_proven=False)for g in GROUPS},
                             review_gaps=unparsed,native_dependencies=u.native.get('dependencies',[]),semantic_completeness='not_established'))
    from .table_contexts import iter_table_contexts
    table_context={t['table_id']:t for t in iter_table_contexts(data)}
    tables=[dict(native_table_id=t['id'],native_table_version_id=t.get('table_version_id'),source_pointer='/'+str(i),source_artifact='extraction/tables.json',
                 physical_page=t['page'],page_text_sha256=pages[t['page']]['text_sha256'],raw_table=t,coverage_status='review_gap',
                 native_table_context=table_context.get(t['id']),
                 reason='entire_native_table_retained_header_footnote_and_legal_ownership_unresolved',native_unit_candidates=[u.native['id']for u in units if any(s['page']==t['page']for s in u.native['spans'])],
                 association='page_cooccurrence_only_not_table_ownership',semantic_review_required=True,calculation_allowed=False)for i,t in enumerate(data['tables'])]
    return dict(schema_version=SCHEMA,source_pdf_sha256=data['source_pdf_sha256'],source_package_sha256=source_package_sha,profile_id=profile['id'],profile_version=profile['version'],profile_sha256=profile_sha,
                records=records,document_observations=document_observations,parent_instrument_candidates=parents,
                coverage=dict(units=coverage,tables=tables,physical_pages=[dict(page=p['page'],page_text_sha256=p['text_sha256'])for p in data['pages']],
                    native_units_accounted=len(coverage),native_tables_accounted=len(tables),semantic_completeness='not_established'),
                native_quality=quality,native_readiness=data.get('readiness'),source_quality_approval_granted=False,legal_answer_eligible=False,publication_eligible=False,
                limitations=['Bounded lexical/quote/frame extraction is not semantic or legal completeness.','Native dependencies are retained, not inferred from an empty list.','Unknown references, branches, source formatting and quality remain unresolved.'])


SOURCE_NAMES=(*SOURCE_FILES,'readiness.json','source-receipt.json','l1-package.json','original.pdf','extraction/evidence-package.json')
IMPLEMENTATION_NAMES=('regulatory_candidates.py','document_blocks.py','field_rules.py','formula_observations.py','legal_l1.py','table_contexts.py','hashing.py','jsonl.py')


def implementation():return {n:file_sha256(Path(__file__).with_name(n))for n in IMPLEMENTATION_NAMES}


def _build_package(package,profile_path):
    receipt=json.loads((package/'source-receipt.json').read_text(encoding='utf-8'))
    profile,path=load_profile(profile_path);data=source_data(package,receipt['actual_sha256'])
    data['readiness']=json.loads((package/'readiness.json').read_text(encoding='utf-8'))
    result=build(data,profile,file_sha256(path),file_sha256(package/'l1-package.json'))
    result['native_source_id']=receipt['declared']['id'];result['native_source_receipt']=receipt
    result['producer_implementation']=implementation()
    return result


def _derive(package,profile,timeout):
    if not 1<=timeout<=600:raise ValueError('Use a time budget 1..600 seconds')
    with tempfile.TemporaryDirectory(prefix='pdf2data-regulatory-')as temp:
        request=Path(temp)/'request.json';response=Path(temp)/'response.json';write(request,dict(package=str(package),profile=str(profile)))
        try:
            ran=subprocess.run([sys.executable,'-m','local_vector_batch.regulatory_candidates','--worker',str(request),str(response)],
                               env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])},capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired:raise ValueError('Regulatory candidate budget exceeded; no partial bundle published')from None
        if ran.returncode or not response.exists():raise ValueError('Regulatory candidate worker failed: '+ran.stderr[-1000:])
        if response.stat().st_size>128*1024*1024:raise ValueError('Candidate bundle exceeds 128 MB; narrow the profile')
        return json.loads(response.read_text(encoding='utf-8'))


def extract(package,output,profile_path=None,timeout=30):
    from .legal_l1 import verify_document
    package=Path(package).resolve();output=Path(output).resolve();profile,path=load_profile(profile_path);path=path.resolve()
    if output.exists()or output.is_relative_to(package)or package.is_relative_to(output):raise ValueError('Use a new bundle outside the immutable source package')
    if not 1<=timeout<=600:raise ValueError('Use a time budget 1..600 seconds')
    checked=verify_document(package);pins={n:file_sha256(package/n)for n in SOURCE_NAMES};profile_sha=file_sha256(path);impl=implementation()
    result=_derive(package,path,timeout)
    if any(file_sha256(package/n)!=v for n,v in pins.items())or file_sha256(path)!=profile_sha:raise ValueError('Source or profile changed during extraction')
    if result['profile_sha256']!=profile_sha or result['source_package_sha256']!=pins['l1-package.json']:raise ValueError('Candidate result used a different source/profile')
    if implementation()!=impl or result['producer_implementation']!=impl:raise ValueError('Producer implementation changed during extraction')
    output.mkdir(parents=True);(output/'profile.json').write_bytes(path.read_bytes());write(output/'candidates.json',result)
    write(output/'manifest.json',dict(schema_version='pdf2data.regulatory-bundle.v1',source_package=str(package),source_files=pins,
          files={n:file_sha256(output/n)for n in ('profile.json','candidates.json')},producer_implementation=implementation(),
          verification=checked,source_quality_approval_granted=False,publication_eligible=False))
    return dict(schema_version=SCHEMA,output=str(output),records=len(result['records']),record_counts=dict(Counter(r['group']for r in result['records'])),
                native_units_accounted=len(result['coverage']['units']),native_tables_accounted=len(result['coverage']['tables']),
                semantic_completeness='not_established',source_quality_changed=False,publication_eligible=False)


def verify(bundle,timeout=30):
    from .legal_l1 import verify_document
    bundle=Path(bundle).resolve();manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!='pdf2data.regulatory-bundle.v1':raise ValueError('Unknown regulatory bundle schema')
    if manifest.get('source_quality_approval_granted')is not False or manifest.get('publication_eligible')is not False:raise ValueError('Candidate manifest cannot grant approval')
    if set(manifest['files'])!={'profile.json','candidates.json'}:raise ValueError('Unexpected candidate artifacts')
    if any(file_sha256(bundle/n)!=v for n,v in manifest['files'].items()):raise ValueError('Candidate artifact changed')
    if manifest['producer_implementation']!=implementation():raise ValueError('Use the captured producer implementation, or extract a new bundle')
    package=Path(manifest['source_package']);checked=verify_document(package)
    if manifest.get('verification')!=checked:raise ValueError('Candidate source verification receipt changed')
    if set(manifest['source_files'])!=set(SOURCE_NAMES)or any(file_sha256(package/n)!=v for n,v in manifest['source_files'].items()):raise ValueError('Candidate source version changed')
    result=json.loads((bundle/'candidates.json').read_text(encoding='utf-8'))
    if result!=_derive(package,bundle/'profile.json',timeout):raise ValueError('Regulatory candidate content or associations differ from fresh source derivation')
    return result


def read(bundle,candidate_id=None,unit_id=None,group=None):
    value=verify(bundle);records=value['records']
    if candidate_id:records=[r for r in records if r['id']==candidate_id]
    if unit_id:records=[r for r in records if r['native_unit_id']==unit_id]
    if group:records=[r for r in records if r['group']==group]
    if candidate_id and len(records)!=1:raise ValueError('Candidate ID does not resolve in this exact bundle/scope')
    return dict(schema_version=SCHEMA,source_pdf_sha256=value['source_pdf_sha256'],source_package_sha256=value['source_package_sha256'],records=records,
                coverage=value['coverage'],semantic_completeness='not_established',source_quality_approval_granted=False,publication_eligible=False)


def _pointer(value,pointer):
    if pointer=='':return value
    if not isinstance(pointer,str)or not pointer.startswith('/'):raise ValueError('Use an explicit JSON pointer')
    for token in pointer[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        value=value[int(token)]if isinstance(value,list)else value[token]
    return value


def evaluate(bundle,references,output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new evaluation directory')
    bundle=Path(bundle).resolve()
    if output.is_relative_to(bundle)or bundle.is_relative_to(output):raise ValueError('Use an evaluation directory outside the immutable candidate bundle')
    value=verify(bundle);refs=json.loads(Path(references).read_text(encoding='utf-8'))
    for key in ('source_pdf_sha256','source_package_sha256','profile_sha256'):
        if refs.get(key)!=value[key]:raise ValueError('Reference snapshot differs: '+key)
    if refs.get('reference_scope')not in ('agent_development','human_source_review')or not refs.get('reviewer'):raise ValueError('Declare reference provenance and reviewer')
    if not isinstance(refs.get('cases'),list)or not 1<=len(refs['cases'])<=10000:raise ValueError('Provide 1..10000 source-bound evaluation cases')
    ids=set();results=[]
    for case in refs['cases']:
        if not isinstance(case.get('id'),str)or not case['id']:raise ValueError('Evaluation cases need nonempty string IDs')
        if case['id']in ids:raise ValueError('Duplicate evaluation case')
        ids.add(case['id']);errors=[]
        if case.get('scope')=='bundle':
            if not case.get('checks'):raise ValueError('Bundle evaluation cases need explicit expectations')
            target=value
        else:
            if type(case.get('expected_count'))is not int or case['expected_count']<0:raise ValueError('Declare an integer expected candidate count')
            selector=case.get('selector',{})
            if not selector or not set(selector)<={'group','subitem_label','native_unit_id','native_unit_label','operation'}:raise ValueError('Use explicit native candidate selectors')
            target=[r for r in value['records']if all(r.get(k)==v for k,v in selector.items())]
            if len(target)!=case['expected_count']:errors.append(dict(reason='candidate_count',expected=case['expected_count'],actual=len(target)))
        for check in case.get('checks',[]):
            if not isinstance(check,dict)or set(check)-{'pointer','equals','contains'}or ('equals'in check)==('contains'in check):raise ValueError('Each evaluation check needs a pointer and exactly one equals/contains expectation')
            if not isinstance(check.get('pointer'),str)or (check['pointer']and not check['pointer'].startswith('/')):raise ValueError('Use an explicit JSON pointer')
            if 'contains'in check and (not isinstance(check['contains'],str)or not check['contains']):raise ValueError('Contains expectations must be nonempty strings')
            try:actual=_pointer(target,check['pointer'])
            except (KeyError,IndexError,ValueError,TypeError):errors.append(dict(reason='missing_pointer',pointer=check['pointer']));continue
            if 'equals'in check and actual!=check['equals']:errors.append(dict(reason='value_mismatch',pointer=check['pointer'],expected=check['equals'],actual=actual))
            if 'contains'in check and (not isinstance(actual,str)or check['contains']not in actual):errors.append(dict(reason='literal_fragment_missing',pointer=check['pointer'],expected=check['contains']))
        results.append(dict(id=case['id'],passed=not errors,errors=errors))
    if not results:raise ValueError('Provide at least one source-bound evaluation case')
    result=dict(schema_version='pdf2data.regulatory-evaluation.v1',source_pdf_sha256=value['source_pdf_sha256'],source_package_sha256=value['source_package_sha256'],profile_sha256=value['profile_sha256'],
                references_sha256=file_sha256(Path(references)),reference_scope=refs['reference_scope'],reviewer=refs['reviewer'],cases=len(results),passed=sum(r['passed']for r in results),failed=sum(not r['passed']for r in results),
                results=results,independent_corpus_accuracy_measured=False,semantic_completeness='not_established',source_quality_changed=False,publication_eligible=False)
    output.mkdir(parents=True);write(output/'evaluation.json',result);return result


def configure(parser):
    sub=parser.add_subparsers(dest='regulatory_operation',required=True)
    initializing=sub.add_parser('init');initializing.add_argument('--output',type=Path,required=True)
    creating=sub.add_parser('extract');creating.add_argument('--package',type=Path,required=True);creating.add_argument('--output',type=Path,required=True);creating.add_argument('--profile',type=Path);creating.add_argument('--timeout',type=int,default=30)
    validating=sub.add_parser('verify');validating.add_argument('--bundle',type=Path,required=True);validating.add_argument('--timeout',type=int,default=30)
    reading=sub.add_parser('read');reading.add_argument('--bundle',type=Path,required=True);reading.add_argument('--candidate-id');reading.add_argument('--unit-id');reading.add_argument('--group',choices=GROUPS)
    evaluation=sub.add_parser('evaluate');evaluation.add_argument('--bundle',type=Path,required=True);evaluation.add_argument('--references',type=Path,required=True);evaluation.add_argument('--output',type=Path,required=True)
    for p in (initializing,creating,validating,reading,evaluation):p.add_argument('--json',action='store_true')


def execute(args):
    if args.regulatory_operation=='init':return init_profile(args.output)
    if args.regulatory_operation=='extract':return extract(args.package,args.output,args.profile,args.timeout)
    if args.regulatory_operation=='read':return read(args.bundle,args.candidate_id,args.unit_id,args.group)
    if args.regulatory_operation=='evaluate':return evaluate(args.bundle,args.references,args.output)
    v=verify(args.bundle,args.timeout)
    return dict(schema_version=SCHEMA,integrity='passed',records=len(v['records']),native_units_accounted=v['coverage']['native_units_accounted'],native_tables_accounted=v['coverage']['native_tables_accounted'],semantic_completeness='not_established',publication_eligible=False)


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise SystemExit('Use pdf2data regulatory')
    request=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    write(Path(sys.argv[3]),_build_package(Path(request['package']),Path(request['profile'])))
