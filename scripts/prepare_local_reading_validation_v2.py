"""Fix the evaluation ROI: complete source lines, not clipped neighbour glyphs."""
from pathlib import Path
import json,hashlib,shutil
import pdfplumber,pypdfium2 as pdfium
from PIL import Image,ImageOps
from local_vector_batch.native_consensus import read_source_lines
from local_vector_batch.l1_review import preview_tables

old=Path('out/local-vlm-validation');out=Path('out/local-vlm-validation-v2').resolve();out.mkdir(exist_ok=True)
data=json.loads((old/'cases.json').read_text());root=Path('out/legal-l1-v1/documents').resolve()
for case in data['cases']:
    if case['kind'] in {'date','subscript'}:
        folder='cerc-amount-conflict' if case['kind']=='date' else 'sharing-july-em'
        source=root/folder/'original.pdf';needle=case.get('expected') if case['kind']=='date' else 'power under GNA'
        with pdfplumber.open(source) as pdf:
            p=pdf.pages[case['page']-1];w=read_source_lines(source,p,case['page'])
            matches=[line for line in w['lines'] if needle in line['text']]
            if len(matches)!=1:raise ValueError('ambiguous source line')
            line=matches[0];x0,y0,x1,y1=line['bbox'];width,height=p.width,p.height
            case['source_line_reference']=line['text'].strip()
            case['reference_scope']='machine source-anchored whole-line reference plus human-reviewed target; not an independent full legal transcription'
        with pdfium.PdfDocument(source) as pdf:
            p=pdf[case['page']-1]
            try:im=p.render(scale=300/72).to_pil().convert('RGB')
            finally:p.close()
        # Keep source vertical bounds tight; add white canvas outside the crop,
        # rather than including sliced glyphs from neighbouring physical lines.
        crop=im.crop((int(x0/width*im.width),int(y0/height*im.height),int(x1/width*im.width)+1,int(y1/height*im.height)+1))
        path=out/(case['id']+'.png');ImageOps.expand(crop,border=12,fill='white').save(path)
        case['source_boxes']=[[x0/width,y0/height,x1/width,y1/height]]
        case['crop_policy']='complete_physical_line_with_external_white_padding'
    else:
        path=out/(case['id']+'.png');shutil.copyfile(case['image'],path)
    case['image']=str(path);case['image_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    if case['kind']=='table':
        tables=preview_tables(root/'sharing-october-em/original.pdf',5,case['source_pdf_sha256'])
        t=tables[0 if case['id'].startswith('illustration-1') else 1]
        case['reference_grid']=[['' for _ in range(t['column_count'])] for _ in range(t['row_count'])]
        for c in t['cell_spans']:case['reference_grid'][c['row']][c['column']]=c['text'] or ''
(out/'cases.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print('Prepared 7 cases with complete source-line crops and explicit table grids')
