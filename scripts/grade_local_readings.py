"""Grade complete local model outputs, including structure and extra numerals."""
from pathlib import Path
from html.parser import HTMLParser
import argparse,json,re,html
from collections import Counter

class Table(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None;self.unsupported_spans=False
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag in ('td','th'):
            self.cell='';a=dict(attrs)
            if a.get('rowspan','1')!='1' or a.get('colspan','1')!='1':self.unsupported_spans=True
        if tag=='br' and self.cell is not None:self.cell+='\n'
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(self.cell)
            self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data

def plain(text):return ' '.join(html.unescape(text).split())
def notation(text):
    for value in ['GNA\\(_{RE}\\)','GNA_{RE}','GNA_RE','GNA<sub>RE</sub>','GNAᴿᴱ']:text=text.replace(value,'GNARE')
    return text

def grid(text):
    if '<table' in text.lower():
        p=Table();p.feed(text);return p.rows,not p.unsupported_spans
    rows=[]
    for row in text.split('<nl>'):
        cells=[]
        for token,content in re.findall(r'(<fcel>|<ecel>|<lcel>|<ucel>|<xcel>)([^<]*)',row):
            if token in ('<lcel>','<ucel>','<xcel>'):return [],False
            cells.append(content if token=='<fcel>' else '')
        if cells:rows.append(cells)
    return rows,bool(rows)

def grade(cases,model):
    by_id={c['id']:c for c in cases['cases']};results=[]
    for item in model['results']:
        case=by_id[item['id']];r={'id':item['id'],'kind':item['kind'],'elapsed_seconds':item.get('elapsed_seconds'),'possible_token_cap':item.get('possible_token_cap',False)}
        if 'error' in item:r.update(status='failed',error=item['error']);results.append(r);continue
        output=item['text']
        if item['kind']=='column_value' or case['kind']=='cell':
            r['value_exact']=plain(output).strip('`\"\' .')==plain(case['expected']);r['status']='passed' if r['value_exact'] and not r['possible_token_cap'] else 'failed'
        elif case['kind']=='table':
            actual,supported=grid(output);expected=case['reference_grid'];shape=supported and len(actual)==len(expected) and all(len(row)==len(expected[0]) for row in actual)
            r['expected_shape']=[len(expected),len(expected[0])];r['actual_row_widths']=[len(row) for row in actual];r['shape_exact']=shape
            r['data_cells_exact']=shape and all(plain(a)==plain(b) for a,b in zip(actual[1],expected[1]))
            r['gna_cell_exact']=shape and plain(actual[1][case['expected_gna_column']])==plain(case['expected'])
            r['status']='passed' if r['shape_exact'] and r['data_cells_exact'] and not r['possible_token_cap'] else 'failed'
        else:
            reference=case['source_line_reference'];a=notation(output) if case['kind']=='subscript' else output;b=notation(reference) if case['kind']=='subscript' else reference
            r['whole_line_exact']=plain(a)==plain(b)
            digits=lambda s:Counter(re.findall(r'\d+(?:[./-]\d+)*',s))
            r['unexpected_numeric_tokens']=list((digits(a)-digits(b)).elements())
            r['missing_numeric_tokens']=list((digits(b)-digits(a)).elements())
            r['status']='passed' if r['whole_line_exact'] and not r['possible_token_cap'] else 'failed'
            r['reference_scope']=case.get('reference_scope')
        results.append(r)
    return {'model':model['model'],'revision':model['revision'],'offline':model['offline'],'cloudflare_operations':0,
        'passed':sum(r['status']=='passed' for r in results),'failed':sum(r['status']=='failed' for r in results),
        'results':results,'scope':'bounded source-case checks; not general accuracy, independent legal validation or training results'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);args=p.parse_args();cases=json.loads((args.root/'cases.json').read_text());reports=[]
    for name in ('paddle','qwen'):
        file=args.root/(name+'.json')
        if file.exists():reports.append(grade(cases,json.loads(file.read_text())))
    report={'cases':len(cases['cases']),'models':reports,'cloudflare_operations':0,'automatic_publication':False}
    (args.root/'grading.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for r in reports:print(r['model'],r['passed'],'passed,',r['failed'],'failed')
