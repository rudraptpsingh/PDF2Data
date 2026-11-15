"""Bounded numeric worked equalities, preserving source and unresolved formula text."""
import ast,re
from bisect import bisect_right
from collections import Counter
from decimal import Decimal,localcontext
from fractions import Fraction
from .document_blocks import span,digest,union
from .document_metadata import field_geometry
from .table_headers import formula_header_symbols

NUMBER=r'[+\-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)'
EQUALITY=re.compile(r'(?<![\w.,])(?P<left>(?:\(\s*)*'+NUMBER+r'[\d\s.,()+\-−*/×÷]*?)\s*=\s*(?P<right>'+NUMBER+r')\s*(?P<unit>MWh|kWh|MW|kW|Hz|INR|%)?(?![\w.])')
OPERATOR_BOUNDARY = '=<>≤≥≠≈≃≅≡+-−±∓*/×÷·⋅^'
OPS={ast.Add:('add',lambda a,b:a+b),ast.Sub:('subtract',lambda a,b:a-b),ast.Mult:('multiply',lambda a,b:a*b),ast.Div:('divide',lambda a,b:a/b)}
WRAPPED_QUOTED_FORMULA = re.compile(r'^[ \t]*[“"][^“”"=\n]{1,80}=[ \t]*[^“”"\n]{1,240}(?:\n[^“”"\n]{1,240}){1,5}[”"][ \t]*$', re.M)


def _script_runs(page, glyphs, positions, mapping):
    def metrics(position):
        glyph = glyphs[mapping[position]]
        top, bottom = glyph['bbox'][1::2]
        return round(glyph['size'], 1), round((top + bottom) / 2, 1)

    (main_size, main_center), _ = Counter(map(metrics, positions)).most_common(1)[0]
    runs = []
    for position in positions:
        size, center = metrics(position)
        if size >= main_size * .84 or abs(center - main_center) < main_size * .12:
            continue
        kind = 'subscript_candidate' if center > main_center else 'superscript_candidate'
        if runs and runs[-1][0] == kind and runs[-1][1][-1] + 1 == position:
            runs[-1][1].append(position)
        else:
            runs.append((kind, [position]))
    return [{'kind': kind, 'source_span': span(page, run[0], run[-1] + 1),
             'glyph_indexes': list(dict.fromkeys(mapping[position] for position in run))}
            for kind, run in runs]


def _aligned_formula_lines(page, layout, match):
    """Reopen every visible character through exact glyph alignment on one page."""
    if layout.get('page_text_sha256') != page['text_sha256'] or not layout.get('width') or not layout.get('height'):
        return None
    glyphs = layout.get('glyphs', ())
    starts, n = [], 0
    for glyph in glyphs:
        starts.append(n)
        n += len(glyph['text'])
    mapping = {}
    for block in layout.get('alignment', ()):
        if block['kind'] != 'equal':
            continue
        start = max(match.start(), block['selected_start'])
        stop = min(match.end(), block['selected_end'])
        for position in range(start, stop):
            offset = block['glyph_start'] + position - block['selected_start']
            index = bisect_right(starts, offset) - 1
            if index < 0:
                return None
            glyph = glyphs[index]
            if glyph['text'][offset - starts[index]:offset - starts[index] + 1] != page['text'][position]:
                return None
            mapping[position] = index
    result = []
    previous = None
    for line in re.finditer(r'[^\n]+', match[0]):
        start, stop = match.start() + line.start(), match.start() + line.end()
        visible = [position for position in range(start, stop) if not page['text'][position].isspace()]
        if not visible:
            continue
        if any(position not in mapping for position in visible):
            return None
        indexes = list(dict.fromkeys(mapping[position] for position in visible))
        boxes = [glyphs[index]['bbox'] for index in indexes]
        box = union(boxes)
        if not 0 <= box[0] < box[2] <= layout['width'] or not 0 <= box[1] < box[3] <= layout['height']:
            return None
        height = box[3] - box[1]
        if previous and not (previous[3] - 1 <= box[1] <= previous[3] + height
                             and abs(box[0] - previous[0]) <= height):
            return None
        start, stop = visible[0], visible[-1] + 1
        result.append({'source_span': span(page, start, stop), 'bbox': box, 'glyph_indexes': indexes,
                       'script_runs': _script_runs(page, glyphs, visible, mapping)})
        previous = box
    return result if len(result) >= 2 else None


