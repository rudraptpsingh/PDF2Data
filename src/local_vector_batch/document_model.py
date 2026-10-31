"""Word-like presentation model over immutable source blocks, not legal analysis."""
from collections import Counter,defaultdict
from copy import deepcopy
import re

from .document_blocks import compact,digest,glyph_lines,validate_blocks

DATE_START=re.compile(r'^\s*\d{1,2}[./-]\d{1,2}[./-]\d{4}')


def paragraph_break(previous,evidence,value):
    """Observed spacing/numbering can separate paragraphs within one legal unit."""
    if not DATE_START.match(value) and re.match(r'^\s*\d+(?:\.\d+)+[.)]?\s*(?=[A-Za-z])',value):return True
    before,after=previous.get('bbox'),evidence.get('bbox')
    if not before or not after:return False
    size=evidence.get('max_font_size') or 10
    return after[1]-before[3]>max(6,size*.8)


def inline_candidates(text):
    """Literal mentions only. No authority resolution, amounts or deadlines inferred."""
    patterns={
      'citation':r'\b(?:Regulation|Regulations|Section|Sections|Clause|Clauses|Article|Petition|Order|OM)\s+(?:(?:No\.?|dated)\s+)?\d+[A-Za-z0-9./()–-]*(?:\s*\([A-Za-z0-9-]+\))*',
      'date':r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b',
      'quantity':r'(?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:%|MW|MWh|kW|kWh|kV|days?|years?)\b|\b\d+(?:\.\d+)?%',
      'formula':r'(?m)^[^\n]{0,90}[=≤≥][^\n]{1,90}$',
      'url':r'https?://[^\s<>]+',
    }
    return [dict(kind=kind,text=m.group(),char_start=m.start(),char_length=len(m.group()),
                 status='literal_candidate',resolved_target=None)
            for kind,pattern in patterns.items() for m in re.finditer(pattern,text,re.I)]

def source_slice(spans,lo,length):
    result=[];offset=0;hi=lo+length
    for s in spans:
        end=offset+s['char_length'];a=max(lo,offset);b=min(hi,end)
        if a<b:result.append({**s,'char_start':s['char_start']+a-offset,'char_length':b-a,
                              'exact_quote':s['exact_quote'][a-offset:b-offset]})
        offset=end
    return result


def role(block,text,page,height):
    kind=block['kind'];box=block.get('bbox')
    if kind not in ('paragraph','heading','list_item'):return kind
    if re.fullmatch(r'\s*(?:Illustration|Figure|Table|Example)\s+[A-Za-z0-9]+\s*:\s*',text,re.I):return 'caption'
    if re.match(r'^\s*(?:No\.|Ref(?:erence)?\s*[:.]|Dated\s*[:.]?)',text,re.I):return 'metadata'
    if not DATE_START.match(text) and re.match(r'^\s*\d+(?:\.\d+)+[.)]?\s*(?=[A-Za-z])',text):return 'numbered_paragraph'
    if text.lstrip().startswith(('“','"')) or block.get('quote_id'):return 'quotation'
    if kind=='list_item':return 'list_item'
    if kind=='paragraph' and re.match(r'^\s*\d+[.)]\s+',text):return 'numbered_paragraph'
    if page==1 and box and height and box[3]<height*.1 and text.strip().isupper():return 'masthead'
    if page==1 and box and height and box[1]<height*.4 and block.get('bold_candidate'):
        if re.fullmatch(r'\s*(?:Explanatory Memorandum|Statement of Reasons|Record of Proceedings)\s*',text,re.I):return 'subtitle'
        if len(text.split())>=9 and not re.match(r'^\s*\d+[.)]',text):return 'title'
    return kind


def source_runs(spans,index):
    result=[]
    for anchor in spans:
        offset=0
        for line in anchor['exact_quote'].splitlines(keepends=True):
            matched=index.get(anchor['page'],{}).get(compact(line),[])
            glyphs=matched[0] if len(matched)==1 else []
            chars=[(c,g) for g in glyphs for c in g['text'] if not c.isspace()]
            # Alignment is exact after whitespace normalization, never fuzzy.
            if ''.join(c for c,g in chars)!=compact(line):chars=[]
            cursor=0;last=None
            for char in line:
                glyph=None
                if not char.isspace() and chars:glyph=chars[cursor][1];cursor+=1
                elif chars:glyph=chars[max(0,cursor-1)][1]
                fmt={'state':'unresolved'}
                if glyph:
                    font=glyph.get('font') or ''
                    fmt=dict(state='source_glyph_candidate',font_name=font,font_size_points=glyph.get('size'),
                             bold='bold' in font.lower(),italic=any(v in font.lower() for v in ('italic','oblique')))
                start=anchor['char_start']+offset
                if last is not None and last['format']==fmt:
                    last['text']+=char;last['source_span']['char_length']+=1;last['source_span']['exact_quote']+=char
                else:
                    last=dict(text=char,format=fmt,source_span={**anchor,'char_start':start,'char_length':1,'exact_quote':char})
                    result.append(last)
                offset+=1
    return result


