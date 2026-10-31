"""Selected structured-table versions from exact reviewed source-cell literals.

Original native pages and raw table/glyph readings remain separate and intact.
This applies processed review output to exported structured data; it does not
turn source/table candidates into verified law or approve whole-page quality.
"""
import argparse
from copy import deepcopy
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil


def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v):return hashlib.sha256(v if isinstance(v,bytes)else v.encode()).hexdigest()
def load(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def normalized(v):return ' '.join(v.split())


class TableRows(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        elif tag in('td','th'):self.cell=[]
        elif tag=='br'and self.cell is not None:self.cell.append('\n')
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in('td','th')and self.cell is not None:
            if self.row is None:raise ValueError('Model cell outside row')
            self.row.append(normalized(''.join(self.cell)));self.cell=None
        elif tag=='tr'and self.row is not None:self.rows.append(self.row);self.row=None


def _model_literal(candidate,row,source_table=None):
    literal=candidate['interpretation']['literal_text']
    if row.get('status')!='processed_candidate'or row.get('possible_token_cap')is not False:
        raise ValueError('Model review is incomplete/capped')
    if row.get('source_quality_clearance')is not False or row.get('review_origin')!='local_vision_model':
        raise ValueError('Model review origin/promotion is unsupported')
    if row['kind']=='table':
        parser=TableRows();parser.feed(row['text'])
        accepted={normalized(literal)}
        if source_table is not None:
            first=min(candidate['original_cells'],key=lambda c:(c['row'],c['column']))
            # A model may combine adjacent printed identifier/label columns.
            # Only exact existing same-row source cells can supply that prefix.
            prefix=[c['text']for c in sorted(source_table['cell_spans'],key=lambda c:c['column'])
                    if c['row']==first['row']and c['column']<first['column']and c['text']]
            if prefix:accepted.add(normalized(' '.join(prefix+[literal])))
        hits=[r for r in parser.rows if any(value in r for value in accepted)]
        if len(hits)!=1:raise ValueError('Reviewed full literal is absent/ambiguous in model table')
        for association in candidate['interpretation'].get('associations',[]):
            if normalized(association['source_literal'])not in hits[0]:raise ValueError('Model row/value association differs')
    elif normalized(row['text'])!=normalized(literal):
        raise ValueError('Model full literal differs from source-reviewed interpretation')
    return {'origin':'local_vision_model','literal_agreement':'exact_nonwhitespace_codepoints',
            'source_pdf_sha256':row['source_pdf_sha256'],'page':row['page'],
            'source_crop_sha256':row['image_sha256'],'model_reading_sha256':sha(canonical(row)),
            'model_quality_approval':False}


def _source_model_crop(bundle,case,row):
    import pdfplumber
    from PIL import Image
    path=Path(bundle)/case['image']
    if sha(path.read_bytes())!=case['image_sha256']or case['image_sha256']!=row['image_sha256']:
        raise ValueError('Saved model input crop changed')
    source=Path(bundle)/case['source_pdf']
    if sha(source.read_bytes())!=case['source_pdf_sha256']:raise ValueError('Original model source PDF changed')
    for key in('id','source_pdf_sha256','page','bbox','dpi','kind'):
        if case[key]!=row[key]:raise ValueError('Model input/readout source binding differs: '+key)
    with pdfplumber.open(source)as pdf:
        actual=pdf.pages[case['page']-1].crop(tuple(case['bbox'])).to_image(resolution=case['dpi']).original.convert('RGB')
    with Image.open(path)as image:expected=image.convert('RGB')
    if actual.size!=expected.size or actual.tobytes()!=expected.tobytes():
        raise ValueError('Model input does not reproduce original source pixels')
    return {'source_image_rgb_sha256':sha(actual.tobytes()),'dimensions':list(actual.size),
            'raw_PNG_sha256':case['image_sha256'],'source_pixels_rederived':True}


def _partition(table):
    text='';parts=[];byrc={(c['row'],c['column']):c for c in table['cell_spans']}
    for row in range(table['row_count']):
        for col in range(table['column_count']):
            cell=byrc.get((row,col))
            if cell is not None:
                value=cell['text'];start=len(text);text+=value
                cell['table_text_span']={'representation_id':table['table_version_id'],'char_start':start,
                    'char_length':len(value),'exact_quote':value,'offset_encoding':'codepoint'}
                parts.append({'kind':'cell_literal','cell_id':cell['id'],**cell['table_text_span']})
            if col<table['column_count']-1:
                parts.append({'kind':'column_separator','char_start':len(text),'char_length':1,'exact_quote':'\t','offset_encoding':'codepoint'});text+='\t'
        if row<table['row_count']-1:
            parts.append({'kind':'row_separator','char_start':len(text),'char_length':1,'exact_quote':'\n','offset_encoding':'codepoint'});text+='\n'
    return {'representation_id':table['table_version_id'],'source_pdf_sha256':table['source_pdf_sha256'],
            'page':table['page'],'text':text,'text_sha256':sha(text),'spans':parts,
            'representation_kind':'selected_reviewed_structured_table_not_native_page_text'}


def _html(table):
    byrow={}
    for cell in table['cell_spans']:byrow.setdefault(cell['row'],[]).append(cell)
    body=[]
    for row in range(table['row_count']):
        cells=[]
        for cell in sorted(byrow.get(row,[]),key=lambda c:c['column']):
            value=cell['text'];cursor=0;segments=[]
            for style in sorted(cell.get('typography',[]),key=lambda s:s['start']):
                start,end=style['start'],style['end']
                if start<cursor or value[start:end]!=style['literal']:raise ValueError('Invalid selected-table typography')
                segments.extend([escape(value[cursor:start]),'<sup>'+escape(value[start:end])+'</sup>']);cursor=end
            segments.append(escape(value[cursor:]));printed=''.join(segments).replace('\n','<br>')
            cells.append(f'<td rowspan="{cell["row_span"]}" colspan="{cell["column_span"]}">{printed}</td>')
        body.append('<tr>'+''.join(cells)+'</tr>')
    return '<table data-source-pdf-sha256="'+table['source_pdf_sha256']+'" data-selected-table-version="'+table['table_version_id']+'"><tbody>'+''.join(body)+'</tbody></table>'


def derive(bundle):
    """Fresh source/crop/model validation once per bundle, never a page pass."""
    from .table_cell_interpretation import validate_interpretation
    from .l1_review import preview_tables
    bundle=Path(bundle);inputs=load(bundle/'inputs.json');feedback=[json.loads(x)for x in(bundle/'inputs/feedback-original.jsonl').read_text().splitlines()if x.strip()]
    model=load(bundle/'model-readings.json');original_cases=load(bundle/'model-cases-original.json')
    if model['cases_sha256']!=sha((bundle/'model-cases-original.json').read_bytes()):raise ValueError('Model cases receipt changed')
    cases=load(bundle/'portable-model-cases.json');byid={r['id']:r for r in model['results']}
    if len(byid)!=len(model['results']):raise ValueError('Duplicate model review identity')
    tables={};cache={};applied=[];pixel_proofs=[]
    for entry in inputs['items']:
        candidate=load(bundle/entry['candidate']);snapshot=load(bundle/entry['snapshot']);q=entry['question']
        validate_interpretation(q,snapshot,feedback,candidate['interpretation'])
        native=load(bundle/entry['original_native_page'])
        if (native['page']!=candidate['page']or sha(native['text'])!=candidate['original_page_text_sha256']or
                native['text_sha256']!=candidate['original_page_text_sha256']):
            raise ValueError('Preserved original native page identity changed')
        case=next(c for c in cases if c['id']==q['focus_key']);row=byid[q['focus_key']]
        if case['source_pdf_sha256']!=candidate['source_pdf_sha256']or case['page']!=candidate['page']:
            raise ValueError('Interpreted literal/model source scope differs')
        original=next(t for t in snapshot['snapshot']['tables']if t['table_version_id']==q['table_version_id'])
        pixels=_source_model_crop(bundle,case,row);model_proof=_model_literal(candidate,row,original)
        model_proof.update(runtime=model.get('runtime'),model_receipt_sha256=sha((bundle/'model-readings.json').read_bytes()),
                           original_model_case_manifest_sha256=model['cases_sha256'])
        key=(case['source_pdf_sha256'],case['page'])
        if key not in cache:
            cache[key]=preview_tables(bundle/case['source_pdf'],case['page'],case['source_pdf_sha256'])
        matches=[t for t in cache[key]if t['table_geometry_sha256']==candidate['table_geometry_sha256']]
        if len(matches)!=1:raise ValueError('Original table geometry does not rederive uniquely')
        fresh=matches[0]
        fields=('row','column','row_span','column_span','bbox','text')
        if [{k:c[k]for k in fields}for c in fresh['cell_spans']]!=[{k:c[k]for k in fields}for c in original['cell_spans']]:
            raise ValueError('Original selected table cells differ from fresh source')
        tablekey=(*key,fresh['table_geometry_sha256'])
        if tablekey not in tables:tables[tablekey]={'original':deepcopy(original),'selected':deepcopy(original),'reviews':[]}
        current=tables[tablekey];chosen=current['selected'];ids=candidate['interpretation']['source_cell_ids']
        cells=[c for c in chosen['cell_spans']if c['id']in ids]
        if len(cells)!=len(ids):raise ValueError('Review overlaps another corrected source cell')
        first=min(cells,key=lambda c:(c['row'],c['column']));replacement=deepcopy(first)
        replacement.update(text=candidate['interpretation']['literal_text'],original_source_cell_ids=ids,
            original_source_cells=deepcopy(cells),review_origin=candidate['interpretation']['review_origin'],
            user_clarification=candidate['interpretation']['user_clarification'],model_provenance=model_proof,
            typography=candidate['interpretation'].get('typography',[]),status='reviewed_literal_selected_source_quality_held',
            selected_literal_sha256=candidate['literal_sha256'])
        if candidate['interpretation']['action']=='logical_cell_continuation':
            replacement.update(row_span=sum(c['row_span']for c in cells),
                bbox=[min(c['bbox'][0]for c in cells),min(c['bbox'][1]for c in cells),max(c['bbox'][2]for c in cells),max(c['bbox'][3]for c in cells)])
        chosen['cell_spans']=[c for c in chosen['cell_spans']if c['id']not in ids]+[replacement]
        current['reviews'].append({'question_id':q['focus_key'],'candidate_sha256':sha((bundle/entry['candidate']).read_bytes()),
            'model_provenance':model_proof,'source_crop_proof':pixels,'source_cell_ids':ids})
        pixel_proofs.append({'question_id':q['focus_key'],**pixels});applied.append(q['focus_key'])
    result=[];representations=[]
    for key,current in sorted(tables.items()):
        table=current['selected'];basis={'original_table':current['original'],'reviews':current['reviews'],'selected_cells':table['cell_spans']}
        version='reviewed-table:'+sha(canonical(basis));table.update(table_version_id=version,source_pdf_sha256=key[0],
            selected_representation_kind='reviewed_source_literal_table_version',
            original_table_version_id=current['original']['table_version_id'],original_table= current['original'],
            applied_reviews=current['reviews'],source_quality_clearance=False,publication_eligible=False,
            selected_table_text_changed=True,raw_native_page_text_changed=False,table_semantics_reviewed=False,
            status='processed_selected_table_with_source_quality_holds')
        table['cell_spans'].sort(key=lambda c:(c['row'],c['column']))
        occupied=set();grid=[[None for _ in range(table['column_count'])]for _ in range(table['row_count'])]
        for cell in table['cell_spans']:
            cell['original_cell_id']=cell['id'];cell['id']=version+f":r{cell['row']}:c{cell['column']}"
            for r in range(cell['row'],cell['row']+cell['row_span']):
                for c in range(cell['column'],cell['column']+cell['column_span']):
                    if (r,c)in occupied or r>=table['row_count']or c>=table['column_count']:raise ValueError('Selected table slot overlap/clipping')
                    occupied.add((r,c))
            grid[cell['row']][cell['column']]=cell['text']
        if len(occupied)!=table['row_count']*table['column_count']:raise ValueError('Selected table omits source slots')
        table['cells']=grid;representations.append(_partition(table));result.append(table)
    return {'selected_tables':result,'table_text_representations':representations,
            'selected_html':'\n'.join(_html(t)for t in result),
            'applied_question_ids':applied,'source_pixel_proofs':pixel_proofs,
            'whole_page_quality_cleared':0,'source_native_layers_modified':False,
            'model_inference_calls':0,'publication_eligible':False}


def build(queue,interpretations,model_folder,output):
    output=Path(output)
    if output.exists():raise ValueError('Selected-table version must use a new immutable directory')
    queue,interpretations,model_folder=map(Path,(queue,interpretations,model_folder))
    rows=[json.loads(x)for x in(queue/'review-manifest.jsonl').read_text().splitlines()if x.strip()]
    receipt=load(interpretations/'receipt.json')
    for name,digest in receipt['files'].items():
        if sha((interpretations/name).read_bytes())!=digest:raise ValueError('Interpreted original evidence changed')
    output.mkdir(parents=True);shutil.copytree(interpretations,output/'inputs');(output/'originals').mkdir();(output/'model-images').mkdir();(output/'native-pages').mkdir();(output/'parent-receipts').mkdir()
    shutil.copyfile(model_folder/'qwen-readings.json',output/'model-readings.json')
    shutil.copyfile(model_folder/'cases.json',output/'model-cases-original.json')
    cases=deepcopy(load(model_folder/'cases.json')['cases']);items=[]
    for index,row in enumerate(rows,1):
        source_name='originals/'+row['source_pdf_sha256']+'.pdf';dest=output/source_name
        if not dest.exists():shutil.copyfile(row['source_pdf'],dest)
        candidate=f'inputs/candidate-{index:03}.json';snapshot=f'inputs/candidate-{index:03}-snapshot.json'
        q=deepcopy(row['target_cell']);q['source_snapshot_path']=snapshot
        native_path='native-pages/'+row['source_pdf_sha256']+f'-p{row["page"]}.json'
        if not(output/native_path).exists():
            native=next(json.loads(line)for line in(Path(row['l1_package'])/'extraction/pages.jsonl').read_text().splitlines()if json.loads(line)['page']==row['page'])
            save(output/native_path,native)
        for filename in('l1-package.json','source-receipt.json'):
            dest=output/'parent-receipts'/(row['source_pdf_sha256']+'-'+filename)
            if not dest.exists():shutil.copyfile(Path(row['l1_package'])/filename,dest)
        items.append({'candidate':candidate,'snapshot':snapshot,'question':q,'original_native_page':native_path,'source_native_page_text_modified':False})
    for index,case in enumerate(cases,1):
        image='model-images/'+str(index)+'.png';shutil.copyfile(case['image'],output/image)
        case['image']=image;case['source_pdf']='originals/'+case['source_pdf_sha256']+'.pdf'
    save(output/'portable-model-cases.json',cases);save(output/'inputs.json',{'items':items})
    result=derive(output);save(output/'selected-tables.json',result['selected_tables'])
    save(output/'table-text-representations.json',result['table_text_representations'])
    (output/'selected-tables.html').write_text(result['selected_html']+'\n')
    save(output/'applied-review-receipt.json',{k:v for k,v in result.items()if k not in('selected_tables','table_text_representations','selected_html')})
    manifest={'contract_version':'pdf2data.selected-reviewed-table-versions.v1','operation':'reviewed_source_literal_tables',
        'decoder_implementation_sha256':sha(Path(__file__).read_bytes()),
        'files':{str(p.relative_to(output)):sha(p.read_bytes())for p in sorted(output.rglob('*'))if p.is_file()},
        'source_quality_clearance':False,'publication_eligible':False,'source_native_layers_modified':False}
    save(output/'selected-table-version.json',manifest)
    return {'status':'processed','selected_tables':len(result['selected_tables']),'applied_reviews':len(result['applied_question_ids']),
        'new_whole_page_clears':0,'manifest_sha256':sha((output/'selected-table-version.json').read_bytes()),'publication_eligible':False}


def verify(bundle):
    bundle=Path(bundle);manifest=load(bundle/'selected-table-version.json')
    if manifest.get('contract_version')!='pdf2data.selected-reviewed-table-versions.v1'or manifest.get('source_quality_clearance')is not False:
        raise ValueError('Unsupported/promoted selected-table version')
    if manifest.get('decoder_implementation_sha256')!=sha(Path(__file__).read_bytes()):raise ValueError('Selected-table decoder epoch differs')
    if manifest.get('publication_eligible')is not False or manifest.get('source_native_layers_modified')is not False:
        raise ValueError('Selected-table representation was improperly promoted')
    if set(manifest['files'])!={str(p.relative_to(bundle))for p in bundle.rglob('*')if p.is_file()and p.name!='selected-table-version.json'}:
        raise ValueError('Selected-table artifact inventory changed')
    for name,digest in manifest['files'].items():
        if Path(name).is_absolute()or '..'in Path(name).parts or sha((bundle/name).read_bytes())!=digest:
            raise ValueError('Selected-table evidence artifact changed')
    result=derive(bundle)
    if (result['selected_tables']!=load(bundle/'selected-tables.json')or
        result['table_text_representations']!=load(bundle/'table-text-representations.json')or
        result['selected_html']+'\n'!=(bundle/'selected-tables.html').read_text()):
        raise ValueError('Selected-table output differs from fresh source/review derivation')
    return {'status':'verified','selected_tables':len(result['selected_tables']),
        'applied_reviews':len(result['applied_question_ids']),'source_pixel_proofs':result['source_pixel_proofs'],
        'new_whole_page_clears':0,'source_native_layers_modified':False,'publication_eligible':False,
        'selected_table_representation_verified':True,'whole_page_quality_verified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',type=Path);p.add_argument('--interpretations',type=Path)
    p.add_argument('--model-folder',type=Path);p.add_argument('--output',type=Path);p.add_argument('--verify',type=Path);a=p.parse_args()
    print(json.dumps(verify(a.verify)if a.verify else build(a.queue,a.interpretations,a.model_folder,a.output),ensure_ascii=False,indent=2))
