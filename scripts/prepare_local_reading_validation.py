from pathlib import Path
import json,hashlib
import pypdfium2 as pdfium
from local_vector_batch.review_queue import load_item
from local_vector_batch.l1_review import preview_tables

root=Path('out/legal-l1-v1/documents').resolve();review=Path('out/legal-l1-review/review-manifest.jsonl').resolve();out=Path('out/local-vlm-validation').resolve();out.mkdir(exist_ok=True)
cases=[]
def crop(source,page,boxes,key):
    from PIL import Image
    with pdfium.PdfDocument(source) as pdf:
        p=pdf[page-1]
        try:im=p.render(scale=300/72).to_pil().convert('RGB')
        finally:p.close()
    images=[im.crop((int(a*im.width),int(b*im.height),int(c*im.width),int(d*im.height))) for a,b,c,d in boxes]
    joined=Image.new('RGB',(max(x.width for x in images),sum(x.height for x in images)+20*(len(images)-1)),'white');y=0
    for image in images:joined.paste(image,(0,y));y+=image.height+20
    path=out/(key+'.png');joined.save(path)
    return str(path),hashlib.sha256(path.read_bytes()).hexdigest()
for i,expected in [(0,'23.12.2014'),(1,'24.2.2010')]:
    item=load_item(review,i);assert item['focus']['kind']=='date'
    key='reviewed-date-'+str(item['page']);image,sha=crop(Path(item['source_pdf']),item['page'],item['focus']['boxes'],key)
    cases.append({'id':key,'kind':'date','image':image,'image_sha256':sha,'source_pdf_sha256':item['source_pdf_sha256'],'page':item['page'],'source_boxes':item['focus']['boxes'],'expected':expected,'reference_basis':'human selected reading in saved focused review','max_tokens':256})
# Source-bound full physical line containing the raised/lowered RE tokens.
import pdfplumber
p=root/'sharing-july-em/original.pdf'
with pdfplumber.open(p) as pdf:
    page=pdf.pages[1]
    lines=[r for r in page.extract_text_lines() if 'power under' in r['text'] or r['text'].strip()=='RE, RE']
    a=min(x['x0'] for x in lines)/page.width;b=max(0,min(x['top'] for x in lines)/page.height-.006)
    c=max(x['x1'] for x in lines)/page.width;d=min(1,max(x['bottom'] for x in lines)/page.height+.006)
boxes=[[max(0,a-.008),b,min(1,c+.008),d]];image,sha=crop(p,2,boxes,'regulatory-subscripts')
cases.append({'id':'regulatory-subscripts','kind':'subscript','image':image,'image_sha256':sha,'source_pdf_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'page':2,'source_boxes':boxes,'expected_terms':['GNARE','T-GNARE'],'reference_basis':'source crop and reviewed independent-reading preference; notation is evaluated separately','max_tokens':256})
p=root/'sharing-october-em/original.pdf';source_sha=hashlib.sha256(p.read_bytes()).hexdigest();tables=preview_tables(p,5,source_sha)
for i,t in enumerate(tables):
    cell=next(c for c in t['cell_spans'] if c['row']==1 and c['column']==7)
    a,b,c,d=cell['bbox'];boxes=[[max(0,(a-.4)/t['page_width']),max(0,(b-.4)/t['page_height']),min(1,(c+.4)/t['page_width']),min(1,(d+.4)/t['page_height'])]]
    key='illustration-'+str(i+1)+'-gna-cell';image,sha=crop(p,5,boxes,key)
    cases.append({'id':key,'kind':'cell','image':image,'image_sha256':sha,'source_pdf_sha256':source_sha,'page':5,'source_boxes':boxes,'expected':cell['text'],'reference_basis':'exact source cell; distinct illustration context verified visually','max_tokens':128})
    a,b,c,d=t['bbox'];boxes=[[max(0,(a-1)/t['page_width']),max(0,(b-1)/t['page_height']),min(1,(c+1)/t['page_width']),min(1,(d+1)/t['page_height'])]]
    key='illustration-'+str(i+1)+'-table';image,sha=crop(p,5,boxes,key)
    cases.append({'id':key,'kind':'table','image':image,'image_sha256':sha,'source_pdf_sha256':source_sha,'page':5,'source_boxes':boxes,'expected':cell['text'],'expected_columns':9,'expected_rows':2,'expected_gna_column':7,'reference_basis':'two separate original tables, not competing reconstructions','max_tokens':1536})
(out/'cases.json').write_text(json.dumps({'scope':'local vision validation only; no Cloudflare operations or publication','cases':cases},indent=2)+'\n')
print('Prepared',len(cases),'source-bound image cases')