def build_document_model(data):
    blocks=data['document_blocks'];validate_blocks(blocks,data['pages'])
    from .layout_furniture import formatting
    layouts={p['page']:p for p in data.get('layout',[])};index={}
    for number,layout in layouts.items():
        rows=defaultdict(list)
        for row in glyph_lines(layout):rows[compact(''.join(g['text'] for g in row))].append(row)
        index[number]=rows
    nodes=[];styles={};stories={k:[] for k in ('body','header','footer','whitespace','alternative_reading')}
    for b in blocks['blocks']:
        text=''.join(s['exact_quote'] for s in b['spans']);page=b['spans'][0]['page'];layout=layouts.get(page,{})
        chosen=role(b,text,page,layout.get('height'))
        story=b['kind'] if b['kind'] in stories and b['kind']!='body' else 'body'
        runs=source_runs(b['spans'],index)
        candidate=dict(role=chosen,bold=b.get('bold_candidate'),heading_level=b.get('heading_level_candidate'),
                       list_depth=b.get('list_depth_candidate'))
        style_id='style-'+digest(candidate)[:16];styles.setdefault(style_id,{**candidate,'status':'presentation_candidate'})
        node=dict(id=b['id'],kind=b['kind'],role_candidate=chosen,story=story,source_spans=deepcopy(b['spans']),
                  text=text,runs=runs,style_id=style_id,bbox=b.get('bbox'),parent_unit_ids=b['parent_unit_ids'],
                  inline_candidates=inline_candidates(text),classification_verified=False)
        for key in ('table_id','marker','heading_level_candidate','list_depth_candidate','quote_id','heading_path'):
            if key in b:node[key]=deepcopy(b[key])
        if chosen=='list_item':
            marker=b.get('marker','')
            node['list_type_candidate']='bullet' if marker in ('•','▪','●','-','–') else 'roman_or_letter_ambiguous' if re.fullmatch(r'\([ivx]+\)|[ivxlcdm]+\.',marker,re.I) else 'letter' if re.fullmatch(r'\([a-z]\)|[a-z]\)',marker,re.I) else 'unresolved'
        if chosen=='numbered_paragraph':
            label=re.match(r'^\s*(\d+(?:\.\d+)*[.)]?)',text)
            node['numbering_candidate']=dict(literal_label=label.group(1),type='decimal',automatic_numbering_verified=False)
        for mention in node['inline_candidates']:
            mention['source_spans']=source_slice(b['spans'],mention['char_start'],mention['char_length'])
            if mention['kind']=='citation':
                token=mention['text'].split()[0].casefold()
                mention['citation_type_candidate']='case_reference' if token=='petition' else 'instrument_reference' if token=='om' else 'order_reference' if token=='order' else 'provision_reference'
            if mention['kind']=='quantity':
                literal=mention['text'].casefold()
                mention['quantity_type_candidate']='percentage' if '%' in literal else 'amount' if re.match(r'₹|rs\.?|inr',literal) else 'duration' if re.search(r'\b(?:days?|years?)\b',literal) else 'measurement'
        if b['kind']=='table':
            table=next(t for t in data['tables'] if t['id']==b['table_id'])
            node['table_structure']=dict(rows=len(table['cells']),columns=max(map(len,table['cells']),default=0),
                                        cells_ref=table['id'],cell_spans=deepcopy(table.get('cell_spans',[])))
        nodes.append(node);stories[story].append(node['id'])
    body=[n for n in nodes if n['story']=='body'];references=False
    for i,n in enumerate(body):
        if re.fullmatch(r'\s*References?\s*:\s*',n['text'],re.I):references=True
        elif n['role_candidate']=='list_item' and references:n['list_context_candidate']='reference_list'
        else:references=False
        if n['role_candidate']=='caption' and i+1<len(body) and body[i+1]['kind']=='table':
            n['caption_for_candidate']=body[i+1]['id'];body[i+1]['caption_element_id_candidate']=n['id']
    for number,layout in layouts.items():
        if not layout.get('width') or not layout.get('height'):continue
        local=[{**n,'type':'text' if n['kind']=='paragraph' else n['kind']} for n in nodes if n['bbox'] and len({s['page'] for s in n['source_spans']})==1 and n['source_spans'][0]['page']==number]
        formatting(local,layout['width'],layout['height'])
        for n in local:next(v for v in nodes if v['id']==n['id'])['paragraph_format']=n['formatting']
    pages=[dict(page=p['page'],width_points=layouts.get(p['page'],{}).get('width'),height_points=layouts.get(p['page'],{}).get('height'),
                element_ids=[n['id'] for n in nodes if any(s['page']==p['page'] for s in n['source_spans'])]) for p in data['pages']]
    model=dict(schema_version='pdf2data.document-model.v1',source_pdf_sha256=data['source_pdf_sha256'],
               elements=nodes,stories=stories,styles=styles,physical_pages=pages,
               outline_candidates=[dict(element_id=n['id'],role=n['role_candidate'],level=n.get('heading_level_candidate'),status='unverified') for n in nodes if n['role_candidate'] in ('title','subtitle','heading')],
               semantic_sections=[],section_recovery='not_proven_by_pdf_page_breaks',
               unsupported_structure=['unresolved_footnote_anchors','automatic_numbering_identity','editable_equation_trees','original_word_style_names'],
               publication_eligible=False)
    from .document_metadata import extract_metadata
    model['metadata']=extract_metadata(data)
    from .formula_observations import extract_formulas
    model['formula_observations']=extract_formulas(data)
    from .document_continuations import build_continuations
    model['continuations']=build_continuations(data)
    from .document_parts import build_document_parts
    model['document_parts']=build_document_parts(data,model['metadata'],nodes)
    validate_document_model(model,data)
    return model


