"""Source-bound logical display blocks; legal hierarchy remains unreviewed.

Raw pages and immutable legal-unit spans are never rewritten. Tables replace a
text interval only when a unique complete character inventory and source geometry
support it. Unknown ordering remains a separate unresolved candidate.
"""
from collections import Counter
from hashlib import sha256
from html import escape
import json,re,statistics

def compact(text):return re.sub(r'\s+','',text)
def inventory(text):return Counter(compact(text))
def digest(value):return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def span(page,lo,hi):
    return {'page':page['page'],'page_text_sha256':page['text_sha256'],'char_start':lo,'char_length':hi-lo,
      'offset_encoding':'codepoint','exact_quote':page['text'][lo:hi]}
def union(boxes):
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)] if boxes else None
def inside(glyph,box):
    b=glyph['bbox'];x=(b[0]+b[2])/2;y=(b[1]+b[3])/2
    return box[0]-.3<=x<=box[2]+.3 and box[1]-.3<=y<=box[3]+.3

def open_continued_sentence(before,after,numbered_parent=False):
    before,after=before.rstrip(),after.lstrip()
    if not before or not after or re.search(r'[.!?;:।。][”"\')]*$',before):return False
    if after.isupper() or re.match(r'^\d+(?:\.\d+)*[.)]?\s',after):return False
    return after[0].islower() or '\u0900'<=after[0]<='\u097f' or numbered_parent

def glyph_lines(layout):
    groups=[]
    for glyph in sorted(layout.get('glyphs',[]),key=lambda g:(g['bbox'][1],g['bbox'][0])):
        if not groups or abs(glyph['bbox'][1]-groups[-1][0]['bbox'][1])>1.5:groups.append([])
        groups[-1].append(glyph)
    return [sorted(group,key=lambda g:g['bbox'][0]) for group in groups]

def line_evidence(text,layout):
    matches=[group for group in glyph_lines(layout) if compact(''.join(g['text'] for g in group))==compact(text)]
    if len(matches)!=1:return {'bbox':None,'geometry_state':'unresolved','font_names':[],'max_font_size':None,'bold_candidate':False}
    # PDF producers can place invisible trailing spaces beyond the physical
    # page. Match the complete literal line, but bound its visible glyphs only;
    # never clamp a visible glyph or rewrite the selected source text.
    glyphs=[g for g in matches[0] if g.get('text','').strip()]
    fonts=sorted({g.get('font') or '' for g in glyphs});sizes=[g.get('size',0) for g in glyphs]
    return {'bbox':union([g['bbox'] for g in glyphs]),'geometry_state':'exact_unique_physical_line',
      'font_names':fonts,'max_font_size':max(sizes,default=0),'bold_candidate':all('bold' in (g.get('font') or '').lower() for g in glyphs if g['text'].strip())}

def margin_block_evidence(text,layout):
    """Locate every literal line of a known header/footer, without guessing."""
    lines=[line_evidence(value,layout) for value in text.splitlines() if value.strip()]
    if not lines or any(not line['bbox'] for line in lines):return {}
    return {'bbox':union([line['bbox'] for line in lines]),
      'geometry_state':'exact_unique_physical_lines',
      'font_names':sorted({font for line in lines for font in line['font_names']}),
      'max_font_size':max(line['max_font_size'] or 0 for line in lines),
      'bold_candidate':all(line['bold_candidate'] for line in lines)}

def footer_start(page,layout):
    """Only terminal page-numbered text wholly observed in the bottom margin."""
    lines=[m for m in re.finditer(r'[^\n]+(?:\n|$)',page['text'])]
    if not lines or not re.search(r'\bPage\s+'+str(page['page'])+r'(?:\s+of\s+\d+)?\s*$',lines[-1].group(),re.I):return None
    height=layout.get('height')
    if not height:return None
    selected=[]
    for line in reversed(lines[-4:]):
        evidence=line_evidence(line.group(),layout);box=evidence['bbox']
        if not box or box[1]<height*.93:break
        selected.append(line)
    return min((m.start() for m in selected),default=None)

