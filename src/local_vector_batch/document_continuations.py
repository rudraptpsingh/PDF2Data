"""Source-bound cross-page candidates, without rewriting fragments or filling cells.

Geometry and lexical cues establish candidates for inspection, never verified
table identity, legal dependency or equivalent accounting periods.
"""
from collections import defaultdict
from copy import deepcopy
import re

from .document_blocks import digest, open_continued_sentence
from .table_headers import first_row_header_candidate

FURNITURE = {'header', 'footer', 'whitespace', 'alternative_reading'}
CONTINUED = re.compile(r'\s*\(?\s*(?:continued|continuation|contd\.?|जारी)\s*\)?\s*', re.I)
TOTAL = re.compile(r'^\s*(?:grand\s+total|sub\s*total|total|कुल|योग)\b', re.I)


def _text(block):
    return ''.join(s['exact_quote'] for s in block['spans']).strip()


def _header(table):
    if first_row_header_candidate(table.get('cells', [])) is None:
        return None
    return [re.sub(r'\s+', ' ', str(v or '')).strip().casefold() for v in table['cells'][0]]


def _columns(table, width):
    boxes = [c['bbox'] for c in table.get('cell_spans', []) if c.get('bbox')]
    if not boxes or not width:
        return None
    return sorted({round(float(x)/width, 5) for b in boxes for x in (b[0], b[2])})


def _marker(marker):
    value = marker.strip('(). ').casefold()
    if value.isdigit():
        return ('number', int(value))
    if len(value) == 1 and 'a' <= value <= 'z':
        return ('letter', ord(value))
    romans = ['i','ii','iii','iv','v','vi','vii','viii','ix','x']
    if value in romans:
        return ('roman', romans.index(value))
    return ('bullet', marker) if marker in ('•','▪','●','-','–') else None


