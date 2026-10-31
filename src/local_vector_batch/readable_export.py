"""Readable, portable outputs derived from an immutable verified L1 package."""
from .jsonl import jsonl_physical_lines
from html import escape
import hashlib,json,shutil,subprocess,tempfile
from pathlib import Path

def full_document(data):
    """One continuous body from complete parents; provenance stays separate."""
    by_page={p['page']:p for p in data['pages']};parts=[];nodes=[];edges=[]
    document_id=data['source_id'];nodes.append({'id':document_id,'kind':'document','source_pdf_sha256':data['source_pdf_sha256']})
    for p in data['pages']:
        page_id=p['id'];nodes.append({'id':page_id,'kind':'page','number':p['page'],'text_sha256':p['text_sha256']})
        edges.append({'from':document_id,'to':page_id,'type':'contains_page','basis':'package_identity'})
    md=f'# {data["title"]}\n\n'
    md+='[Original PDF](source.pdf) · [Structured JSON](data.json) · [Relations](relations.json) · [Exact page readings](pages.md)\n\n'
    md+='Continuous extracted text. Source links retained; interpretation unverified.\n\n'
    for i,unit in enumerate(data['structure']['units']):
        text=''.join(s['exact_quote'] for s in unit['spans'])
        for span in unit['spans']:
            if by_page[span['page']]['text'][span['char_start']:span['char_start']+span['char_length']]!=span['exact_quote']:raise ValueError('continuous document anchor mismatch')
        anchor=f'unit-{i+1}';heading='Paragraph '+unit['label'] if unit['label'].isdigit() else 'Opening'
        pages=list(dict.fromkeys(s['page'] for s in unit['spans']))
        links=' · '.join(f'[Source page {n}](source.pdf#page={n})' for n in pages)
        md+=f'<a id="{anchor}"></a>\n\n## {heading}\n\n{text}\n\n{links}\n\n'
        parts.append(f'<article id="{anchor}"><h2>{escape(heading)}</h2><pre class="parsed">{escape(text)}</pre><p class="muted">'+ ' · '.join(f'<a href="source.pdf#page={n}">Source page {n}</a>' for n in pages)+'</p></article>')
        nodes.append({'id':unit['id'],'kind':'paragraph_candidate','anchor':anchor,'label':unit['label'],'boundary_state':unit['boundary_state'],'semantic_state':unit['semantic_state']})
        edges.append({'from':document_id,'to':unit['id'],'type':'contains_unit','basis':'candidate_structure'})
        for span in unit['spans']:edges.append({'from':unit['id'],'to':by_page[span['page']]['id'],'type':'anchored_in','char_start':span['char_start'],'char_length':span['char_length'],'offset_encoding':'codepoint','page_text_sha256':span['page_text_sha256']})
        for dependency in unit.get('dependencies',[]):edges.append({'from':unit['id'],'to':dependency,'type':'candidate_dependency','review':'unverified'})
    for i,table in enumerate(data['tables']):
        table_id=table['id'];nodes.append({'id':table_id,'kind':'table_candidate','status':table.get('status','review_required')})
        edges.append({'from':by_page[table['page']]['id'],'to':table_id,'type':'contains_table','basis':'extracted_candidate'})
        from .document_blocks import validated_cells
        for cell in validated_cells(table):
            row,col=cell['row'],cell['column'];cell_id=cell.get('id') or f'{table_id}:r{row}:c{col}'
            nodes.append({'id':cell_id,'kind':'cell_candidate','row':row,'column':col,'value':cell.get('text'),'row_span':cell['row_span'],'column_span':cell['column_span'],'bbox':cell.get('bbox'),'role_candidate':cell.get('role_candidate')})
            edges.append({'from':table_id,'to':cell_id,'type':'contains_cell','basis':'source_cell_metadata','table_version_id':table.get('table_version_id')})
        heading=f'Table candidate {i+1} on source page {table["page"]}'
        rendered=table_html(table);md+=f'## {heading}\n\n{rendered}\n\n'
        parts.append(f'<article><h2>{heading}</h2><div class="table-scroll">{rendered}</div><p class="muted">Candidate grid. See original source page {table["page"]}; no cell merges inferred.</p></article>')
    observations=[]
    for i,fact in enumerate(data['fact_candidates']):
        fact_id=f'{document_id}:observation:{i+1}';nodes.append({'id':fact_id,'kind':'fact_candidate',**fact})
        edges.append({'from':fact_id,'to':by_page[fact['page']]['id'],'type':'observed_on_page','char_start':fact['char_start'],'char_length':fact['char_length']})
        matched=[]
        for unit in data['structure']['units']:
            if any(s['page']==fact['page'] and s['char_start']<=fact['char_start'] and fact['char_start']+fact['char_length']<=s['char_start']+s['char_length'] for s in unit['spans']):
                edges.append({'from':fact_id,'to':unit['id'],'type':'observed_within_unit','basis':'exact_span_containment'});matched.append(unit['label'])
        observations.append(f'- {fact["kind"]}: {fact["value_text"]} — [page {fact["page"]}](source.pdf#page={fact["page"]}), unit {", ".join(matched) or "unassigned"}; unverified candidate.')
    for i,candidate in enumerate(data.get('structure_candidates',[])):
        candidate_id=f'{document_id}:structure-observation:{i+1}'
        nodes.append({'id':candidate_id,'kind':'structure_candidate','candidate':candidate,'review':'unverified'})
        for span in candidate.get('evidence',[]):
            edges.append({'from':candidate_id,'to':by_page[span['page']]['id'],'type':'observed_on_page','char_start':span['char_start'],'char_length':span['char_length'],'offset_encoding':'codepoint'})
            for unit in data['structure']['units']:
                if any(s['page']==span['page'] and s['char_start']<=span['char_start'] and span['char_start']+span['char_length']<=s['char_start']+s['char_length'] for s in unit['spans']):
                    edges.append({'from':candidate_id,'to':unit['id'],'type':'observed_within_unit','basis':'exact_span_containment'})
    if observations:md+='## Extracted observations\n\n'+'\n'.join(observations)+'\n\n'
    md+='## Source relationships\n\n[relations.json](relations.json) retains provenance and observed links. Legal relationships are unverified.\n'
    if 'document_blocks' in data:
        from .document_blocks import render_blocks
        body,rendered=render_blocks(data)
        md=f'# {data["title"]}\n\n[Original PDF](source.pdf) · [Structured JSON](data.json) · [Blocks](document-blocks.json) · [Exact page readings](pages.md)\n\n'+body
        md+='\n[Source relationships](relations.json) · Candidate structure; legal interpretation unverified.\n'
        parts=[rendered];previous=None
        for block in data['document_blocks']['blocks']:
            nodes.append({'id':block['id'],'kind':'document_block','block_kind':block['kind'],'semantic_state':'candidate'})
            edges.append({'from':document_id,'to':block['id'],'type':'contains_block','order':block['order']})
            for a in block['spans']:edges.append({'from':block['id'],'to':by_page[a['page']]['id'],'type':'anchored_in','char_start':a['char_start'],'char_length':a['char_length'],'page_text_sha256':a['page_text_sha256']})
            for unit_id in block['parent_unit_ids']:edges.append({'from':block['id'],'to':unit_id,'type':'within_candidate_unit','review':'unverified'})
            if block['kind'] not in ('footer','header','whitespace'):
                if previous:edges.append({'from':previous,'to':block['id'],'type':'next_rendered_block','basis':'source_order_candidate'})
                previous=block['id']
            if block.get('table_id'):edges.append({'from':block['id'],'to':block['table_id'],'type':'renders_table_candidate'})
    graph={'schema_version':'pdf2data.relations.v1','source_pdf_sha256':data['source_pdf_sha256'],'nodes':nodes,'edges':edges,'legal_relationships':'unverified','publication_eligible':False}
    return md,''.join(parts),graph