def table_interval(page,table,layout):
    text=''.join('' if c is None else str(c) for row in table.get('cells',[]) for c in row)
    expected=inventory(text);wanted=sum(expected.values())
    if not wanted:return None,'empty_table'
    box=table.get('bbox');glyphs=layout.get('glyphs',[])
    if not box or not glyphs:return None,'source_geometry_unavailable'
    if inventory(''.join(g['text'] for g in glyphs if inside(g,box)))!=expected:return None,'table_cells_differ_from_source_glyph_inventory'
    lines=[m for m in re.finditer(r'[^\n]*(?:\n|$)',page['text']) if m.end()>m.start()]
    candidates=[]
    for start in range(len(lines)):
        counted=Counter();count=0
        for end in range(start,len(lines)):
            addition=inventory(lines[end].group());counted.update(addition);count+=sum(addition.values())
            if count>wanted:break
            if count==wanted and counted==expected:
                lo,hi=lines[start].start(),lines[end].end()
                # At least one unique source line must place this interval in
                # this table. Repeated/anagram page text is not unique proof.
                evidence=[line_evidence(m.group(),layout) for m in lines[start:end+1]]
                located=[e['bbox'] for e in evidence if e['bbox']]
                if located and all(b[0]>=box[0]-1 and b[2]<=box[2]+1 and b[1]>=box[1]-1 and b[3]<=box[3]+1 for b in located):candidates.append((lo,hi))
                break
    if len(candidates)!=1:return None,'ambiguous_or_unmapped_selected_table_interval'
    return candidates[0],'unique_exact_inventory_and_source_geometry'

def validated_cells(table):
    rows=table.get('cells',[]);height=len(rows);width=max(map(len,rows),default=0)
    spans=table.get('cell_spans',[])
    if not spans:return [{'row':r,'column':c,'row_span':1,'column_span':1,'text':v,'role_candidate':'unresolved'} for r,row in enumerate(rows) for c,v in enumerate(row)]
    result=[];occupied=set()
    for cell in spans:
        r,c=cell.get('row'),cell.get('column');rs,cs=cell.get('row_span',1),cell.get('column_span',1)
        if any(type(n) is not int for n in (r,c,rs,cs)) or r<0 or c<0 or rs<1 or cs<1 or r+rs>height or c+cs>width:raise ValueError('invalid retained cell span')
        slots={(y,x) for y in range(r,r+rs) for x in range(c,c+cs)}
        if slots&occupied:raise ValueError('overlapping retained cell spans')
        if c>=len(rows[r]) or str(cell.get('text') or '')!=str(rows[r][c] or ''):raise ValueError('retained cell text differs from grid')
        for y,x in slots-{(r,c)}:
            if x<len(rows[y]) and rows[y][x] not in (None,''):raise ValueError('merged span would hide populated source cell')
        occupied|=slots;result.append(cell)
    for r,row in enumerate(rows):
        for c,value in enumerate(row):
            if (r,c) not in occupied:result.append({'row':r,'column':c,'row_span':1,'column_span':1,'text':value,'role_candidate':'unresolved'})
    return sorted(result,key=lambda s:(s['row'],s['column']))

def table_html(table):
    cells=validated_cells(table);rendered=[]
    for row in range(len(table.get('cells',[]))):
        cols=[]
        for cell in cells:
            if cell['row']!=row:continue
            tag='th' if cell.get('role_candidate')=='header' else 'td'
            attrs=(' rowspan="'+str(cell['row_span'])+'"' if cell['row_span']>1 else '')+(' colspan="'+str(cell['column_span'])+'"' if cell['column_span']>1 else '')
            cols.append(f'<{tag}{attrs}>'+escape('' if cell.get('text') is None else str(cell['text']))+f'</{tag}>')
        rendered.append('<tr>'+''.join(cols)+'</tr>')
    return '<table data-structure="candidate"><tbody>'+''.join(rendered)+'</tbody></table>'

