"""Map existing extracted pages to verified original page rasters, without OCR.

Review annotations are supplied observations, never newly inferred legal facts.
The source bundle is immutable. This command writes a separate review package.
"""
from pathlib import Path
import hashlib
import json
import math
import shutil
from .document_blocks import validated_cells


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def build_page_map(document_path, layout_path, output, notes_path=None):
    document_path, layout_path, output = map(Path, (document_path, layout_path, output))
    if output.exists():
        raise ValueError('page-map output already exists; use a new folder')
    document, layout = load(document_path), load(layout_path)
    if layout.get('schema_version') != 'pdf2data.region-layout.v1':
        raise ValueError('unsupported region layout')
    source = Path(document['source_pdf_path'])
    source_sha = document['source_pdf_sha256']
    if digest(source) != source_sha:
        raise ValueError('original PDF changed')
    candidates = [d for d in layout['documents'] if d['source_pdf_sha256'] == source_sha]
    if len(candidates) != 1:
        raise ValueError('expected exactly one matching source in layout')
    source_document = candidates[0]
    pages = document['pages']
    numbers = [p['page'] for p in pages]
    if len(numbers) != len(set(numbers)) or numbers != list(range(1, len(pages) + 1)):
        raise ValueError('extracted pages are missing, duplicated or out of physical order')
    if numbers != [p['page'] for p in source_document['pages']]:
        raise ValueError('source and extracted page inventories differ')
    known, mapped = set(), []
    for page, original in zip(pages, source_document['pages']):
        if hashlib.sha256(page['text'].encode()).hexdigest() != page['text_sha256']:
            raise ValueError('extracted page text hash changed')
        if original['source_pdf_sha256'] != source_sha:
            raise ValueError('source page belongs to another PDF')
        image = Path(original['source_image'])
        if digest(image) != original['source_image_sha256']:
            raise ValueError('source page raster changed')
        width, height = original['page_size_points']
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise ValueError('invalid original page dimensions')
        regions = {r['id'] for r in original['regions']}
        for block in page['blocks']:
            if block['region_id'] not in regions or block['region_id'] in known:
                raise ValueError('extracted block has no unique original region')
            known.add(block['region_id'])
            x0, y0, x1, y1 = block['bbox']
            if not all(math.isfinite(v) for v in block['bbox']) or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError('extracted block outside original page')
        blocks = []
        for block in page['blocks']:
            tables = [t for t in document.get('tables', []) if t['page'] == page['page'] and
                      (t['id'] == block.get('selected_table_id') or t['id'].startswith(block['region_id'] + ':'))]
            blocks.append({**block, 'tables': [dict(id=t['id'], row_count=len(t['cells']),
                           cells=validated_cells(t)) for t in tables]})
        mapped.append({**page, 'blocks': blocks, 'source_image': f'page-{page["page"]}.png',
                       'source_image_sha256': original['source_image_sha256'],
                       'page_size_points': original['page_size_points']})
    notes = load(notes_path) if notes_path else {'pages': [], 'relationships': [], 'findings': []}
    for annotation in notes.get('pages', []):
        if annotation['page'] not in numbers:
            raise ValueError('annotation points to an absent page')
    relationship_ids = set()
    for edge in notes.get('relationships', []):
        if edge['id'] in relationship_ids:
            raise ValueError('duplicate relationship ID')
        relationship_ids.add(edge['id'])
        for endpoint in edge['from'] + edge['to']:
            if endpoint not in known:
                raise ValueError('relationship points to an absent block')
    for finding in notes.get('findings', []):
        if any(p not in numbers for p in finding['pages']):
            raise ValueError('finding points to an absent page')
        if any(ref not in known for ref in finding.get('blocks', [])):
            raise ValueError('finding points to an absent block')
    # No output directory is created until every binding and annotation passes.
    result = {'schema_version': 'pdf2data.page-map.v1', 'source_pdf_sha256': source_sha,
              'source_pdf_path': str(source.resolve()), 'document_sha256': digest(document_path),
              'layout_sha256': digest(layout_path), 'annotation_sha256': digest(notes_path) if notes_path else None,
              'annotation_basis': 'supplied_review_observations_not_automatic_relationship_extraction',
              'publication_eligible': False, 'pages': mapped, 'annotations': notes,
              'validation_scope': 'artifact identity, physical page and block bindings; not semantic completeness'}
    output.mkdir(parents=True)
    shutil.copyfile(source, output / 'original.pdf')
    for page, original in zip(pages, source_document['pages']):
        shutil.copyfile(original['source_image'], output / f'page-{page["page"]}.png')
    (output / 'page-map.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (output / 'index.html').write_text(render_html(result))
    (output / 'page-map.md').write_text(render_markdown(result))
    return {'output': str(output.resolve()), 'pages_mapped': len(pages), 'blocks_mapped': len(known),
            'review_relationships': len(notes.get('relationships', [])),
            'findings': len(notes.get('findings', [])), 'publication_eligible': False}


def render_markdown(data):
    notes = data['annotations']
    lines = ['# Original-to-output page map', '', notes.get('scope', ''), '',
             f"Original PDF SHA-256: `{data['source_pdf_sha256']}`", '',
             'Supplied review annotations; relationships below are not existing parser output or approved legal conclusions.', '',
             '| Original page | Body blocks | Furniture blocks | What is present / missing |', '| --- | ---: | ---: | --- |']
    by_page = {p['page']: p for p in notes.get('pages', [])}
    for p in data['pages']:
        body = sum(b['scope'] == 'body' for b in p['blocks'])
        annotation = by_page.get(p['page'], {})
        lines.append(f"| {p['page']} | {body} | {len(p['blocks']) - body} | {annotation.get('summary', '').replace('|', '/')} |")
    lines += ['', '## Findings', '']
    for finding in notes.get('findings', []):
        lines += [f"### {finding['id']}: {finding['title']}", '',
                  'Original pages: ' + ', '.join(map(str, finding['pages'])), '', finding['detail'], '',
                  '**Required change:** ' + finding['change'], '', '**Validation:** ' + finding['validation'], '']
    lines += ['## Relationship map', '']
    for edge in notes.get('relationships', []):
        lines += [f"- **{edge['id']} / {edge['type']}**: {edge['description']}",
                  '  - From: ' + ', '.join(f'`{r}`' for r in edge['from']),
                  '  - To: ' + ', '.join(f'`{r}`' for r in edge['to'])]
    return '\n'.join(lines) + '\n'


def render_html(data):
    # User/PDF text never becomes executable markup. Tables are reconstructed
    # from JSON cells, not from model-supplied HTML/Markdown.
    payload = json.dumps(data, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return HTML.replace('__PAYLOAD__', payload).replace('__PAGE_COUNT__', str(len(data['pages'])))


HTML = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDF2Data · Original to output</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f3;color:#203b31;font:15px/1.55 system-ui}a{color:#1b634c}button,select{font:inherit}button{cursor:pointer;background:white;border:1px solid #cbd7cf;border-radius:8px;padding:8px 14px;color:inherit}button[aria-pressed=true]{background:#24664e;color:white}header{padding:20px 24px;background:white;border-bottom:1px solid #dce3dd}header strong{font-size:21px}header p{margin:5px 0;font-size:13px;color:#58695e}nav{position:sticky;top:0;z-index:5;background:#f3f5f3ee;backdrop-filter:blur(10px);padding:12px 24px;border-bottom:1px solid #dce3dd;display:flex;gap:8px;align-items:center;flex-wrap:wrap}main{max-width:1000px;margin:auto;padding:20px}h1{font-size:23px;margin:0 0 8px}h2{font-size:18px;margin:0 0 14px}h3{font-size:15px;margin:4px 0 8px}.card{background:white;border:1px solid #dce3dd;border-radius:12px;padding:20px;margin-bottom:18px}.muted{font-size:12px;color:#627467}.warning{border-left:4px solid #b97830}.badge{font-size:11px;padding:2px 7px;background:#edf1ed;border-radius:5px;display:inline-block}.source{position:relative;max-width:740px;margin:auto;line-height:0}.source img{width:100%;height:auto}.source svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.source rect{fill:#e8a63c30;stroke:#bc6b10;stroke-width:1.5}.block{border:1px solid #e1e6e1;border-radius:9px;padding:14px;margin:10px 0}.block.active{border-color:#b97830}.block button{padding:3px 9px;font-size:12px;float:right}.text{white-space:pre-wrap;color:#263a30;margin:8px 0}.refs{overflow-wrap:anywhere;font:11px/1.5 ui-monospace,monospace;color:#617467}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:8px;border:1px solid #dbe3dc;text-align:left;vertical-align:top;white-space:pre-wrap}.scroll{overflow-x:auto}.gap{background:#fff9ee}.edge{border-top:1px solid #e5e9e4;padding:12px 0}.edge button{font-size:11px;padding:3px 6px;margin:2px}.actions{display:flex;gap:15px;flex-wrap:wrap;font-size:13px}details{margin:12px 0}summary{cursor:pointer}#overview td:first-child{white-space:nowrap}#pageDescription{margin-bottom:16px}section[hidden]{display:none}@media(max-width:600px){main{padding:12px}.card{padding:14px}nav{padding:10px 12px}header{padding:16px}button{padding:7px 10px}}
</style><header><strong>PDF2Data <span class="muted">/ Original to output</span></strong><p>__PAGE_COUNT__ original pages · traceable blocks · supplied review observations</p><div class="actions"><a href="original.pdf" target="_blank" rel="noopener">Original PDF ↗</a><a href="page-map.json">JSON map</a><a href="page-map.md">Review notes</a></div></header>
<nav><span class="muted">Page</span><span id="pageButtons"></span><button id="prev">←</button><button id="next">→</button><select id="view" aria-label="Review view"><option value="compare">Source & output</option><option value="relationships">Relationships & gaps</option><option value="overview">All pages</option></select></nav>
<main><h1 id="title"></h1><p id="pageDescription"></p><section id="compare"><div class="card"><h2>Original page <span class="muted">Select an output block to locate it here</span></h2><div class="source"><img id="sourceImage" alt="Original PDF page"><svg id="overlay"></svg></div></div><div class="card"><h2>Current extracted blocks</h2><p class="muted">Exact exported text in candidate reading order. Styled headings, nested lists and quotations are not reconstructed by this map.</p><div id="blocks"></div><details><summary>Furniture retained in JSON</summary><div id="furniture"></div></details></div></section><section id="relationships" hidden><div id="findings"></div><div class="card"><h2>Relationships needed for this page</h2><p class="muted">Source-reviewed annotations for this example. These edges are absent from the current extraction schema.</p><div id="edges"></div></div></section><section id="overview" hidden><div class="card"><h2>Physical page correspondence</h2><div class="scroll"><table><thead><tr><th>Original → output</th><th>Body / furniture</th><th>Review</th></tr></thead><tbody id="mapRows"></tbody></table></div></div><div class="card warning"><h2>What this map proves</h2><p>Original PDF and raster hashes match the saved artifacts. Every displayed block maps to an existing original region and bounded page coordinates. It does not prove complete OCR or legal correctness. The earlier 7 probes do not measure document hierarchy or relationship coverage.</p><p id="scope"></p><p class="refs" id="sha"></p></div></section></main>
<script type="application/json" id="data">__PAYLOAD__</script><script>
const d=JSON.parse(document.getElementById('data').textContent);let index=0;
const $=id=>document.getElementById(id),e=(tag,text)=>{let x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x};
const notes=new Map((d.annotations.pages||[]).map(p=>[p.page,p]));
function go(n){index=Math.max(0,Math.min(d.pages.length-1,n));render();location.hash='page-'+d.pages[index].page;window.scrollTo({top:0});}
function locate(ref){let n=d.pages.findIndex(p=>p.blocks.some(b=>b.region_id===ref));if(n<0)return;index=n;$('view').value='compare';render();let b=d.pages[n].blocks.find(b=>b.region_id===ref),[w,h]=d.pages[n].page_size_points,[x,y,x1,y1]=b.bbox;let r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('x',x);r.setAttribute('y',y);r.setAttribute('width',x1-x);r.setAttribute('height',y1-y);$('overlay').replaceChildren(r);for(let node of document.querySelectorAll('.block'))if(node.dataset.ref===ref)node.classList.add('active');$('sourceImage').scrollIntoView({block:'start',behavior:'smooth'});location.hash='page-'+d.pages[n].page;}
function refButton(ref){let b=e('button',ref.split(':').slice(-2).join(' · '));b.onclick=()=>locate(ref);b.title=ref;return b;}
function blockNode(b){let x=e('div');x.className='block';x.dataset.ref=b.region_id;let locateButton=e('button','Locate on original');locateButton.onclick=()=>locate(b.region_id);x.append(locateButton,e('span',b.kind.replaceAll('_',' ')));let id=e('p',b.region_id);id.className='refs';x.append(id);if(b.kind==='table'&&b.tables.length){for(let table of b.tables){let wrap=e('div');wrap.className='scroll';let t=e('table');for(let i=0;i<table.row_count;i++){let tr=e('tr');for(let cell of table.cells.filter(c=>c.row===i)){let td=e(cell.role_candidate==='header'?'th':'td',cell.text===null?'':String(cell.text));td.rowSpan=cell.row_span;td.colSpan=cell.column_span;tr.append(td)}t.append(tr)}wrap.append(t);x.append(wrap)}}else{let text=e('p',b.text);text.className='text';x.append(text)}return x;}
function render(){let p=d.pages[index],a=notes.get(p.page)||{};$('title').textContent='Original page '+p.page+' → extracted page '+p.page;$('pageDescription').textContent=a.summary||'';$('sourceImage').src=p.source_image;$('sourceImage').alt='Original PDF, physical page '+p.page;$('overlay').setAttribute('viewBox','0 0 '+p.page_size_points.join(' '));$('overlay').replaceChildren();$('pageButtons').replaceChildren();d.pages.forEach((p,i)=>{let b=e('button',String(p.page));b.setAttribute('aria-pressed',i===index);b.onclick=()=>go(i);$('pageButtons').append(b)});$('prev').disabled=index===0;$('next').disabled=index===d.pages.length-1;$('blocks').replaceChildren(...p.blocks.filter(b=>b.scope==='body').map(blockNode));$('furniture').replaceChildren(...p.blocks.filter(b=>b.scope!=='body').map(blockNode));$('findings').replaceChildren();for(let f of d.annotations.findings||[]){if(!f.pages.includes(p.page))continue;let c=e('div');c.className='card warning';c.append(e('h2',f.id+' · '+f.title),e('p',f.detail),e('h3','Required change'),e('p',f.change),e('h3','Validation'),e('p',f.validation));let refs=e('div');for(let ref of f.blocks||[])refs.append(refButton(ref));c.append(refs);$('findings').append(c)}$('edges').replaceChildren();for(let edge of d.annotations.relationships||[]){let refs=[...edge.from,...edge.to];if(!refs.some(ref=>p.blocks.some(b=>b.region_id===ref)))continue;let c=e('div');c.className='edge';c.append(e('h3',edge.id+' · '+edge.type),e('p',edge.description));for(let side of ['from','to']){let row=e('div',side+' ');for(let ref of edge[side])row.append(refButton(ref));c.append(row)}$('edges').append(c)}for(let id of ['compare','relationships','overview'])$(id).hidden=$('view').value!==id;}
$('prev').onclick=()=>go(index-1);$('next').onclick=()=>go(index+1);$('view').onchange=render;
for(let p of d.pages){let tr=e('tr'),cell=e('td'),b=e('button',p.page+' → '+p.page);b.onclick=()=>{go(p.page-1);$('view').value='compare';render()};cell.append(b);let body=p.blocks.filter(b=>b.scope==='body').length;tr.append(cell,e('td',body+' / '+(p.blocks.length-body)),e('td',(notes.get(p.page)||{}).summary||''));$('mapRows').append(tr)}$('scope').textContent=d.annotations.scope||'';$('sha').textContent='PDF SHA-256 '+d.source_pdf_sha256;let initial=Number(location.hash.replace('#page-',''));if(initial>=1&&initial<=d.pages.length)index=initial-1;render();
</script></html>'''