def table_html(table):
    from .document_blocks import table_html as render_table
    return render_table(table)

def html_document(data,has_docx,continuous,overlays=None,layout_mode=False):
    from .layout_overlay import build_overlays,viewer_controls
    if overlays is None:overlays=build_overlays(data)
    pages=data['pages'];quality={r['page']:r for r in data['quality']['results']}
    sections=[]
    for p in pages:
        number=p['page'];q=quality[number];tables=[t for t in data['tables'] if t['page']==number]
        reading='<pre class="parsed">'+escape(p['text'])+'</pre>'
        if data.get('region_adapter'):
            from .document_blocks import render_blocks
            subset={**data,'document_blocks':{**data['document_blocks'],'blocks':[b for b in data['document_blocks']['blocks'] if b['spans'][0]['page']==number]}}
            reading=render_blocks(subset,id_prefix='page-view-')[1]
            reading+='<details><summary>Raw region transcript, including alternative readings</summary><pre class="parsed">'+escape(p['text'])+'</pre></details>'
            tables=[]  # already placed by the same shared source renderer
        sections.append(f'''<section class="page" id="page-{number}" {'hidden' if number!=pages[0]['page'] else ''}>
<div class="label">Original PDF · page {number}</div>
<div class="source"><div class="source-canvas"><img src="assets/page-{number:04d}.png" alt="Original PDF page {number}"></div></div>
<div class="reading"><div class="reading-head"><h2>Parsed content</h2><span class="badge">{escape(q['outcome'].replace('_',' '))}</span></div>
{reading}
{('<h3>Reconstructed table candidates</h3><p class="muted">Check rows, columns and values against the original above. Merged cell semantics are retained in JSON when available.</p>'+''.join('<div class="table-scroll">'+table_html(t)+'</div>' for t in tables)) if tables else ''}
<details><summary>Extraction details</summary><p>Reader: {escape(p['method'])} · {len(p['text'])} characters</p><p>Page SHA256 <code>{escape(p['text_sha256'])}</code></p><pre>{escape(json.dumps({'failed_components':q.get('failed_components',[]),'missing_evidence':q.get('missing_evidence',[])},indent=2))}</pre></details></div></section>''')
    options=''.join(f'<option value="{p["page"]}">Page {p["page"]} of {len(pages)}</option>' for p in pages)
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDF2Data document output</title><link rel="stylesheet" href="assets/ui.css"><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f5;color:#1b2923;font:15px/1.6 system-ui,sans-serif}header{background:#fff;border-bottom:1px solid #dfe6e1;padding:22px max(24px,calc((100% - 1100px)/2));}header h1{font-size:24px;margin:5px 0 12px;line-height:1.3}.brand{font-weight:750;color:#34634f;font-size:13px;letter-spacing:.06em}nav{display:flex;gap:8px;flex-wrap:wrap}a,button,select{font:inherit;color:inherit}nav a,button,select{border:1px solid #d4ded7;border-radius:7px;background:white;padding:7px 12px;text-decoration:none}a:hover,button:hover{background:#eef4ef}button:disabled{opacity:.35}.toolbar{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;align-items:center;gap:12px;max-width:1100px;margin:0 auto;padding:12px 0;background:#f4f6f5ee;backdrop-filter:blur(8px)}.controls{display:flex;gap:8px}.muted{color:#65716a;font-size:13px}main{max-width:1100px;margin:auto;padding:0 20px 40px}.notice{font-size:13px;color:#59665e;margin:18px 0}.label{font-weight:650;font-size:13px;padding:12px 16px;background:#fff;border:1px solid #dfe6e1;border-radius:10px 10px 0 0}.source{background:#e9eeea;padding:16px;border:1px solid #dfe6e1;border-top:0;border-radius:0 0 10px 10px}.source img{display:block;width:100%;height:auto;max-width:800px;margin:auto;box-shadow:0 2px 12px #17291e18}.reading{background:#fff;border:1px solid #dfe6e1;border-radius:10px;margin-top:18px;padding:22px}.reading-head{display:flex;align-items:center;justify-content:space-between;gap:12px}h2{font-size:18px;margin:0}.badge{border-radius:20px;padding:4px 10px;background:#fff4dc;color:#785812;font-size:12px;white-space:nowrap}.parsed{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 Georgia,serif;color:#202c25}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}details{border-top:1px solid #e5eae6;padding-top:12px;color:#59665e;font-size:13px}summary{cursor:pointer}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px;margin:12px 0}td{border:1px solid #ccd5ce;padding:8px;vertical-align:top;white-space:pre-wrap}tbody tr:first-child{background:#eef3ef}[hidden]{display:none!important}@media(max-width:600px){header{padding:18px 20px}.toolbar{align-items:flex-start}.toolbar>.muted{display:none}.reading{padding:16px}}
</style><link rel="stylesheet" href="assets/viewer.css"><link rel="stylesheet" href="assets/overlays.css"><script src="assets/overlays.js" defer></script><script>if(new URLSearchParams(location.search).has('embedded'))document.documentElement.classList.add('embedded');</script></head><body><header><div class="brand">PDF2Data</div><h1>'''+escape(data['title'])+'''</h1><nav><a href="source.pdf">PDF</a><a href="document.md" download>MD</a>'''+('<a href="document.docx" download>Word</a>' if has_docx else '')+'''<a href="data.json" download>JSON</a><a href="relations.json" download>Relations</a><a href="quality.json" download>Checks</a></nav></header><main><div class="toolbar"><div class="controls"><button id="full-mode" class="selected">Document</button><button id="compare-mode">Source &amp; layout</button></div><span class="muted">Source linked</span></div><p class="notice">'''+f'{len(pages)} pages · {data["flagged_pages"]} review flags · 0 withheld'+'''</p><div id="full" class="reading">'''+continuous+'''</div><div id="comparison" hidden>'''+viewer_controls(overlays)+'''<div class="pagebar"><div class="controls"><button id="prev">←</button><select id="pages" aria-label="PDF page">'''+options+'''</select><button id="next">→</button></div><span class="muted">Original → extracted</span></div>'''+''.join(sections)+'''</div></main><script>
document.getElementById('full-mode').onclick=()=>{document.getElementById('full').hidden=false;document.getElementById('comparison').hidden=true;document.getElementById('full-mode').classList.add('selected');document.getElementById('compare-mode').classList.remove('selected')};
document.getElementById('compare-mode').onclick=()=>{document.getElementById('full').hidden=true;document.getElementById('comparison').hidden=false;document.getElementById('compare-mode').classList.add('selected');document.getElementById('full-mode').classList.remove('selected')};
const select=document.getElementById('pages'),prev=document.getElementById('prev'),next=document.getElementById('next');
function show(scroll=false){document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='page-'+select.value);prev.disabled=select.selectedIndex===0;next.disabled=select.selectedIndex===select.options.length-1;if(scroll)window.scrollTo({top:0,behavior:'instant'});history.replaceState(null,'','#page-'+select.value)}
select.onchange=()=>show(true);prev.onclick=()=>{select.selectedIndex--;show(true)};next.onclick=()=>{select.selectedIndex++;show(true)};
const initial=location.hash.replace('#page-','');if([...select.options].some(o=>o.value===initial))select.value=initial;show();if('''+('true' if layout_mode else 'false')+''' || ['source','layout'].includes(new URLSearchParams(location.search).get('mode')))document.getElementById('compare-mode').click();
</script></body></html>'''

def export_readable(package:Path,output:Path,docx_python:Path|None=None):
    from .legal_l1 import verify_document
    package=Path(package).resolve();output=Path(output).absolute()
    if output.exists() or output.is_symlink():raise ValueError('readable output must be a new directory')
    if package==output.resolve() or package in output.resolve().parents or output.resolve() in package.parents:raise ValueError('readable output must be outside the immutable package')
    verification=verify_document(package)
    source=json.loads((package/'source-receipt.json').read_text());pages=[json.loads(l) for l in jsonl_physical_lines((package/'extraction/pages.jsonl').read_text()) if l.strip(' \t\r')]
    quality=json.loads((package/'quality.json').read_text());tables=json.loads((package/'extraction/tables.json').read_text())
    data={'schema_version':'pdf2data.readable.v1','title':source['declared'].get('title',source['declared']['id']),
      'source_id':source['declared']['id'],'source_pdf_sha256':source['actual_sha256'],'source':source,
      'pages':pages,'tables':tables,'structure':json.loads((package/'structure.json').read_text()),'quality':quality,
      'fact_candidates':[json.loads(l) for l in jsonl_physical_lines((package/'extraction/fact-candidates.jsonl').read_text()) if l.strip(' \t\r')],
      'structure_candidates':[json.loads(l) for l in jsonl_physical_lines((package/'extraction/structure-candidates.jsonl').read_text()) if l.strip(' \t\r')],
      'flagged_pages':sum(q['outcome']!='auto_accepted' for q in quality['results']),
      'layout':[json.loads(l) for l in jsonl_physical_lines((package/'layout.jsonl').read_text()) if l.strip(' \t\r')],
      'verification':verification,'publication_eligible':False}
    from .document_blocks import build_blocks
    data['document_blocks']=build_blocks(data)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pdf2data-readable-',dir=output.parent) as temp:
        stage=Path(temp)/'bundle';stage.mkdir();(stage/'assets').mkdir()
        for name in ('ui.css','viewer.css'):shutil.copyfile(Path(__file__).with_name(name),stage/'assets'/name)
        shutil.copyfile(package/'original.pdf',stage/'source.pdf')
        for name,value in [('data.json',data),('quality.json',quality)]: (stage/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        (stage/'document-blocks.json').write_text(json.dumps(data['document_blocks'],ensure_ascii=False,indent=2)+'\n')
        shutil.copyfile(package/'layout.jsonl',stage/'layout.jsonl')
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(str(package/'original.pdf'))
        try:
            for p in pages:
                page=pdf[p['page']-1];bitmap=page.render(scale=1.5)
                try:bitmap.to_pil().save(stage/'assets'/f'page-{p["page"]:04d}.png')
                finally:bitmap.close();page.close()
        finally:pdf.close()
        md='# '+data['title']+'\n\n[Original PDF](source.pdf) · [Structured JSON](data.json) · [Quality report](quality.json)\n\n'
        md+='Extracted source content. Quality flags are retained; legal interpretation and currentness are not verified.\n\n'
        for p in pages:
            md+=f'## PDF page {p["page"]}\n\n'+p['text']+'\n\n'
            for t in tables:
                if t['page']==p['page']:md+='### Reconstructed table candidate\n\n'+table_html(t)+'\n\n'
        (stage/'pages.md').write_text(md)
        document_md,continuous,graph=full_document(data)
        (stage/'document.md').write_text(document_md)
        (stage/'relations.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2)+'\n')
        if docx_python:
            result=subprocess.run([str(docx_python),str(Path(__file__).with_name('readable_word.py')),str(stage/'data.json'),str(stage/'document.docx')],capture_output=True,text=True,timeout=120)
            if result.returncode:raise ValueError('Word export failed: '+result.stderr[-1200:])
        from .layout_overlay import write_overlays
        overlays=write_overlays(stage,data)
        (stage/'document.html').write_text(html_document(data,bool(docx_python),continuous,overlays))
        (stage/'layout.html').write_text(html_document(data,bool(docx_python),continuous,overlays,layout_mode=True))
        files={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file()}
        (stage/'manifest.json').write_text(json.dumps({'schema_version':'pdf2data.readable-manifest.v1','source_pdf_sha256':data['source_pdf_sha256'],'files':files,'publication_eligible':False},indent=2)+'\n')
        # Fresh destination only; never alter the source package.
        if output.exists():raise ValueError('readable output appeared during export')
        stage.rename(output)
    return {'output':str(output),'open':str(output/'document.html'),'markdown':str(output/'document.md'),
      'word':str(output/'document.docx') if docx_python else None,'json':str(output/'data.json'),
      'pages':len(pages),'quality_flagged_pages':data['flagged_pages'],'withheld_pages':0,'source_integrity':'passed','publication_eligible':False}