def validate_document_model(model,data):
    if 'document_parts' in model:
        from .document_parts import build_document_parts
        if model['document_parts']!=build_document_parts(data,model['metadata'],model['elements']):raise ValueError('document part identity or source anchors changed')
    if 'continuations' in model:
        from .document_continuations import build_continuations
        if model['continuations']!=build_continuations(data):raise ValueError('continuation source anchors or associations changed')
    if 'formula_observations' in model:
        from .formula_observations import extract_formulas
        if model['formula_observations']!=extract_formulas(data):raise ValueError('formula source anchor or arithmetic observation changed')
        pages={p['page']:p for p in data['pages']}
        for formula in model['formula_observations']['formulas']:
            s=formula['source_span'];p=pages[s['page']]
            if s['page_text_sha256']!=p['text_sha256'] or p['text'][s['char_start']:s['char_start']+s['char_length']]!=formula['literal'] or s['exact_quote']!=formula['literal']:raise ValueError('formula source anchor changed')
    if 'metadata' in model:
        from .document_metadata import validate_metadata
        validate_metadata(model['metadata'],data)
    if model['source_pdf_sha256']!=data['source_pdf_sha256'] or model['publication_eligible'] is not False:raise ValueError('invalid model source or approval state')
    expected={b['id']:b for b in data['document_blocks']['blocks']}
    if len(model['elements'])!=len(expected) or {n['id'] for n in model['elements']}!=set(expected):raise ValueError('model lost or duplicated a source block')
    for n in model['elements']:
        b=expected[n['id']]
        if n['source_spans']!=b['spans'] or n['text']!=''.join(s['exact_quote'] for s in b['spans']):raise ValueError('model changed source spans')
        if ''.join(r['text'] for r in n['runs'])!=n['text']:raise ValueError('model runs lost source text')
        offset=0
        for run in n['runs']:
            if [run['source_span']]!=source_slice(b['spans'],offset,len(run['text'])):raise ValueError('model run source anchor changed')
            offset+=len(run['text'])
        for mention in n['inline_candidates']:
            if mention['source_spans']!=source_slice(b['spans'],mention['char_start'],mention['char_length']) or ''.join(s['exact_quote'] for s in mention['source_spans'])!=mention['text']:raise ValueError('inline mention source anchor changed')
    if Counter(i for ids in model['stories'].values() for i in ids)!=Counter(expected.keys()):raise ValueError('model stories do not partition source blocks')
