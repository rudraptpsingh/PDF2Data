"""Source-only whitespace partitioning for failed visual readings.

No annotations, expected strings or document-specific identities are accepted.
Original failed regions/responses remain immutable and referenced in the receipt.
"""
from copy import deepcopy
from pathlib import Path
import math
import json
import re
import numpy as np
from PIL import Image
from .structured import load,write,sha,verify_page_source


def bands(mask):
    result=[];start=None
    for i,value in enumerate([*map(bool,mask),False]):
        if value and start is None:start=i
        if not value and start is not None:result.append((start,i));start=None
    return result


def text_rows(ink):
    active=ink.sum(axis=1)>=max(2,ink.shape[1]*.001)
    found=bands(active);merged=[]
    for a,b in found:
        if merged and a-merged[-1][1]<=3:merged[-1]=(merged[-1][0],b)
        else:merged.append((a,b))
    return merged


def split_pixels(ink,max_rows=4,max_parts=16,allow_columns=True):
    """Partition the entire rectangle at measured low-ink whitespace only."""
    height,width=ink.shape;columns=[(0,width)]
    if allow_columns and width>height*1.5:
        # Skewed lines in adjacent columns can overlap in a full-width row
        # projection. Test the centre of each potential column independently.
        # Requiring repeated rows on both sides avoids splitting short prose or
        # handwriting at an incidental gap between words.
        counts=ink.sum(axis=0);lo,hi=int(width*.47),int(width*.53)
        quiet=counts<=max(0,int(height*.002))
        gaps=[(a+lo,b+lo) for a,b in bands(quiet[lo:hi]) if b-a>=max(8,width*.006)]
        gaps=[(a,b) for a,b in gaps if all(
            len(text_rows(ink[:,x0+(x1-x0)//4:x1-(x1-x0)//4]))>=8
            for x0,x1 in ((0,(a+b)//2),((a+b)//2,width)))]
        if gaps:
            a,b=min(gaps,key=lambda g:(abs((g[0]+g[1])/2-width/2),-(g[1]-g[0])))
            cut=(a+b)//2;columns=[(0,cut),(cut,width)]
    parts=[]
    for x0,x1 in columns:
        rows=text_rows(ink[:,x0:x1]);cuts=[0]
        batch=max_rows if len(rows)>max_rows else max(1,math.ceil(len(rows)/2))
        for i in range(batch,len(rows),batch):
            # Two pixels of context on each side must remain inside the gap.
            a,b=rows[i-1][1],rows[i][0]
            if b-a>=6:cuts.append((a+b)//2)
        cuts.append(height)
        parts.extend([x0,y0,x1,y1] for y0,y1 in zip(cuts,cuts[1:]))
    if len(parts)>max_parts:return [[0,0,width,height]]
    return parts


def recover_layout(layout_path,read_root,output,max_rows=4,max_parts=16):
    if not 1<=max_rows<=20 or not 2<=max_parts<=64:raise ValueError('invalid recovery partition limits')
    output=Path(output).resolve();read_root=Path(read_root).resolve()
    if output.exists():raise ValueError('recovery output already exists')
    layout=load(layout_path);parent=load(read_root/'read.json')
    if parent['layout_sha256']!=sha(Path(layout_path).read_bytes()):raise ValueError('reading/layout identity differs')
    failed={};files={}
    for file in sorted(read_root.glob('*/readings.json')):
        files[str(file.relative_to(read_root))]=sha(file.read_bytes())
        for reading in load(file):
            if reading['status']=='reading_failed':failed[reading['region_id']]=reading
    updated=deepcopy(layout);decisions=[]
    # Verify original input pixels before creating any output directory.
    for document in layout['documents']:
        if sha(Path(document['source_pdf_path']).read_bytes())!=document['source_pdf_sha256']:raise ValueError('original PDF changed')
        for page in document['pages']:
            if any(r['id'] in failed for r in page['regions']):verify_page_source(document,page)
    output.mkdir(parents=True)
    for document in updated['documents']:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',document['id']):raise ValueError('unsafe document identity')
        for page in document['pages']:
            replacement=[];retired=[]
            with Image.open(page['source_image']) as source:
                pw,ph=page['page_size_points']
                for region in page['regions']:
                    if region['id'] not in failed:replacement.append(region);continue
                    reading=failed[region['id']]
                    if (reading['source_pdf_sha256']!=document['source_pdf_sha256'] or
                        reading['source_crop_sha256']!=region['crop_sha256'] or reading['page']!=page['page']):raise ValueError('failed crop binding changed')
                    bx=region['bbox'];core=[math.floor(bx[0]/pw*source.width),math.floor(bx[1]/ph*source.height),math.ceil(bx[2]/pw*source.width),math.ceil(bx[3]/ph*source.height)]
                    with source.crop(core) as crop:ink=np.asarray(crop.convert('L'))<100
                    parts=split_pixels(ink,max_rows,max_parts,region['kind']!='table')
                    decision=dict(region_id=region['id'],source_pdf_sha256=document['source_pdf_sha256'],
                                  original_crop_sha256=region['crop_sha256'],prior_failure=reading.get('failure_reason',reading.get('error')),
                                  fragments=[],status='no_safe_partition')
                    if len(parts)==1:replacement.append(region);decisions.append(decision);continue
                    folder=output/document['id'];folder.mkdir(exist_ok=True)
                    for i,(x0,y0,x1,y1) in enumerate(parts):
                        box=[bx[0] if x0==0 else (core[0]+x0)/source.width*pw,
                             bx[1] if y0==0 else (core[1]+y0)/source.height*ph,
                             bx[2] if x1==ink.shape[1] else (core[0]+x1)/source.width*pw,
                             bx[3] if y1==ink.shape[0] else (core[1]+y1)/source.height*ph]
                        bounds=[max(0,math.floor(box[0]/pw*source.width)-2),max(0,math.floor(box[1]/ph*source.height)-2),min(source.width,math.ceil(box[2]/pw*source.width)+2),min(source.height,math.ceil(box[3]/ph*source.height)+2)]
                        path=folder/f'p{page["page"]}-{region["id"].split(":")[-1]}-part{i}.png'
                        source.crop(bounds).save(path)
                        child={**region,'id':region['id']+f'-part{i}','bbox':box,'crop_bbox_pixels':bounds,
                               'crop_path':str(path),'crop_sha256':sha(path.read_bytes()),'recovery_parent_region':region['id'],
                               'recovery_kind':'source_whitespace_partition','table_fragment_of':region['id'] if region['kind']=='table' else None,
                               'order_verified':False}
                        replacement.append(child);decision['fragments'].append(dict(id=child['id'],bbox=box,crop_sha256=child['crop_sha256']))
                    area=sum((c['bbox'][2]-c['bbox'][0])*(c['bbox'][3]-c['bbox'][1]) for c in decision['fragments'])
                    if not math.isclose(area,(bx[2]-bx[0])*(bx[3]-bx[1]),rel_tol=1e-8):raise ValueError('partition lost source area')
                    retired.append(region);decision['status']='partitioned';decisions.append(decision)
            for i,region in enumerate(replacement):region['reading_order_index']=i
            page['regions']=replacement;page['retired_failed_regions']=retired
    updated['recovery']=dict(schema_version='pdf2data.region-recovery.v1',
        parent_layout_sha256=sha(Path(layout_path).read_bytes()),parent_read_sha256=sha((read_root/'read.json').read_bytes()),
        parent_read_path=str(read_root),parent_reading_files_sha256=files,max_rows=max_rows,max_parts=max_parts,
        implementation_sha256=sha(Path(__file__).read_bytes()),decisions=decisions,publication_eligible=False)
    write(output/'layout.json',updated)
    return dict(output=str(output/'layout.json'),failed_regions=len(failed),partitioned=sum(d['status']=='partitioned' for d in decisions),
                new_fragments=sum(len(d['fragments']) for d in decisions),recognition_rerun=False)