def wrapped_formula(page, layout, match, source_sha):
    """Keep a quoted wrapped formula as source evidence without evaluating it."""
    text = match.group().strip()
    if (text[0], text[-1]) not in {('“', '”'), ('"', '"')}:
        return None
    expression = text[1:-1].partition('=')[2]
    if not re.search(r'\)\s*[÷/]\s*\(', expression):
        return None
    depth, slashes = 0, 0
    for char in expression:
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                return None
        elif char in '/÷' and depth == 0:
            slashes += 1
    if depth or slashes != 1:
        return None
    lines = _aligned_formula_lines(page, layout, match)
    if not lines:
        return None
    start = lines[0]['source_span']['char_start']
    last = lines[-1]['source_span']
    source = span(page, start, last['char_start'] + last['char_length'])
    literal = source['exact_quote']
    return dict(id='formula-'+digest([source_sha, source])[:24], kind='wrapped_formula_candidate',
        literal=literal, source_span=source, expression_literal=expression.strip(),
        expression_tree=None, arithmetic_status='unparsed', result_unit_literal=None,
        geometry={'bbox': union([line['bbox'] for line in lines]), 'basis': 'exact_aligned_native_glyph_lines'},
        source_lines=lines, variable_bindings_verified=False, legal_applicability='unknown', publication_eligible=False)

def stated_number(literal):
    value=literal.replace('−','-')
    unsigned=value.lstrip('+-')
    if ',' in unsigned and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?',unsigned):raise ValueError('ambiguous numeric grouping')
    return Fraction(Decimal(value.replace(',','')))


def arithmetic(expression):
    normalized=expression.replace('−','-').replace('×','*').replace('÷','/')
    def comma_number(match):
        n=match.group()
        if not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?',n):raise ValueError('ambiguous numeric grouping')
        return n.replace(',','')
    normalized=re.sub(r'\d[\d,]*,\d[\d,]*(?:\.\d+)?',comma_number,normalized)
    normalized=' '.join(normalized.split())
    if len(normalized)>240:raise ValueError('expression exceeds bounded grammar')
    tree=ast.parse(normalized,mode='eval')
    if len(list(ast.walk(tree)))>64:raise ValueError('expression too complex')
    def walk(n,depth=0):
        if depth>12:raise ValueError('expression too deep')
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):
            literal=ast.get_source_segment(normalized,n)
            if not re.fullmatch(r'\d+(?:\.\d+)?|\.\d+',literal) or len(literal)>30:raise ValueError('unsupported numeric literal')
            return Fraction(Decimal(literal)),dict(type='number',literal=literal,unit_binding='unresolved')
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            value,node=walk(n.operand,depth+1);negative=isinstance(n.op,ast.USub)
            return -value if negative else value,dict(type='negate' if negative else 'positive',argument=node)
        if isinstance(n,ast.BinOp) and type(n.op) in OPS:
            a,left=walk(n.left,depth+1);b,right=walk(n.right,depth+1);name,operation=OPS[type(n.op)]
            return operation(a,b),dict(type=name,left=left,right=right)
        raise ValueError('unsupported expression syntax')
    value,node=walk(tree.body)
    if node['type'] in ('number','positive','negate'):raise ValueError('not a compound worked expression')
    return value,node


def is_partial_equality(text, match):
    """Reject arithmetic matches clipped out of a longer expression or comparison."""
    prefix = text[text.rfind('\n', 0, match.start()) + 1:match.start()].rstrip()
    suffix = text[match.end():].partition('\n')[0].lstrip()
    return (bool(prefix) and prefix[-1] in OPERATOR_BOUNDARY + '('
            or bool(suffix) and suffix[0] in OPERATOR_BOUNDARY)


def table_formulas(data):
    result = []
    for table in data.get('tables', []):
        symbols = formula_header_symbols(table)
        for index, cell in enumerate(table.get('cell_spans', [])):
            symbol, assignment = symbols.get(cell['column'], (None, False))
            if cell['row'] != 1 or cell.get('column_span',1) != 1 or not assignment or not cell.get('bbox'):
                continue
            literal = cell['text']
            if literal != table['cells'][1][cell['column']]:
                continue
            source = dict(page=table['page'], table_id=table['id'], table_version_id=table['table_version_id'],
                          cell_id=cell['id'], cell_span_index=index, row=1, column=cell['column'],
                          char_start=0, char_length=len(literal), exact_quote=literal,
                          offset_encoding='codepoint')
            result.append(dict(id='formula-'+digest([data['source_pdf_sha256'],source])[:24],
                kind='table_formula_candidate', literal=literal, source_cell=source, symbol=symbol,
                expression_literal=literal.partition('=')[2].strip(), expression_tree=None,
                arithmetic_status='unparsed', result_unit_literal=None,
                geometry=dict(bbox=cell['bbox'],basis='native_table_cell'),
                variable_bindings_verified=False, legal_applicability='unknown', publication_eligible=False))
    return result


