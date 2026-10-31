"""Labelled, source-bound document metadata. Frequency is never proof of a role."""
from datetime import date
from collections import defaultdict
import re
from .document_blocks import compact,digest,glyph_lines,span,union

MONTHS={name:i for i,names in enumerate((('january','jan'),('february','feb'),('march','mar'),('april','apr'),('may',),('june','jun'),('july','jul'),('august','aug'),('september','sep','sept'),('october','oct'),('november','nov'),('december','dec')),1) for name in names}
MONTHS.update({name:i for i,names in enumerate((('जनवरी',),('फरवरी','फ़रवरी'),('मार्च',),('अप्रैल',),('मई',),('जून',),('जुलाई',),('अगस्त',),('सितंबर','सितम्बर'),('अक्टूबर','अक्तूबर'),('नवंबर','नवम्बर'),('दिसंबर','दिसम्बर')),1) for name in names})
DIGITS=str.maketrans('०१२३४५६७८९','0123456789')
MONTH='(?:'+'|'.join(sorted(MONTHS,key=len,reverse=True))+')'
DATE=re.compile(r'(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+'+MONTH+r'\.?\s*,?\s*\d{4}|'+MONTH+r'\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}|\d{1,2}\s*[\u0900-\u0965\u0970-\u097F]{2,20}\s*,?\s*\d{4})(?!\w)',re.I)
REFERENCE=re.compile(r'[A-Z0-9\u0900-\u097F][A-Z0-9\u0900-\u097F/.:_–-]*(?:\(\s*[A-Z0-9\u0900-\u097F]+\s*\)[A-Z0-9\u0900-\u097F/.:_–-]*)*(?:\s+(?:of|वर्ष)\s+\d{4})?',re.I)
# Longer labels take precedence over their embedded generic No./Date labels.
LABELS=[
 ('registration_number',r'REGD\.?\s*NO\.?|रजिस्ट्री\s*(?:संख्या|सं०|सं\.)','reference'),
 ('case_number',r'(?:Petition|Case)\s+No\.?|(?:याचिका|प्रकरण|वाद)\s*(?:संख्या|सं०|सं\.)','reference'),
 ('appeal_number',r'Appeal\s+No\.?|अपील\s*(?:संख्या|सं०|सं\.)','reference'),
 ('application_number',r'(?:IA|I\.?A\.?)\s*(?:\(Diary\)\s*)?No\.?|आवेदन\s*(?:संख्या|सं०|सं\.)','reference'),
 ('document_reference',r'(?:File\s+No\.?|Reference\s*(?:No\.?)?|Ref\.?\s*(?:No\.?)?|Document\s+No\.?|No\.|(?:फाइल|फ़ाइल|पत्र|संदर्भ)\s*(?:संख्या|सं०|सं\.)|संख्या|सं०|सं\.)','reference'),
 ('hearing_date',r'Date\s+of\s+Hearing|सुनवाई\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)','date'),
 ('decision_date',r'Date\s+of\s+(?:Order|Judg(?:e)?ment|Decision)|Pronounced\s+on|(?:आदेश|निर्णय)\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)','date'),
 ('filing_date',r'Date\s+of\s+Filing|दाखिल\s*(?:करने\s*)?(?:की\s*)?(?:तिथि|तारीख)','date'),
 ('publication_date',r'Date\s+of\s+Publication|Published\s+on|प्रकाशन\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)','date'),
 ('effective_date',r'Effective\s+(?:date|from)|Date\s+of\s+(?:Effect|Commencement)|(?:प्रभावी|प्रवर्तन)\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)','date'),
 ('as_of_date',r'as\s+on','date'),
 ('place_date',r'New\s+Delhi,\s*(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*)?(?:the\s*)?|नई\s*दिल्ली,\s*(?:(?:सोमवार|मंगलवार|बुधवार|गुरुवार|शुक्रवार|शनिवार|रविवार),\s*)?','date'),
 ('document_date',r'Dated|Date|दिनांक|तारीख|तिथि','date'),
]
LABEL_RE=re.compile(r'(?<![\w])(?:'+'|'.join('(?P<r'+str(i)+'>'+pattern+')' for i,(_,pattern,_) in enumerate(LABELS))+r')(?![\w])',re.I)