def build_blocks(data):
    layouts={p['page']:p for p in data.get('layout',[])};blocks=[];unresolved=[]
    for page in data['pages']:
        layout=layouts.get(page['page'],{});text=page['text'];cuts={0,len(text)};special=[]
        sizes=[g.get('size',0) for g in layout.get('glyphs',[]) if g.get('text','').strip() and g.get('size',0)>0]
        body_size=statistics.median(sizes) if sizes else 0
        for region in data['structure']['regions']:
            a=region['span']
            if a['page']==page['page'] and region['kind']!='body_candidate':special.append((a['char_start'],a['char_start']+a['char_length'],'footer' if region['kind']=='footer_candidate' else 'header',None))
        start=footer_start(page,layout)
        if start is not None:
            special=[s for s in special if s[1]<=start];special.append((start,len(text),'footer',None))
        for table in [t for t in data['tables'] if t['page']==page['page']]:
            interval,basis=table_interval(page,table,layout)
            if interval and not any(interval[0]<s[1] and interval[1]>s[0] for s in special):special.append((*interval,'table',table));table['readable_placement']={'state':'source_bound_candidate','basis':basis}
            else:
                unresolved.append({'table_id':table['id'],'page':page['page'],'reason':basis});table['readable_placement']={'state':'unresolved','reason':basis}
        # Preserve every selected character, including whitespace and margins.
        for m in re.finditer(r'[^\n]*(?:\n|$)',text):
            if m.end()>m.start():cuts.update((m.start(),m.end()))
        for lo,hi,*_ in special:cuts.update((lo,hi))
        chunks=[];ordered=sorted(cuts)
        for lo,hi in zip(ordered,ordered[1:]):
            if lo==hi:continue
            found=next((s for s in special if s[0]<=lo and hi<=s[1]),None)
            if found:
                a,b,kind,table=found
                if lo!=a:continue
                meta=margin_block_evidence(text[a:b],layout) if kind in ('header','footer') else {}
                chunks.append((a,b,kind,table,meta));continue
            value=text[lo:hi];evidence=line_evidence(value,layout);kind='paragraph';meta={}
            if not value.strip():kind='whitespace'
            else:
                marker=re.match(r'^\s*(\([a-zivx]+\)|[a-z]\)|[ivxlcdm]+\.(?=\s)|[•▪●]|[-–]\s)(?=\s|\S)',value,re.I)
                if text[:lo].rstrip().endswith('-'):marker=None
                if marker:kind='list_item';meta={'marker':marker.group(1).strip(),'list_depth_candidate':0,'nesting_state':'unresolved'}
                elif len(value.strip())<160 and evidence.get('bold_candidate') and (
                    (evidence.get('max_font_size') or 0)>body_size+1 or page['page']==1 and lo==0 or
                    re.match(r'^\s*\d+[.)]\s+\S',value) and len(value.split())<=8 and len(value.strip())<80 and value.rstrip()[-1] not in '.,;”"'):
                    kind='heading';meta={'heading_level_candidate':1,'hierarchy_state':'unresolved'}
                elif len(value.strip())<100 and value.strip().endswith(':') and evidence.get('bbox') and value.lstrip()[:1].isupper():
                    kind='heading';meta={'heading_level_candidate':2,'hierarchy_state':'unresolved'}
            chunks.append((lo,hi,kind,None,{**evidence,**meta}))
        for lo,hi,kind,table,meta in chunks:
            unit_ids=[u['id'] for u in data['structure']['units'] if any(s['page']==page['page'] and s['char_start']<hi and s['char_start']+s['char_length']>lo for s in u['spans'])]
            # Wrapped paragraph/list continuation is joined only within the same
            # source parent. Physical page boundaries remain separate spans.
            previous=blocks[-1] if blocks else None
            continuation=kind=='paragraph' and previous and previous['kind'] in ('paragraph','list_item') and previous['parent_unit_ids']==unit_ids and previous.get('bold_candidate',False)==meta.get('bold_candidate',False) and previous['spans'][-1]['page']==page['page'] and previous['spans'][-1]['char_start']+previous['spans'][-1]['char_length']==lo
            if continuation:
                from .document_model import paragraph_break
                continuation=not paragraph_break(previous,meta,text[lo:hi])
            if continuation:
                prior=previous['spans'][-1];prior['char_length']=hi-prior['char_start'];prior['exact_quote']=text[prior['char_start']:hi]
                previous['bbox']=union([b for b in (previous.get('bbox'),meta.get('bbox')) if b]);continue
            anchor=span(page,lo,hi);item={'id':'block-'+digest({'pdf':data['source_pdf_sha256'],'span':anchor,'kind':kind})[:24],
              'order':len(blocks),'kind':kind,'spans':[anchor],'parent_unit_ids':unit_ids,'semantic_state':'candidate','bbox':table.get('bbox') if table else meta.get('bbox'),**meta}
            if table:item.update(table_id=table['id'],table_version_id=table.get('table_version_id'),replacement_proof=table['readable_placement'])
            blocks.append(item)
    joined=[];previous=None
    numbered_units={u['id'] for u in data['structure']['units'] if re.fullmatch(r'\d+(?:\.\d+)*',str(u.get('label','')))}
    for block in blocks:
        before=''.join(s['exact_quote'] for s in previous['spans']).rstrip() if previous else ''
        after=''.join(s['exact_quote'] for s in block['spans']).lstrip()
        open_sentence=open_continued_sentence(before,after,bool(set(block['parent_unit_ids'])&numbered_units))
        ab=previous.get('bbox') if previous else None;bb=block.get('bbox')
        ah=layouts.get(previous['spans'][-1]['page'],{}).get('height') if previous else None
        bh=layouts.get(block['spans'][0]['page'],{}).get('height')
        boundary=bool(ab and bb and ah and bh and ab[3]/ah>=.7 and bb[1]/bh<=.25 and abs(ab[0]-bb[0])<=12)
        if open_sentence and boundary and block['kind']=='paragraph' and previous and previous['kind']=='paragraph' and block['parent_unit_ids'] and block['parent_unit_ids']==previous['parent_unit_ids'] and block['spans'][0]['page']==previous['spans'][-1]['page']+1 and block.get('bold_candidate',False)==previous.get('bold_candidate',False):
            previous['spans'].extend(block['spans']);previous['cross_page_continuation']='same_candidate_source_parent';continue
        joined.append(block)
        if block['kind'] not in ('footer','header','whitespace'):previous=block
    blocks=joined
    indents=[]
    for order,block in enumerate(blocks):
        block['order']=order;block['id']='block-'+digest({'pdf':data['source_pdf_sha256'],'spans':block['spans'],'kind':block['kind']})[:24]
        if block['kind']=='list_item' and block.get('bbox'):
            x=block['bbox'][0]
            while indents and x<indents[-1]-8:indents.pop()
            if not indents or x>indents[-1]+10:indents.append(x)
            block['list_depth_candidate']=len(indents)-1;block['nesting_state']='source_indent_candidate_not_legal_hierarchy'
        elif block['kind'] in ('heading','table'):indents=[]
    result={'schema_version':'pdf2data.document-blocks.v1','source_pdf_sha256':data['source_pdf_sha256'],'blocks':blocks,'unresolved_tables':unresolved,'legal_hierarchy':'unreviewed','publication_eligible':False}
    validate_blocks(result,data['pages']);return result

