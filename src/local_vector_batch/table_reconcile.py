"""Source-geometry table reconciliation; original readings stay immutable.

Native candidates may replace a model grid only with original package identity,
physical ruling/cell geometry and exact glyph conservation. This is a source
representation choice, not a legal/publication approval.
"""
from pathlib import Path
from collections import Counter
from html import escape
import json,hashlib,time,re,unicodedata
from local_vector_batch.jsonl import jsonl_physical_lines
from local_vector_batch.document_blocks import inventory,inside,validated_cells,table_html,glyph_lines,compact
from local_vector_batch.evidence_package import verify_package
from local_vector_batch.native_tables import glyph_audit


def sha(data):return hashlib.sha256(data).hexdigest()
def load(p):return json.loads(Path(p).read_text())
def rows(p):return [json.loads(l) for l in jsonl_physical_lines(Path(p).read_text())]
def write(p,data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area
    return area/union if union>0 else 0


def native_table_proof(table,region,layout,page):
    if table.get('extraction_basis')!='native_pdf_geometry':return False,'not_native_rulings'
    if not isinstance(table.get('bbox'),list) or len(table['bbox'])!=4:return False,'native_table_bbox_absent'
    if overlap(table['bbox'],region['bbox'])<.85:return False,'different_source_region'
    if page.get('method')!='pdf_text':return False,'native_selected_text_not_available'
    coverage=page.get('independent_coverage') or {}
    if (coverage.get('char_recall') or 0)<.995 or coverage.get('digit_recall') not in (None,1):return False,'native_reader_disagreement'
    try:cells=validated_cells(table)
    except ValueError:return False,'invalid_native_grid'
    a=layout.get('native_text_raw');b=layout.get('secondary_text_raw')
    if not isinstance(a,str) or not isinstance(b,str) or Counter(c for c in a if c.isalnum())!=Counter(c for c in b if c.isalnum()):return False,'fresh_native_decoders_disagree'
    glyphs=layout.get('glyphs',[])
    if not glyphs or not cells:return False,'native_source_geometry_absent'
    text=''.join(str(c.get('text') or '') for c in cells)
    if not inventory(text):return False,'empty_native_table'
    if glyph_audit(text)['issues']:return False,'unresolved_native_glyphs'
    # Every visible encoded glyph in the detector's table ROI must be retained.
    if inventory(''.join(g['text'] for g in glyphs if inside(g,region['bbox'])))!=inventory(text):return False,'visual_region_glyph_inventory_differs'
    for cell in cells:
        if not cell.get('bbox'):return False,'native_cell_bbox_absent'
        actual=''.join(g['text'] for g in glyphs if inside(g,cell['bbox']))
        if inventory(actual)!=inventory(str(cell.get('text') or '')):return False,'native_cell_glyph_inventory_differs'
        cell_glyphs=[g for g in glyphs if inside(g,cell['bbox'])]
        ordered='\n'.join(''.join(g['text'] for g in line) for line in glyph_lines({'glyphs':cell_glyphs}))
        if compact(ordered)!=compact(str(cell.get('text') or '')):return False,'native_cell_glyph_order_differs'
    return True,'native_rulings_and_exact_cell_source_glyph_inventory'


def visual_content_corroborates(native,model_tables):
    """Independent visual reading must support values/symbols, not just geometry.

    Model columns may be misplaced: match cell content independent of its model
    column. Whitespace and separating commas are ignored; body token case is exact.
    Header words allow case variation, but electrical-unit case stays exact; commas embedded
    in amounts and dates remain part of their numeric token. Header function
    words may differ, but meaningful header words still require visual support.
    """
    def tokens(text):
        return [t for t in re.findall(r"\d+(?:[.,:/]\d+)*|[^\W\d_]+|[^\s]",unicodedata.normalize('NFC',str(text))) if t!=',']
    model_cells=[str(c) for table in model_tables for row in table['cells'] for c in row if c is not None]
    sequences=[tokens(c) for c in model_cells];all_words=Counter(t.casefold() for seq in sequences for t in seq if t.isalpha())
    if not sequences:return False,'no_independent_visual_table'
    stop={'of','in','as','per','the','a','an','or','and'}
    for cell in validated_cells(native):
        text=str(cell.get('text') or '')
        if not text.strip():return False,'native_blank_cell_not_visually_proven'
        seq=tokens(text)
        if cell.get('role_candidate')=='header':
            critical=[t for t in seq if not t.isalpha()]
            words=Counter(t.casefold() for t in seq if t.isalpha() and t.casefold() not in stop)
            units=[t for t in seq if re.fullmatch(r'(?i:mw|kw|gw|w|mwh|kwh|gwh|wh|kv|mv|v|ma|a|hz|khz|mwp|mwac|mwdc)',t)]
            if any(not any(t in other for other in sequences) for t in units):return False,'native_unit_case_disagreement'
            if words-all_words:return False,'native_header_not_visually_supported'
            if critical and not any([t for t in other if not t.isalpha()]==critical for other in sequences):return False,'native_header_symbol_or_number_disagreement'
        elif seq not in sequences:return False,'native_cell_value_disagrees_with_visual_reader'
    return True,'source_cell_values_and_symbols_visually_corroborated'


def reconcile(layout_path,read_root,native_packages,output):
    start=time.perf_counter();output=Path(output).resolve();read_root=Path(read_root);native_packages=Path(native_packages)
    if output.exists():raise ValueError('reconciliation output exists')
    layout=load(layout_path);parent=load(read_root/'read.json')
    if layout.get('schema_version')!='pdf2data.region-layout.v1' or parent.get('schema_version')!='pdf2data.region-read.v1':raise ValueError('unsupported reconciliation inputs')
    if parent['layout_sha256']!=sha(Path(layout_path).read_bytes()):raise ValueError('model reading belongs to another layout')
    output.mkdir(parents=True);documents=[];decisions=[]
    for document in layout['documents']:
        id=document['id'];source=Path(document['source_pdf_path'])
        if sha(source.read_bytes())!=document['source_pdf_sha256']:raise ValueError('original PDF changed')
        native=native_packages/id;verify_package(native,source_pdf=source)
        report=load(native/'report.json')
        if report['source_pdf_sha256']!=document['source_pdf_sha256']:raise ValueError('native package belongs to another PDF')
        pages=rows(read_root/id/'pages.jsonl');native_pages={p['page']:p for p in rows(native/'pages.jsonl')}
        native_tables=load(native/'tables.json')
        from local_vector_batch.legal_l1 import capture_layout
        captured_layout=capture_layout(source,list(native_pages.values())) if native_tables else []
        native_layout={p['page']:p for p in captured_layout}
        model_tables=load(read_root/id/'tables.json');selected=[];replacements=0
        for page in pages:
            page['native_tables_selected']=0
            source_page=next(p for p in document['pages'] if p['page']==page['page']);blocks={b['region_id']:b for b in page['blocks']}
            for region in source_page['regions']:
                if region['kind']!='table':continue
                candidates=[];rejected=[]
                for table in native_tables:
                    if table['page']!=page['page']:continue
                    passed,basis=native_table_proof(table,region,native_layout[page['page']],native_pages[page['page']])
                    if passed:
                        associated=[t for t in model_tables if t['id'].startswith(region['id']+':')]
                        passed,basis=visual_content_corroborates(table,associated)
                    if passed:candidates.append(table)
                    else:rejected.append({'table_id':table['id'],'reason':basis})
                decision={'document_id':id,'page':page['page'],'region_id':region['id'],'source_pdf_sha256':document['source_pdf_sha256'],
                          'model_tables':[t['id'] for t in model_tables if t['id'].startswith(region['id']+':')],
                          'status':'retain_model_candidate','rejected_native_candidates':rejected,'source_review_required':True}
                if len(candidates)==1:
                    table={**candidates[0],'native_table_id':candidates[0]['id'],'id':region['id']+':source-native',
                           'model_region_id':region['id'],'source_pdf_sha256':document['source_pdf_sha256'],'native_package_manifest_sha256':sha((native/'evidence-package.json').read_bytes()),
                           'selection_basis':'native_rulings_and_exact_cell_source_glyph_inventory','status':'needs_row_column_review'}
                    plain='\n'.join(' '.join(str(c) for c in row if c is not None) for row in table['cells'])
                    blocks[region['id']]={'region_id':region['id'],'kind':'table','bbox':table['bbox'],'text':plain,'markdown':table_html(table),'scope':'body','selected_table_id':table['id']}
                    selected.append(table);page['native_tables_selected']+=1;page['method']='layout_paddleocr_vl_with_native_source_tables';page['failed_regions']=[r for r in page['failed_regions'] if r!=region['id']];replacements+=1
                    decision.update(status='native_source_geometry_selected',selected_table_id=table['id'],basis=table['selection_basis'])
                else:selected.extend(t for t in model_tables if t['id'].startswith(region['id']+':'))
                decisions.append(decision)
            # Existing order comes from source regions. Reinsert recovered tables
            # at their physical detector position, not after the document body.
            order={r['id']:r['reading_order_index'] for r in source_page['regions']}
            page['blocks']=sorted(blocks.values(),key=lambda b:order[b['region_id']])
            page['text']='\n\n'.join(b['text'] for b in page['blocks'] if b['scope']=='body');page['text_sha256']=sha(page['text'].encode())
            page['table_reconciliation_required_review']=True
        dest=output/id;dest.mkdir();(dest/'native-layout.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in captured_layout));(dest/'pages.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in pages))
        write(dest/'tables.json',selected);write(dest/'model-tables.json',model_tables);write(dest/'native-tables.json',native_tables);write(dest/'table-decisions.json',[d for d in decisions if d['document_id']==id])
        write(dest/'document.json',{'pages':pages,'tables':selected,'source_pdf_sha256':document['source_pdf_sha256'],'source_pdf_path':str(source),'publication_eligible':False})
        md='\n\n'.join(b['markdown'] for p in pages for b in p['blocks'] if b['scope']=='body');(dest/'document.md').write_text(md+'\n')
        html=''.join('<section><h2>Page '+str(p['page'])+'</h2>'+''.join(b['markdown'] if b['kind']=='table' else '<pre>'+escape(b['text'])+'</pre>' for b in p['blocks'] if b['scope']=='body')+'</section>' for p in pages)
        (dest/'document.html').write_text('<!doctype html><meta charset="utf-8"><title>PDF2Data source reconciliation</title><style>body{max-width:1000px;margin:40px auto;font:15px/1.5 system-ui;padding:20px}pre{white-space:pre-wrap;font:inherit}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #ddd;padding:6px}</style><h1>Structured source candidate</h1><p>Original native cell geometry is retained where source-glyph checks pass. Model readings and decisions remain available. Source review required.</p>'+html)
        from .region_document import export_regions
        structured_export=export_regions(dest/'document.json',layout_path,dest/'structured')
        documents.append({'id':id,'pages':len(pages),'native_tables_selected':replacements,'failed_regions':sum(len(p['failed_regions']) for p in pages),'source_pdf_sha256':document['source_pdf_sha256'],'native_layout_sha256':sha((dest/'native-layout.jsonl').read_bytes()),'output':str(dest),'structured_export':structured_export})
    record={'schema_version':'pdf2data.table-reconciliation.v1','layout_sha256':sha(Path(layout_path).read_bytes()),'parent_read_sha256':sha((read_root/'read.json').read_bytes()),'parent_read_path':str(read_root.resolve()),
            'native_packages':str(native_packages.resolve()),'documents':documents,'decisions':decisions,'wall_seconds':time.perf_counter()-start,'publication_eligible':False,'source_review_required':True}
    write(output/'reconciliation.json',record);return {'output':str(output/'reconciliation.json'),'documents':len(documents),'native_tables_selected':sum(d['native_tables_selected'] for d in documents),'source_review_required':True}
