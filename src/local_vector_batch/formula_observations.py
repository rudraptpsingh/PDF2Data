"""Bounded numeric worked equalities, preserving source and unresolved formula text."""
import ast,re
from decimal import Decimal,localcontext
from fractions import Fraction
from .document_blocks import span,digest
from .document_metadata import field_geometry

NUMBER=r'[+\-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)'
EQUALITY=re.compile(r'(?<![\w.,])(?P<left>'+NUMBER+r'[\d\s.,()+\-−*/×÷]*?)\s*=\s*(?P<right>'+NUMBER+r')\s*(?P<unit>MWh|kWh|MW|kW|Hz|INR|%)?(?![\w.])')
OPS={ast.Add:('add',lambda a,b:a+b),ast.Sub:('subtract',lambda a,b:a-b),ast.Mult:('multiply',lambda a,b:a*b),ast.Div:('divide',lambda a,b:a/b)}

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


def extract_formulas(data):
    layouts={p['page']:p for p in data.get('layout',[])};records=[]
    for page in data['pages']:
        known=[]
        for match in EQUALITY.finditer(page['text']):
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
        for line in re.finditer(r'[^\n]*[=≤≥][^\n]*',page['text']):
            if any(a<line.end() and b>line.start() for a,b in known):continue
            records.append(dict(id='formula-'+digest([data['source_pdf_sha256'],page['page'],line.start(),line.group()])[:24],kind='unparsed_formula_candidate',literal=line.group(),
                source_span=span(page,line.start(),line.end()),expression_tree=None,arithmetic_status='unparsed',result_unit_literal=None,
                geometry=field_geometry(page,layouts.get(page['page'],{}),line.start(),line.end()),
                variable_bindings_verified=False,legal_applicability='unknown',publication_eligible=False))
    return dict(schema_version='pdf2data.formulas.v1',source_pdf_sha256=data['source_pdf_sha256'],formulas=records,
                grammar='bounded numeric + - * / with parentheses and stated equality; symbolic math remains unresolved',publication_eligible=False)