def validate_blocks(document,pages):
    if document.get('publication_eligible') is not False:raise ValueError('display blocks cannot promote legal status')
    by_page={p['page']:p for p in pages};covered={n:[] for n in by_page}
    if any(sha256(p['text'].encode()).hexdigest()!=p['text_sha256'] for p in pages):raise ValueError('document block page hash mismatch')
    if len({b['id'] for b in document['blocks']})!=len(document['blocks']):raise ValueError('duplicate document block identity')
    for block in document['blocks']:
        for a in block['spans']:
            page=by_page[a['page']]
            if a!=span(page,a['char_start'],a['char_start']+a['char_length']):raise ValueError('inexact document block source span')
            covered[a['page']].append((a['char_start'],a['char_start']+a['char_length']))
    for n,page in by_page.items():
        cursor=0
        for lo,hi in sorted(covered[n]):
            if lo!=cursor or hi<=lo:raise ValueError('document block gap or overlap')
            cursor=hi
        if cursor!=len(page['text']):raise ValueError('document block lost source text')

def render_blocks(data,id_prefix=''):
    document=data.get('document_blocks') or build_blocks(data);tables={t['id']:t for t in data['tables']};md=[];html=[]
    list_depth=-1;active_quote=None
    continuations=data.get('document_model',{}).get('continuations',{}).get('links',[])
    def close_list():
        nonlocal list_depth
        while list_depth>=0:html.append('</li></ol>');list_depth-=1
    for block in document['blocks']:
        kind=block['kind']
        if kind in ('footer','header','whitespace','alternative_reading'):continue
        quote=block.get('quote_id')
        if quote!=active_quote:
            close_list()
            if active_quote:html.append('</blockquote>')
            if quote:html.append('<blockquote data-source-quote="'+escape(quote,quote=True)+'">')
            active_quote=quote
        md_start=len(md)
        text='\n'.join(s['exact_quote'].rstrip('\n') for s in block['spans']).strip('\n');page=block['spans'][0]['page']
        if kind!='list_item':close_list()
        attrs=f'id="{escape(id_prefix+block["id"],quote=True)}" data-source-page="{page}" data-semantic-state="candidate"'
        source_pages=list(dict.fromkeys(s['page'] for s in block['spans']))
        cite=' · '.join(f'<a class="block-source" href="source.pdf#page={number}" aria-label="Source page {number}">p{number}</a>' for number in source_pages)
        if kind=='table':
            rendered=table_html(tables[block['table_id']]);md.append(rendered);html.append(f'<figure {attrs}>{rendered}<figcaption>{cite} · Table structure candidate</figcaption></figure>')
        elif kind=='heading':
            level=block.get('heading_level_candidate',2)+1;md.append('#'*level+' '+text.strip());html.append(f'<h{level} {attrs}>{escape(text.strip())}</h{level}>')
        elif kind=='list_item':
            depth=min(block.get('list_depth_candidate',0),list_depth+1)
            if depth>list_depth:html.append('<ol class="source-list" data-nesting="candidate">');list_depth+=1
            else:
                while depth<list_depth:html.append('</li></ol>');list_depth-=1
                html.append('</li>')
            lines=text.strip().splitlines();indent='  '*depth
            md.append(indent+'- '+lines[0]+''.join('\n'+indent+'  '+line for line in lines[1:]));html.append(f'<li {attrs}><p>{escape(text)}</p>{cite}')
        else:
            md.append('**'+text+'**' if block.get('bold_candidate') else text)
            rendered='<strong>'+escape(text)+'</strong>' if block.get('bold_candidate') else escape(text)
            html.append(f'<p {attrs} class="source-paragraph">{rendered} {cite}</p>')
        if len(source_pages)>1:
            md.append('<small>Source pages: '+cite+'</small>')
        owner=block.get('table_id',block['id'])
        for relation in continuations:
            if relation['from_id']==relation['to_id']:
                continue
            if owner not in (relation['from_id'],relation['to_id']):
                continue
            forward=owner==relation['from_id'];number=relation['to_page'] if forward else relation['from_page']
            label=('Continues on' if forward else 'Continued from')+f' source page {number} · candidate'
            note=f'<small class="continuation-note"><a href="source.pdf#page={number}">{escape(label)}</a></small>'
            html.append(note);md.append(note)
        if quote:
            for i in range(md_start,len(md)):md[i]='\n'.join('> '+line for line in md[i].split('\n'))
    close_list()
    if active_quote:html.append('</blockquote>')
    if document['unresolved_tables']:
        md.append('## Unresolved table candidates\n\nRaw text remains above because exact inline replacement was not proved.')
        html.append('<details><summary>Unresolved table candidates</summary><p>Raw text retained; inline replacement unproved.</p>')
        for unresolved in document['unresolved_tables']:
            table=tables[unresolved['table_id']];rendered=table_html(table);md.append(rendered);html.append(rendered)
        html.append('</details>')
    return '\n\n'.join(md)+'\n',''.join(html)