def normalize_date(literal):
    """Never resolve an ambiguous numeric date by assuming the user's locale."""
    value=literal.strip().translate(DIGITS);candidates=[]
    try:
        if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}',value):
            y,m,d=map(int,value.split('-'));return dict(status='unambiguous',iso=date(y,m,d).isoformat())
        numeric=re.fullmatch(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})',value)
        if numeric:
            a,b,y=map(int,numeric.groups())
            for m,d in ((b,a),(a,b)):
                try:
                    iso=date(y,m,d).isoformat()
                    if iso not in candidates:candidates.append(iso)
                except ValueError:pass
            if len(candidates)==1:return dict(status='unambiguous',iso=candidates[0])
            return dict(status='ambiguous_numeric_date' if candidates else 'invalid_calendar_date',iso=None,candidates=candidates)
        cleaned=re.sub(r'(?<=\d)(?:st|nd|rd|th)\b','',value,flags=re.I)
        tokens=re.findall(r'[A-Za-z]+|[\u0900-\u0965\u0970-\u097F]+|\d+',cleaned)
        if tokens[0].isdigit():d,m,y=int(tokens[0]),MONTHS[tokens[1].lower()],int(tokens[2])
        else:m,d,y=MONTHS[tokens[0].lower()],int(tokens[1]),int(tokens[2])
        return dict(status='unambiguous',iso=date(y,m,d).isoformat())
    except KeyError:return dict(status='unrecognized_month',iso=None)
    except (ValueError,IndexError):return dict(status='invalid_calendar_date',iso=None)


def header_end(text,family):
    if family=='record_of_proceedings':pattern=r'^\s*Record of Proceedings\s*$'
    elif family in ('judgment','approval','interim_order'):pattern=r'^\s*(?:In the matter of\s*:?(?:\s*)|ORDER|JUDG(?:E)?MENT|मामले\s*में|आदेश|निर्णय)\s*$'
    elif family=='registry_listing':pattern=r'^\s*Sr\.?\s*No\.'
    else:pattern=r'^\s*(?:\d+\.\s+\S|In exercise of\b|The following amendments\b)'
    match=re.search(pattern,text,re.I|re.M)
    return match.start() if match else len(text)


def field_geometry(page,layout,lo,hi):
    """Locate this exact substring through a unique complete native physical line."""
    if not layout.get('width') or not layout.get('height'):return None
    rows=defaultdict(list)
    for row in glyph_lines(layout):rows[compact(''.join(g['text'] for g in row))].append(row)
    boxes=[]
    for line in re.finditer(r'[^\n]+',page['text']):
        a,b=max(lo,line.start()),min(hi,line.end())
        if a>=b:continue
        matches=rows.get(compact(line.group()),[])
        if len(matches)!=1:return None
        glyph_chars=[g for g in matches[0] for char in g['text'] if not char.isspace()]
        positions=[line.start()+i for i,char in enumerate(line.group()) if not char.isspace()]
        if len(glyph_chars)!=len(positions):return None
        boxes.extend(g['bbox'] for g,pos in zip(glyph_chars,positions) if a<=pos<b)
    box=union(boxes)
    if not box:return None
    w,h=layout['width'],layout['height']
    if not 0<=box[0]<box[2]<=w or not 0<=box[1]<box[3]<=h:return None
    return dict(bbox=box,bbox_normalized=[box[0]/w,box[1]/h,box[2]/w,box[3]/h],
                basis='unique_exact_native_line_substring',side='left' if (box[0]+box[2])/2<w/2 else 'right')


