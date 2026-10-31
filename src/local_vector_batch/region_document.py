"""Lossless region-reading adapter to the shared readable block/span contract.

Physical relationships are conservative candidates. No supplied review labels or
question/answer fixtures enter assembly. Recognition and raw pages stay immutable.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime

from .document_blocks import digest, span, validate_blocks, validated_cells, compact


def sha(value):
    return hashlib.sha256(value).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def ownership(blocks):
    """Only exact parent/children key-value alternatives in the same source box."""
    alternatives = {}
    for parent in blocks:
        if parent['kind'] != 'key_value_region' or parent['scope'] != 'body':
            continue
        a = parent['bbox']
        children = [b for b in blocks if b['kind'] == 'text' and b['scope'] == 'body' and
                    b['region_id'] != parent['region_id'] and
                    a[0]-.3 <= b['bbox'][0] < b['bbox'][2] <= a[2]+.3 and
                    a[1]-.3 <= b['bbox'][1] < b['bbox'][3] <= a[3]+.3]
        children.sort(key=lambda b: (b['bbox'][1] // 3, b['bbox'][0]))
        if len(children) < 2 or compact(''.join(b['text'] for b in children)) != compact(parent['text']):
            continue
        # Competing parents or overlapping child readings are ambiguous.
        overlapping = any(min(x['bbox'][2], y['bbox'][2]) > max(x['bbox'][0], y['bbox'][0]) + .3 and
                          min(x['bbox'][3], y['bbox'][3]) > max(x['bbox'][1], y['bbox'][1]) + .3
                          for i, x in enumerate(children) for y in children[i+1:])
        if overlapping:
            continue
        for child in children:
            alternatives.setdefault(child['region_id'], []).append(parent['region_id'])
    return {key: values[0] for key, values in alternatives.items() if len(values) == 1}


def assemble(document):
    """Canonical page text contains *all* readings, including alternatives/furniture.

    Original body-text hashes are retained separately; these new transcript hashes
    are not the old body hashes or proof of exact PDF character recognition.
    """
    source_sha = document['source_pdf_sha256']
    pages, blocks, raw_by_id = [], [], {}
    numbers = [p['page'] for p in document['pages']]
    if numbers != list(range(1, len(numbers)+1)) or not numbers:
        raise ValueError('incomplete physical page inventory')
    for original in document['pages']:
        if sha(original['text'].encode()) != original['text_sha256']:
            raise ValueError('region page text hash mismatch')
        if '\n\n'.join(b['text'] for b in original['blocks'] if b['scope'] == 'body') != original['text']:
            raise ValueError('region blocks differ from saved body transcript')
        for raw in original['blocks']:
            if raw['region_id'] in raw_by_id:
                raise ValueError('duplicate source region')
            raw_by_id[raw['region_id']] = raw
        text = '\n\n'.join(b['text'] for b in original['blocks'])
        page = {**deepcopy(original), 'id': f'pdf-{source_sha[:24]}:p{original["page"]}',
                'original_body_text_sha256': original['text_sha256'], 'text': text,
                'text_sha256': sha(text.encode()), 'text_basis': 'all_region_readings_including_alternatives_and_furniture'}
        pages.append(page)
        suppressed = ownership(original['blocks'])
        cursor = 0
        for index, raw in enumerate(original['blocks']):
            if index:
                blocks.append(dict(id='gap-'+digest([source_sha, page['page'], cursor])[:24],
                                   kind='whitespace', spans=[span(page, cursor, cursor+2)],
                                   parent_unit_ids=[], semantic_state='candidate', bbox=None))
                cursor += 2
            kind = ('footer' if raw['kind'] == 'page_footer' else 'header') if raw['scope'] != 'body' else (
                'alternative_reading' if raw['region_id'] in suppressed else
                'heading' if raw['kind'] in ('section_header', 'title') else
                'table' if raw['kind'] == 'table' else 'paragraph')
            block = dict(id='block-'+digest([source_sha, raw['region_id'], raw['text']])[:24],
                         region_id=raw['region_id'], kind=kind, source_kind=raw['kind'],
                         spans=[span(page, cursor, cursor+len(raw['text']))], bbox=raw['bbox'],
                         parent_unit_ids=[], semantic_state='candidate')
            for key in ('recovery_parent_region', 'recovery_kind', 'table_fragment_of', 'reader_engine'):
                if raw.get(key) is not None:
                    block[key] = raw[key]
            if raw.get('table_fragment_of'):
                block['table_assembly_state'] = 'fragment_unstitched'
            if not raw['text']:
                raise ValueError('empty successful region reading')
            if kind == 'heading':
                block.update(heading_level_candidate=1, hierarchy_state='detector_heading_candidate')
            if kind == 'alternative_reading':
                block['alternative_of_region'] = suppressed[raw['region_id']]
            if kind == 'table':
                tables = [t for t in document.get('tables', []) if t['page'] == page['page'] and
                          (t['id'] == raw.get('selected_table_id') or t['id'].startswith(raw['region_id']+':'))]
                if len(tables) == 1:
                    validated_cells(tables[0]);block['table_id'] = tables[0]['id']
                else:
                    block.update(kind='paragraph', table_placement_unresolved=True)
            blocks.append(block)
            cursor += len(raw['text'])
    for i, block in enumerate(blocks):
        block['order'] = i
    result = dict(schema_version='pdf2data.document-blocks.v1', source_pdf_sha256=source_sha,
                  blocks=blocks, unresolved_tables=[], legal_hierarchy='unreviewed', publication_eligible=False)
    data = dict(schema_version='pdf2data.readable.v1', source_id='pdf-'+source_sha[:24],
                source_pdf_sha256=source_sha, pages=pages, tables=deepcopy(document.get('tables', [])),
                document_blocks=result, title='Extracted document', fact_candidates=[], structure_candidates=[],
                flagged_pages=len(pages), region_adapter=True, publication_eligible=False,
                quality={'results':[dict(page=p['page'], outcome='source_review_required') for p in pages]})
    enrich_structure(data)
    validate_blocks(result, pages)
    return data


def enrich_structure(data):
    blocks = data['document_blocks']['blocks']
    by_region = {b['region_id']: b for b in blocks if 'region_id' in b}
    units, groups, edges, diagnostics = {}, [], [], []
    current, quote, illustration, scenario = None, None, None, None
    last, indent_stacks = None, {}
    by_id = {b['id']: b for b in blocks}
    def node(kind, label, block):
        n = dict(id=kind+'-'+digest([data['source_pdf_sha256'], block['id'], kind, label])[:24],
                 kind=kind, label=label, member_ids=[], evidence=[*block['spans']], review='unverified')
        groups.append(n)
        return n
    def edge(a, b, kind, evidence, basis):
        edges.append(dict(**{'from':a, 'to':b}, type=kind, evidence=deepcopy(evidence),
                          basis=basis, review='unverified', publication_eligible=False))
    for block in blocks:
        if block['kind'] == 'alternative_reading':
            parent = by_region[block['alternative_of_region']]
            edge(block['id'], parent['id'], 'alternative_reading_of', block['spans']+parent['spans'], 'contained_exact_key_value_children')
        if block['kind'] in ('header', 'footer', 'whitespace', 'alternative_reading'):
            continue
        text = ''.join(s['exact_quote'] for s in block['spans'])
        page = block['spans'][0]['page']
        marker = re.match(r'^\s*(\([a-zivx]+\)|[ivx]+\.|\d+\.)\s+', text, re.I)
        label = re.match(r'^\s*(\d{1,3}(?:\.\d{1,3}){1,4})\.?\s*(?=[A-Za-z])', text)
        if quote is None and illustration is None and block['kind']!='table':
            label=label or re.match(r'^\s*(\d{1,3})\.\s+(?=[A-Za-z])',text)
        if block['kind']=='heading' and quote is None:
            label=label or re.match(r'^\s*(\d+)[.)]\s+(?=[A-Za-z])',text)
        if quote is None and label:
            current = node('clause_candidate', label.group(1), block)
            units[current['id']] = dict(id=current['id'], label=current['label'], spans=[],
                                       boundary_state='lexical_candidate', semantic_state='candidate', dependencies=[])
            illustration = scenario = None
            block['clause_start'] = True
        if current is None:
            current = node('clause_candidate', 'opening', block)
            units[current['id']] = dict(id=current['id'],label='opening',spans=[],boundary_state='candidate',semantic_state='candidate',dependencies=[])
        block['parent_unit_ids'] = [current['id']]
        units[current['id']]['spans'].extend(block['spans'])
        current['member_ids'].append(block['id'])
        block['heading_path'] = [current['label']]
        if last and quote is None and text.lstrip().startswith(('“', '"')) and (
                ''.join(s['exact_quote'] for s in last['spans']).rstrip().endswith(':') and
                block['bbox'][0] > last['bbox'][0]+4):
            quote = node('quotation_candidate', 'quoted source', block)
            quote['introduced_by'] = last['id']
            introduction=''.join(s['exact_quote'] for s in last['spans'])
            dates=list(re.finditer(r'\bOM\s+dated\s+(\d{1,2}\.\d{1,2}\.\d{4})',introduction,re.I))
            if dates:
                try:
                    value=dates[-1].group(1);normalized=datetime.strptime(value,'%d.%m.%Y')
                    quote.update(label='Quoted OM dated '+normalized.strftime('%d %B %Y'),
                                 cited_date_literal=value,cited_date_evidence=deepcopy(last['spans']))
                except ValueError:
                    diagnostics.append(dict(kind='invalid_cited_date',id=quote['id']))
            edge(quote['id'], last['id'], 'introduced_by', last['spans'], 'opening_quote_after_indented_introduction')
        if quote is not None:
            block['quote_id'] = quote['id'];quote['member_ids'].append(block['id'])
            edge(block['id'], quote['id'], 'within_quote', block['spans'], 'explicit_opening_and_closing_delimiters_candidate')
            block['heading_path'].append(quote['label'])
        caption = re.match(r'^\s*(Illustration|Example)\s+([A-Za-z0-9]+)\s*:\s*$', text, re.I)
        if caption and quote is None:
            illustration = node('illustration_candidate', caption.group(1)+' '+caption.group(2), block)
            illustration['caption_id'] = block['id'];scenario = None
        if illustration and quote is None:
            illustration['member_ids'].append(block['id']);block['illustration_id'] = illustration['id']
            block['heading_path'].append(illustration['label'])
            edge(block['id'], illustration['id'], 'member_of_illustration', block['spans'], 'explicit_caption_until_next_caption_or_clause')
        if marker and block['kind'] not in ('table','heading') and not label:
            block.update(kind='list_item', marker=marker.group(1), list_depth_candidate=0,
                         nesting_state='source_indent_candidate')
            scope = quote['id'] if quote else illustration['id'] if illustration else current['id']
            stack = indent_stacks.setdefault(scope, [])
            x = block['bbox'][0]
            while stack and x < stack[-1][0]-8:
                stack.pop()
            if stack and x > stack[-1][0]+10:
                stack.append((x, block['id']))
            elif not stack:
                stack.append((x, block['id']))
            else:
                stack[-1] = (x, block['id'])
            block['list_depth_candidate'] = len(stack)-1
            if len(stack)>1:
                parent=by_id[stack[-2][1]]
                edge(block['id'],parent['id'],'list_child_of',block['spans']+parent['spans'],'marker_and_source_indent')
            if illustration and quote is None and re.fullmatch(r'\([a-z]\)', marker.group(1)):
                scenario = node('scenario_candidate', marker.group(1), block)
                scenario['illustration_id'] = illustration['id']
        if scenario and illustration and quote is None:
            block['scenario_id'] = scenario['id'];scenario['member_ids'].append(block['id'])
            edge(block['id'], scenario['id'], 'within_scenario', block['spans'], 'explicit_lettered_scenario_and_following_paragraphs')
        if last and page > last['spans'][-1]['page'] and not block.get('clause_start') and block['kind'] != 'heading' and (quote or illustration or current['label']!='opening'):
            # Separate source spans stay intact; no synthetic text concatenation.
            target = last if quote else by_id[current['member_ids'][0]]
            if not quote and illustration:
                target = by_id[illustration['caption_id']]
            edge(block['id'], target['id'], 'continues', block['spans']+target['spans'], 'adjacent_physical_page_same_open_candidate_container')
        block['date_mentions'] = []
        for date in re.finditer(r'\b\d{1,2}[./]\d{1,2}[./]\d{4}\b', text):
            a = block['spans'][0]
            p = next(p for p in data['pages'] if p['page'] == a['page'])
            block['date_mentions'].append(dict(raw_text=date.group(), evidence=span(p,a['char_start']+date.start(),a['char_start']+date.end()),
                context='hypothetical_assumption' if re.match(r'^\s*(?:\([a-z]\)\s*)?Suppose\b',text,re.I) else 'illustrative_scenario' if illustration else 'quoted_source' if quote else 'unclassified',
                legal_date_role='unreviewed'))
        for match in re.finditer(r'\bTable\s+\d+\s+of\s+Regulation\s+\d+(?:\([^)]*\))+', text, re.I):
            citation = node('external_citation_candidate', match.group(), block)
            citation.update(target_status='unresolved', evidence_text=match.group(), member_ids=[])
            edge(block['id'], citation['id'], 'references', block['spans'], 'explicit_table_of_regulation_literal_not_local_table_number')
        if quote and re.search(r'[”"]\s*$', text):
            quote['boundary_status'] = 'explicit_delimiters_candidate';quote = None
        last = block
    if quote:
        quote['boundary_status'] = 'unclosed';diagnostics.append(dict(kind='unclosed_quotation', id=quote['id']))
    for group in groups:
        if group['kind'] != 'illustration_candidate':
            continue
        tables = [by_id[i] for i in group['member_ids'] if by_id[i]['kind'] == 'table']
        if len(tables) == 1:
            table = tables[0]
            edge(group['caption_id'], table['id'], 'caption_of', by_id[group['caption_id']]['spans']+table['spans'], 'unique_table_in_explicit_illustration_container')
            for bid in group['member_ids']:
                if bid not in (group['caption_id'], table['id']):
                    edge(bid, table['id'], 'explains', by_id[bid]['spans']+table['spans'], 'following_scenario_in_same_illustration_candidate')
        else:
            diagnostics.append(dict(kind='ambiguous_illustration_table',id=group['id'],table_count=len(tables)))
    data['structure'] = dict(units=list(units.values()), regions=[], status='candidate_only')
    data['structural_candidates'] = dict(nodes=groups, edges=edges, diagnostics=diagnostics, publication_eligible=False)
    data['title'] = next((''.join(s['exact_quote'] for s in b['spans']) for b in blocks
                          if b.get('source_kind') == 'text' and len(''.join(s['exact_quote'] for s in b['spans'])) > 80), 'Extracted document')
    validate_candidates(data)


def validate_candidates(data):
    pages={p['page']:p for p in data['pages']}
    blocks=data['document_blocks']['blocks'];groups=data['structural_candidates']['nodes']
    ids={b['id'] for b in blocks}|{g['id'] for g in groups}
    if len({g['id'] for g in groups})!=len(groups):
        raise ValueError('duplicate structural candidate identity')
    evidence=[]
    for group in groups:
        if any(i not in ids for i in group['member_ids']):
            raise ValueError('structural member absent')
        evidence.extend(group['evidence'])
    for edge in data['structural_candidates']['edges']:
        if edge['from'] not in ids or edge['to'] not in ids or edge.get('review')!='unverified':
            raise ValueError('structural edge endpoint or review state invalid')
        evidence.extend(edge['evidence'])
    for block in blocks:
        evidence.extend(d['evidence'] for d in block.get('date_mentions',[]))
    for a in evidence:
        p=pages[a['page']]
        if a!=span(p,a['char_start'],a['char_start']+a['char_length']):
            raise ValueError('structural evidence does not match exact transcript span')


def search_projections(data):
    """Search aids retain exact body spans/cell IDs; they are never new evidence."""
    tables = {t['id']:t for t in data['tables']}
    units = []
    for block in data['document_blocks']['blocks']:
        if block['kind'] in ('whitespace','header','footer','alternative_reading'):
            continue
        raw = ''.join(a['exact_quote'] for a in block['spans'])
        text, cell_ids = raw, []
        if block['kind'] == 'table':
            table = tables[block['table_id']];cells = validated_cells(table);lines = []
            for cell in cells:
                cid = cell.get('id') or f'{table["id"]}:r{cell["row"]}:c{cell["column"]}'
                cell_ids.append(cid)
                if cell.get('role_candidate') == 'header':
                    continue
                headers = [c['text'] for c in cells if c.get('role_candidate') == 'header' and
                           c['row'] < cell['row'] and c['column'] <= cell['column'] < c['column']+c['column_span']]
                label = ' / '.join(' '.join(str(v).split()) for v in headers) or f'column {cell["column"]+1}'
                lines.append(f'row {cell["row"]+1}: {label} = '+str(cell.get('text') or ''))
            text = '\n'.join(lines) if lines else raw
        units.append(dict(id=block['id'], region_id=block['region_id'], page=block['spans'][0]['page'],
                          text=' > '.join(block.get('heading_path', []))+'\n'+text,
                          evidence=deepcopy(block['spans']), cell_ids=cell_ids,
                          source_text=raw, kind=block['kind'],
                          **{key:block[key] for key in ('table_fragment_of','table_assembly_state','reader_engine') if key in block},
                          publication_eligible=False))
    return dict(schema_version='pdf2data.search-projections.v1', source_pdf_sha256=data['source_pdf_sha256'],
                units=units, derived_search_aid=True, publication_eligible=False)


def export_regions(document_path, layout_path, output,reuse_existing=False):
    from .page_map import build_page_map
    from .readable_export import full_document, html_document
    output = Path(output).absolute()
    implementation={name:sha(Path(__file__).with_name(name).read_bytes()) for name in
                    ('region_document.py','document_blocks.py','readable_export.py','page_map.py',
                     'layout_overlay.py','layout_furniture.py','document_model.py','document_metadata.py','document_parts.py','formula_observations.py','document_continuations.py','table_headers.py','table_contexts.py','overlays.js','overlays.css','viewer.css','ui.css')}
    if output.exists() and reuse_existing:
        receipt=load(output/'manifest.json')
        if (receipt['input_document_sha256']!=sha(Path(document_path).read_bytes()) or
            receipt['layout_sha256']!=sha(Path(layout_path).read_bytes()) or
            receipt['implementation_sha256']!=implementation):
            raise ValueError('saved structured export input or implementation changed')
        for name,expected in receipt['files'].items():
            path=(output/name).resolve()
            if not path.is_relative_to(output.resolve()) or sha(path.read_bytes())!=expected:
                raise ValueError('saved structured export artifact changed')
        return dict(output=str(output),open=str(output/'document.html'),reused=True)
    if output.exists() or output.is_symlink():
        raise ValueError('structured export must be a new directory')
    # Reuse the binding validator; its temporary review package never changes input.
    with tempfile.TemporaryDirectory(prefix='pdf2data-binding-') as check:
        build_page_map(document_path, layout_path, Path(check)/'map')
    original = load(document_path);layout = load(layout_path)
    source = next(d for d in layout['documents'] if d['source_pdf_sha256'] == original['source_pdf_sha256'])
    data = assemble(original)
    data['layout']=[dict(page=p['page'],width=p['page_size_points'][0],height=p['page_size_points'][1],glyphs=[]) for p in source['pages']]
    from .document_model import build_document_model
    data['document_model']=build_document_model(data)
    md, continuous, graph = full_document(data)
    existing = {n['id'] for n in graph['nodes']}
    # Clause groups already have paragraph-candidate nodes in the shared graph.
    for node in data['structural_candidates']['nodes']:
        if node['id'] not in existing:
            graph['nodes'].append(node);existing.add(node['id'])
    graph['edges'].extend(data['structural_candidates']['edges'])
    if any(e['from'] not in existing or e['to'] not in existing for e in graph['edges']):
        raise ValueError('structural graph has an unbound endpoint')
    projections = search_projections(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pdf2data-structure-',dir=output.parent) as temp:
        stage=Path(temp)/'bundle';stage.mkdir();(stage/'assets').mkdir()
        shutil.copyfile(original['source_pdf_path'],stage/'source.pdf')
        for p in source['pages']:
            shutil.copyfile(p['source_image'],stage/'assets'/f'page-{p["page"]:04d}.png')
        for name in ('ui.css','viewer.css'):
            shutil.copyfile(Path(__file__).with_name(name),stage/'assets'/name)
        for name,value in [('data.json',data),('document-blocks.json',data['document_blocks']),
                           ('document-model.json',data['document_model']),
                           ('document-metadata.json',data['document_model']['metadata']),
                           ('document-parts.json',data['document_model']['document_parts']),
                           ('formulas.json',data['document_model']['formula_observations']),
                           ('continuations.json',data['document_model']['continuations']),
                           ('relations.json',graph),('search-projections.json',projections),
                           ('quality.json',data['quality']),('raw-region-document.json',original)]:
            (stage/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        from .table_contexts import write_table_contexts
        write_table_contexts(stage/'table-contexts.jsonl',data)
        (stage/'document.md').write_text(md)
        (stage/'pages.md').write_text('\n\n'.join(f'## Page {p["page"]}\n\n'+p['text'] for p in data['pages']))
        from .layout_overlay import write_overlays
        overlays=write_overlays(stage,data,source)
        (stage/'document.html').write_text(html_document(data,False,continuous,overlays))
        (stage/'layout.html').write_text(html_document(data,False,continuous,overlays,layout_mode=True))
        receipt=dict(schema_version='pdf2data.region-structure-export.v1',source_pdf_sha256=original['source_pdf_sha256'],
                     input_document_sha256=sha(Path(document_path).read_bytes()),layout_sha256=sha(Path(layout_path).read_bytes()),
                     pages=len(data['pages']),raw_regions=sum('region_id' in b for b in data['document_blocks']['blocks']),
                     alternative_readings=sum(b['kind']=='alternative_reading' for b in data['document_blocks']['blocks']),
                     structural_edge_counts=dict(Counter(e['type'] for e in data['structural_candidates']['edges'])),
                     structure_diagnostics=data['structural_candidates']['diagnostics'],source_review_required=True,
                     failed_reading_regions=sum(len(p.get('failed_regions',[])) for p in original['pages']),
                     visual_review_regions=sum(len(p.get('visual_review_regions',[])) for p in original['pages']),
                     source_completeness_verified=False,
                     recognition_rerun=False,publication_eligible=False)
        receipt['implementation_sha256']=implementation
        receipt['files']={p.relative_to(stage).as_posix():sha(p.read_bytes()) for p in sorted(stage.rglob('*')) if p.is_file()}
        (stage/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
        if output.exists():
            raise ValueError('structured output appeared during export')
        stage.rename(output)
    return dict(output=str(output),open=str(output/'document.html'),**{k:v for k,v in receipt.items() if k!='files'})