def build_continuations(data):
    pages = {p['page']:p for p in data['pages']}
    layouts = {p['page']:p for p in data.get('layout', [])}
    blocks = data.get('document_blocks', {}).get('blocks', [])
    by_page = defaultdict(list)
    for block in blocks:
        if block['kind'] not in FURNITURE:
            for number in {s['page'] for s in block['spans']}:
                by_page[number].append(block)
    tables = defaultdict(list)
    for table in data.get('tables', []):
        tables[table['page']].append(table)
    links, unresolved, roots = [], [], {}

    def link(kind, before, after, first, second, basis, **extra):
        record = dict(kind=kind, from_id=before, to_id=after, from_page=first,
                      to_page=second, basis=basis, status='unreviewed_candidate',
                      source_pdf_sha256=data['source_pdf_sha256'],
                      page_text_sha256=[pages[first]['text_sha256'],pages[second]['text_sha256']],
                      publication_eligible=False, **extra)
        record['id'] = 'continuation-'+digest(record)[:24]
        links.append(record)

    for number in sorted(pages):
        following = number+1
        if following not in pages:
            continue
        left, right = layouts.get(number, {}), layouts.get(following, {})
        if tables[number] and tables[following]:
            a = max(tables[number], key=lambda t:(t.get('bbox') or [0,0,0,0])[3])
            b = min(tables[following], key=lambda t:(t.get('bbox') or [0,0,0,0])[1])
            reason = None
            ab, bb = a.get('bbox'), b.get('bbox')
            ac, bc = _columns(a,left.get('width')), _columns(b,right.get('width'))
            if not ab or not bb or not left.get('height') or not right.get('height') or not ac or not bc:
                reason = 'source_geometry_unavailable'
            elif ab[3]/left['height'] < .7 or bb[1]/right['height'] > .25:
                reason = 'table_not_at_adjacent_page_boundary'
            elif len(ac)!=len(bc) or max(abs(x-y) for x,y in zip(ac,bc))>.012:
                reason = 'column_geometry_differs'
            elif max(map(len,a.get('cells',[])),default=0)!=max(map(len,b.get('cells',[])),default=0):
                reason = 'column_count_differs'
            elif a.get('cells') and any(TOTAL.match(str(v or '')) for v in a['cells'][-1]):
                reason = 'preceding_table_has_terminal_total'
            else:
                intervening = [block for block in by_page[following]
                               if len({s['page'] for s in block['spans']})==1
                               and block.get('bbox') and block['bbox'][3] <= bb[1]
                               and not CONTINUED.fullmatch(_text(block))]
                trailing = [block for block in by_page[number]
                            if len({s['page'] for s in block['spans']})==1
                            and block.get('bbox') and block['bbox'][1] >= ab[3]]
                if intervening or trailing:
                    reason = 'intervening_body_content_or_new_section'
            root = roots.get(a['id'], a)
            header = _header(root)
            if reason is None and header is None:
                reason = 'header_origin_not_established'
            if reason is None and _header(b) is not None and _header(b)!=header:
                reason = 'new_or_conflicting_header'
            if reason:
                unresolved.append(dict(kind='table_boundary',from_id=a['id'],to_id=b['id'],
                                       from_page=number,to_page=following,reason=reason))
            else:
                roots[b['id']] = root
                link('table_continuation_candidate',a['id'],b['id'],number,following,
                     ['adjacent_pages','boundary_geometry','matching_column_boundaries','no_new_body_section'],
                     group_id='table-group-'+digest([data['source_pdf_sha256'],root['id']])[:24],
                     header_context=dict(table_id=root['id'],page=root['page'],row=0,
                                         table_version_id=root.get('table_version_id'),
                                         raw_cells=deepcopy(root['cells'][0]),
                                         association='source_reference_only_not_copied_into_fragment'),
                     repeated_header_candidate=_header(b)==header,
                     from_bbox=deepcopy(ab),to_bbox=deepcopy(bb))

        if not by_page[number] or not by_page[following]:
            continue
        a, b = by_page[number][-1], by_page[following][0]
        if a['id']==b['id']:
            # Existing continuous paragraphs retain separate exact source spans.
            if a['kind'] in ('paragraph','list_item'):
                link('paragraph_continuation_candidate',a['id'],b['id'],number,following,
                     ['existing_source_parent_with_exact_spans'],
                     evidence=[deepcopy(s) for s in a['spans'] if s['page'] in (number,following)])
            continue
        ab, bb = a.get('bbox'), b.get('bbox')
        if not ab or not bb or not left.get('height') or not right.get('height'):
            continue
        if ab[3]/left['height']<.7 or bb[1]/right['height']>.25:
            continue
        if not a.get('parent_unit_ids') or a['parent_unit_ids']!=b.get('parent_unit_ids'):
            continue
        if abs(ab[0]-bb[0])>12 or a.get('quote_id')!=b.get('quote_id'):
            continue
        if a['kind']=='list_item' and b['kind']=='list_item':
            am, bm = _marker(a.get('marker','')), _marker(b.get('marker',''))
            romans=['i','ii','iii','iv','v','vi','vii','viii','ix','x']
            av,bv=(v.get('marker','').strip('(). ').casefold() for v in (a,b))
            if av in romans and bv in romans and romans.index(bv)==romans.index(av)+1:
                am,bm=('roman',romans.index(av)),('roman',romans.index(bv))
            if not am or not bm or am[0]!=bm[0]:
                continue
            if not (am[0]=='bullet' and am==bm or am[0]!='bullet' and bm[1]==am[1]+1):
                continue
            kind='list_continuation_candidate'
        elif a['kind'] in ('paragraph','list_item') and b['kind']=='paragraph':
            before, after = _text(a), _text(b)
            numbered=any(u['id'] in a['parent_unit_ids'] and re.fullmatch(r'\d+(?:\.\d+)*',str(u.get('label','')))
                         for u in data.get('structure',{}).get('units',[]))
            if not open_continued_sentence(before,after,numbered):
                continue
            kind='list_item_continuation_candidate' if a['kind']=='list_item' else 'paragraph_continuation_candidate'
        else:
            continue
        link(kind,a['id'],b['id'],number,following,
             ['adjacent_pages','shared_candidate_parent','boundary_geometry','compatible_indent_and_markers'],
             evidence=deepcopy(a['spans']+b['spans']))
    return dict(schema_version='pdf2data.continuations.v1',source_pdf_sha256=data['source_pdf_sha256'],
                links=links,unresolved_boundaries=unresolved,publication_eligible=False,
                policy='candidate links only; original fragments, headers, blanks and text unchanged')