def extract_metadata(data):
    family=data.get('source',{}).get('declared',{}).get('family') or data.get('structure',{}).get('declared_family','generic')
    layouts={p['page']:p for p in data.get('layout',[])};fields=[]
    furniture=data.get('structure',{}).get('regions',[])
    for page in data['pages']:
        text=page['text'];layout=layouts.get(page['page'],{});end=header_end(text,family)
        for label in LABEL_RE.finditer(text):
            role,_,kind=LABELS[int(label.lastgroup[1:])]
            if role=='as_of_date' and family=='registry_listing':role='listing_as_of_date'
            # Generic labels in narrative citations are not document metadata.
            line_start=text.rfind('\n',0,label.start())+1
            prefix=text[line_start:label.start()]
            prior=[f for f in fields if f['source_span']['page']==page['page'] and f['source_span']['char_start']>=line_start and f['source_span']['char_start']+f['source_span']['char_length']<=label.start()]
            if role in ('document_reference','document_date') and prefix.strip() and not prior:continue
            tail=re.match(r'[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?',text[label.end():])
            start=label.end()+tail.end()
            matcher=DATE if kind=='date' else re.compile(r'[^\n]*\d{2,}[^\n]*') if role=='registration_number' else REFERENCE
            match=matcher.match(text,start)
            if not match:continue
            value_end=match.end();literal=match.group()
            if kind=='reference':
                delimiter=re.search(r'\.[–-](?=The\b|In\b|यह|केन्द्रीय)',literal)
                if delimiter:value_end=start+delimiter.start();literal=text[start:value_end]
            if kind=='reference' and not any(c.isdigit() for c in literal):continue
            if role=='document_reference' and literal.isdigit():role='unqualified_number'
            geometry=field_geometry(page,layout,label.start(),value_end)
            scope='body_mention'
            if any((r['kind']=='footer_candidate' or r['kind']=='repeated_header_candidate' and page['page']>1) and r['span']['page']==page['page'] and r['span']['char_start']<=label.start()<r['span']['char_start']+r['span']['char_length'] for r in furniture):scope='page_furniture'
            elif page['page']==1 and label.start()<end:
                if geometry and geometry['bbox_normalized'][1] < (.8 if family=='record_of_proceedings' else .5):scope='document_header'
                elif not geometry and text[:label.start()].count('\n')<35:scope='header_context_unlocated'
            if kind=='date' and role not in ('place_date','listing_as_of_date','as_of_date') and prefix.strip() and not prior:scope='body_mention'
            label_span=span(page,label.start(),label.end());value_span=span(page,start,value_end)
            item=dict(id='field-'+digest([data['source_pdf_sha256'],role,value_span])[:24],role_candidate=role,
                      label=label.group(),value=literal,label_span=label_span,value_span=value_span,
                      source_span=span(page,label.start(),value_end),scope=scope,geometry=geometry,
                      label_language='hi' if re.search(r'[\u0900-\u097F]',label.group()) else 'en',
                      status='candidate',role_verified=False)
            if kind=='date':item['normalization']=normalize_date(literal)
            fields.append(item)
    roles=('document_reference','document_date','decision_date','hearing_date','case_number','appeal_number','application_number','listing_as_of_date','publication_date','effective_date')
    primary={}
    for role in roles:
        matches=[f for f in fields if f['role_candidate']==role and f['scope']=='document_header']
        values={f.get('normalization',{}).get('iso') or f['value'].translate(DIGITS).casefold().strip() for f in matches}
        primary[role]=dict(status='candidate' if len(values)==1 else 'conflicting_candidates' if values else 'not_established',candidate_ids=[f['id'] for f in matches])
    # An order's explicitly labelled decision date may supply a date candidate;
    # a hearing/filing/effective date never supplies the issue date.
    if primary['document_date']['status']=='not_established' and family in ('judgment','approval','interim_order') and primary['decision_date']['status']=='candidate':
        primary['document_date']={**primary['decision_date'],'basis':'explicit_decision_date_label','source_role':'decision_date'}
    correlations=[]
    for i,a in enumerate(fields):
        for b in fields[i+1:]:
            if a['label_language']==b['label_language'] or a['role_candidate']!=b['role_candidate'] or a['source_span']['page']!=b['source_span']['page']:continue
            an,bn=a.get('normalization',{}),b.get('normalization',{})
            relation=None
            if an or bn:
                if an.get('iso') and bn.get('iso') and an['iso']==bn['iso']:relation='same_normalized_date_candidate'
                # Unknown month OCR is kept unresolved, never repaired from English.
            else:
                av=re.findall(r'\d+',a['value'].translate(DIGITS));bv=re.findall(r'\d+',b['value'].translate(DIGITS))
                if len(av)>=2 and av==bv:relation='shared_identifier_numeric_components_candidate'
            if relation:correlations.append(dict(from_field_id=a['id'],to_field_id=b['id'],relation=relation,
                                                 text_equivalence_verified=False,review_required=True))
    result=dict(schema_version='pdf2data.document-metadata.v1',source_pdf_sha256=data['source_pdf_sha256'],declared_document_type=family,
                fields=fields,primary_fields=primary,date_policy='no_assumed_numeric_locale',role_verified=False,publication_eligible=False)
    result['cross_language_correlations']=correlations
    validate_metadata(result,data)
    return result


def validate_metadata(record,data):
    if record['source_pdf_sha256']!=data['source_pdf_sha256'] or record['publication_eligible'] is not False:raise ValueError('invalid metadata identity or approval')
    pages={p['page']:p for p in data['pages']};ids={f['id'] for f in record['fields']}
    if len(ids)!=len(record['fields']):raise ValueError('duplicate metadata field')
    for f in record['fields']:
        for key in ('label_span','value_span','source_span'):
            s=f[key]
            if s!=span(pages[s['page']],s['char_start'],s['char_start']+s['char_length']):raise ValueError('metadata source anchor differs')
        if f['label']!=f['label_span']['exact_quote'] or f['value']!=f['value_span']['exact_quote']:raise ValueError('metadata literal differs')
        if 'normalization' in f and f['normalization']!=normalize_date(f['value']):raise ValueError('metadata date normalization differs')
    if any(i not in ids for p in record['primary_fields'].values() for i in p['candidate_ids']):raise ValueError('unbound primary metadata field')
    if any(r['from_field_id'] not in ids or r['to_field_id'] not in ids or r['text_equivalence_verified'] for r in record.get('cross_language_correlations',[])):raise ValueError('invalid bilingual correlation')