def extract_formulas(data, *, include_table_cells=True, include_wrapped=True):
    layouts={p['page']:p for p in data.get('layout',[])};records=[]
    cells = table_formulas(data) if include_table_cells else []
    wrapped = False
    for page in data['pages']:
        page_cells = [formula for formula in cells if formula['source_cell']['page']==page['page']]
        known=[]
        for match in EQUALITY.finditer(page['text']):
            if is_partial_equality(page['text'], match):
                continue
            # Keep a source equation on one physical line unless parentheses/
            # operators explicitly wrap; arbitrary preceding numbers are not cut off.
            literal=match.group();record=dict(id='formula-'+digest([data['source_pdf_sha256'],page['page'],match.start(),literal])[:24],
                kind='worked_numeric_equality',literal=literal,source_span=span(page,match.start(),match.end()),
                result_unit_literal=match.group('unit') or None,expression_literal=match.group('left').rstrip(),
                stated_result_literal=match.group('right'),expression_tree=None,arithmetic_status='unparsed',
                variable_bindings_verified=False,legal_applicability='unknown',publication_eligible=False)
            try:
                value,node=arithmetic(record['expression_literal']);stated=stated_number(record['stated_result_literal'])
                with localcontext() as ctx:ctx.prec=40;display=format(Decimal(value.numerator)/Decimal(value.denominator),'f')
                record.update(expression_tree=node,computed_value=display,computed_rational=f'{value.numerator}/{value.denominator}',
                              arithmetic_status='exact_match' if value==stated else 'different_or_rounded')
            except ZeroDivisionError:record['arithmetic_status']='undefined_division_by_zero'
            except (ValueError,SyntaxError,ArithmeticError):pass
            record['geometry']=field_geometry(page,layouts.get(page['page'],{}),match.start(),match.end())
            record['parent_element_ids']=[b['id'] for b in data['document_blocks']['blocks'] if any(s['page']==page['page'] and s['char_start']<match.end() and s['char_start']+s['char_length']>match.start() for s in b['spans'])]
            records.append(record);known.append((match.start(),match.end()))
        if include_wrapped:
            for match in WRAPPED_QUOTED_FORMULA.finditer(page['text']):
                if any(a < match.end() and b > match.start() for a,b in known):
                    continue
                formula = wrapped_formula(page, layouts.get(page['page'], {}), match, data['source_pdf_sha256'])
                if formula:
                    records.append(formula)
                    known.append((match.start(), match.end()))
                    wrapped = True
        for line in re.finditer(r'[^\n]*[=<>≤≥≠≈≃≅≡±∓][^\n]*',page['text']):
            if any(a<line.end() and b>line.start() for a,b in known):continue
            geometry = field_geometry(page,layouts.get(page['page'],{}),line.start(),line.end())
            if geometry and any(
                re.search(r'\(\s*'+formula['symbol']+r'\s*\)\s*=',line.group())
                and all(max(geometry['bbox'][i], formula['geometry']['bbox'][i]) <
                        min(geometry['bbox'][i+2], formula['geometry']['bbox'][i+2]) for i in (0,1))
                for formula in page_cells
            ):
                continue
            records.append(dict(id='formula-'+digest([data['source_pdf_sha256'],page['page'],line.start(),line.group()])[:24],kind='unparsed_formula_candidate',literal=line.group(),
                source_span=span(page,line.start(),line.end()),expression_tree=None,arithmetic_status='unparsed',result_unit_literal=None,
                geometry=geometry,
                variable_bindings_verified=False,legal_applicability='unknown',publication_eligible=False))
        records.extend(page_cells)
    version = 3 if wrapped else 2 if cells else 1
    return dict(schema_version=f'pdf2data.formulas.v{version}',source_pdf_sha256=data['source_pdf_sha256'],formulas=records,
                grammar='bounded numeric + - * / with parentheses and stated equality; symbolic math remains unresolved',publication_eligible=False)
