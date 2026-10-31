"""Source-coordinate layout visualization; never changes readings or quality gates."""
from pathlib import Path
from html import escape
import hashlib
import json
import math
import shutil

PALETTE = {
    'title': ('Title', '#7c3aed'), 'subtitle': ('Subtitle', '#a855f7'),
    'metadata': ('Metadata', '#0d9488'), 'quotation': ('Quotation', '#b45309'),
    'numbered_paragraph': ('Numbered paragraph', '#1d4ed8'),
    'heading': ('Heading', '#8b5cf6'), 'text': ('Text', '#2563eb'),
    'table': ('Table', '#059669'), 'list': ('List', '#d97706'),
    'formula': ('Formula', '#db2777'), 'figure': ('Figure', '#ca8a04'),
    'caption': ('Caption', '#0891b2'), 'header': ('Header', '#0e7490'),
    'footer': ('Footer', '#64748b'), 'page_number': ('Page no.', '#475569'),
    'unresolved': ('Review region', '#e11d48'),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def category(kind):
    if kind in ('title','doc_title'):return 'title'
    if kind=='subtitle':return 'subtitle'
    if kind in ('metadata','masthead'):return 'metadata'
    if kind=='quotation':return 'quotation'
    if kind=='numbered_paragraph':return 'numbered_paragraph'
    if kind in ('section_header','heading','paragraph_title'):return 'heading'
    if kind in ('table','table_cell'):return 'table'
    if kind in ('list_item','list'):return 'list'
    if kind in ('formula','equation','formula_number'):return 'formula'
    if kind in ('picture','chart','image','figure'):return 'figure'
    if kind in ('caption','figure_title','table_caption'):return 'caption'
    if kind in ('header','page_header'):return 'header'
    if kind in ('footer','page_footer'):return 'footer'
    if kind=='page_number':return 'page_number'
    if kind in ('unresolved','unresolved_full_page','document_index'):return 'unresolved'
    return 'text'


def normalized(box,width,height):
    if (any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)) or
        not isinstance(box,(list,tuple)) or len(box)!=4 or
        any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or
        not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height):
        raise ValueError('overlay geometry is outside the original physical page')
    return [box[0]/width,box[1]/height,box[2]/width,box[3]/height]


def build_overlays(data,source_document=None):
    """Use existing detected boxes or exact native source-line evidence only."""
    from .document_blocks import line_evidence,union,render_blocks,compact as compact_metadata
    if source_document and source_document.get('source_pdf_sha256',data['source_pdf_sha256'])!=data['source_pdf_sha256']:
        raise ValueError('overlay layout belongs to a different source PDF')
    pages=[];blocks=data.get('document_blocks',{}).get('blocks',[])
    model={n['id']:n for n in data.get('document_model',{}).get('elements',[])}
    metadata=data.get('document_model',{}).get('metadata',{}).get('fields',[])
    layouts={p['page']:p for p in (source_document['pages'] if source_document else data.get('layout',[]))}
    quality={r['page']:r for r in data.get('quality',{}).get('results',[])}
    from .layout_furniture import PAGE_LABEL,observed_margins,furniture_candidates,formatting
    repeated=observed_margins(data.get('layout',[]))
    def preview(block,text,kind):
        if block and block['kind'] not in ('footer','header','alternative_reading'):
            shown={**block}
            if category(kind)=='list':shown['kind']='list_item'
            subset={**data,'document_blocks':{'blocks':[shown],'unresolved_tables':[]}}
            return render_blocks(subset,id_prefix='overlay-preview-')[1]
        return '<p class="source-paragraph">'+escape(text)+'</p>' if text else ''
    for page in data['pages']:
        number=page['page'];layout=layouts.get(number,{});regions=[];unlocated=[]
        if source_document:
            width,height=layout['page_size_points']
        else:
            width,height=layout.get('width'),layout.get('height')
        if not width or not height:
            pages.append(dict(page=number,regions=[],unlocated_blocks=['page_dimensions_unavailable']))
            continue
        def add(identity,kind,box,text='',**meta):
            try:bounds=normalized(box,width,height)
            except ValueError:
                unlocated.append(identity);return
            regions.append(dict(id=identity,type=category(kind),source_kind=kind,bbox=box,
                                bbox_normalized=bounds,text=text,content_verified=False,**meta))
        if source_document:
            by_region={b['region_id']:b for b in blocks if 'region_id' in b}
            raw_by_region={b['region_id']:b for b in page.get('blocks',[])}
            failed=set(page.get('failed_regions',[]))
            for region in layout['regions']:
                block=by_region.get(region['id']);raw=raw_by_region.get(region['id'],{})
                kind=block['kind'] if block and block['kind'] in ('heading','list_item','table') else region['kind']
                add(region['id'],kind,region['bbox'],raw.get('text',''),
                    preview_html=preview(block,raw.get('text',''),kind),
                    block_id=block['id'] if block else None,
                    order=region.get('reading_order_index'),geometry_basis='source_detector_box',
                    state='reading_failed' if region['id'] in failed else 'visual_review' if region['kind'] in ('picture','chart','document_index') else 'candidate',
                    confidence=region.get('score'),recovery_parent_region=region.get('recovery_parent_region'))
        else:
            for block in blocks:
                if block['kind'] in ('whitespace','alternative_reading'):continue
                spans=[s for s in block['spans'] if s['page']==number]
                if not spans:continue
                text=''.join(s['exact_quote'] for s in spans)
                box=block.get('bbox') if len({s['page'] for s in block['spans']})==1 else None
                basis='native_source_block'
                if not box:
                    lines=[v for v in text.splitlines() if v.strip()]
                    evidence=[line_evidence(v,layout).get('bbox') for v in lines]
                    if evidence and all(evidence):box=union(evidence);basis='exact_unique_source_lines'
                if not box:
                    unlocated.append(block['id']);continue
                candidate=model.get(block['id'],{});shown_kind=candidate.get('role_candidate',block['kind'])
                fields=[f for f in metadata if f['scope']=='document_header' and f.get('geometry') and f['source_span']['page']==number and
                        any(s['page']==number and s['char_start']<=f['source_span']['char_start'] and f['source_span']['char_start']+f['source_span']['char_length']<=s['char_start']+s['char_length'] for s in block['spans'])]
                fields.sort(key=lambda f:f['source_span']['char_start'])
                if shown_kind=='metadata' and fields and compact_metadata(text)==compact_metadata(''.join(f['source_span']['exact_quote'] for f in fields)):
                    for field in fields:
                        literal=field['source_span']['exact_quote']
                        add(field['id'],'metadata',field['geometry']['bbox'],literal,block_id=block['id'],
                            order=block.get('order'),preview_html='<p class="source-paragraph">'+escape(literal)+'</p>',
                            geometry_basis=field['geometry']['basis'],state='candidate',confidence=None,
                            field_role=field['role_candidate'],display_label=field['role_candidate'].replace('_',' ').capitalize(),
                            field_value=field['value'],normalized_value=field.get('normalization',{}).get('iso'))
                    continue
                add(block['id'],shown_kind,box,text,block_id=block['id'],order=block.get('order'),
                    preview_html=preview(block,text,block['kind']),geometry_basis=basis,state='candidate',confidence=None,
                    role_candidate=shown_kind,inline_candidates=candidate.get('inline_candidates',[]),
                    font_names=block.get('font_names',[]),bold_candidate=block.get('bold_candidate'))
        for r in regions:
            if r['type'] in ('header','footer') and PAGE_LABEL.fullmatch(r['text'].strip()):r['type']='page_number'
        if not source_document:
            for i,observation in enumerate(furniture_candidates(layout,regions,repeated,page.get('text',''))):
                add(f'margin-p{number}-{i}',observation['kind'],observation['bbox'],observation['text'],
                    preview_html='<p class="source-paragraph">'+escape(observation['text'])+'</p>',
                    geometry_basis=observation['basis'],state='candidate' if observation['parsed_text_aligned'] else 'source_glyph_observation_unaligned',
                    confidence=None,order=None,parsed_text_aligned=observation['parsed_text_aligned'],font_names=observation['font_names'])
        formatting(regions,width,height)
        for formula in data.get('document_model',{}).get('formula_observations',{}).get('formulas',[]):
            if formula['source_span']['page']!=number:continue
            geometry=formula.get('geometry')
            if not geometry:continue
            checked=formula.get('computed_value');unit=formula.get('result_unit_literal') or ''
            rendered='<p class="source-paragraph">'+escape(formula['literal'])+'</p>'
            if checked is not None:rendered+='<p>Checked result: '+escape(checked+' '+unit)+'</p>'
            add(formula['id'],'formula',geometry['bbox'],formula['literal'],preview_html=rendered,order=None,confidence=None,
                geometry_basis=geometry['basis'],state='candidate',display_label='Worked formula' if formula['kind']=='worked_numeric_equality' else 'Formula candidate',
                arithmetic_status=formula['arithmetic_status'],variable_bindings_verified=False)
        for i,region in enumerate(quality.get(number,{}).get('unresolved_visual_regions',[])):
            box=region.get('bbox_normalized')
            if box:
                add(f'review-p{number}-{i}','unresolved',[box[0]*width,box[1]*height,box[2]*width,box[3]*height],
                    geometry_basis='source_quality_raster',order=None,confidence=None,state='needs_review',
                    reason=region.get('classification') or 'unaccounted_source_region')
        pages.append(dict(page=number,width=width,height=height,regions=regions,unlocated_blocks=unlocated))
    return dict(schema_version='pdf2data.overlays.v1',source_pdf_sha256=data['source_pdf_sha256'],
                document_type=data.get('source',{}).get('declared',{}).get('family','unknown'),
                palette={k:dict(label=v[0],color=v[1]) for k,v in PALETTE.items()},pages=pages,
                classification_verified=False,recognition_rerun=False,publication_eligible=False)


def write_overlays(stage,data,source_document=None):
    record=build_overlays(data,source_document)
    (stage/'overlays.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    for name in ('overlays.css','overlays.js'):
        shutil.copyfile(Path(__file__).with_name(name),stage/'assets'/name)
    return record


def viewer_controls(record):
    payload=json.dumps(record,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return '''<div class="overlay-tools" aria-label="Layout overlays"><label><input id="overlay-enabled" type="checkbox" checked> Layout</label><label><input id="overlay-labels" type="checkbox" checked> Labels</label><label class="overlay-opacity">Opacity <input id="overlay-opacity" type="range" min="5" max="35" value="14" aria-label="Overlay opacity"></label><span class="overlay-note">Detected candidates</span><div id="overlay-legend" class="overlay-legend" aria-label="Structure types"></div></div><script type="application/json" id="overlay-data">'''+payload+'''</script>'''


def visualize_bundle(bundle,output,pages=None,opacity=.14):
    """CLI PNG previews of saved overlays on verified original source images."""
    from PIL import Image,ImageDraw,ImageFont
    bundle=Path(bundle).resolve();output=Path(output).absolute()
    if output.exists() or output.is_symlink():raise ValueError('visualization output must be a new directory')
    if not .05<=opacity<=.35:raise ValueError('overlay opacity must be .05..0.35')
    manifest=json.loads((bundle/'manifest.json').read_text())
    if not {'source.pdf','overlays.json'}<=set(manifest['files']):raise ValueError('overlay bundle identity is not in manifest')
    for name,expected in manifest['files'].items():
        path=(bundle/name).resolve()
        if not path.is_relative_to(bundle) or digest(path)!=expected:raise ValueError('bundle artifact changed: '+name)
    record=json.loads((bundle/'overlays.json').read_text())
    if digest(bundle/'source.pdf')!=record['source_pdf_sha256'] or manifest['source_pdf_sha256']!=record['source_pdf_sha256']:raise ValueError('overlay original PDF differs')
    available={p['page'] for p in record['pages']}
    chosen=available if pages is None else set(pages)
    if not chosen or chosen-available:raise ValueError('requested overlay page does not exist')
    if any(f'assets/page-{n:04d}.png' not in manifest['files'] for n in chosen):raise ValueError('source page image is not in manifest')
    output.mkdir(parents=True);artifacts=[]
    font_path='/System/Library/Fonts/Supplemental/Arial.ttf'
    for page in record['pages']:
        if page['page'] not in chosen:continue
        source=bundle/'assets'/f'page-{page["page"]:04d}.png'
        with Image.open(source) as source_image:image=source_image.convert('RGBA')
        layer=Image.new('RGBA',image.size);draw=ImageDraw.Draw(layer)
        size=max(12,round(image.width/85));font=ImageFont.truetype(font_path,size) if Path(font_path).is_file() else ImageFont.load_default()
        for i,region in enumerate(page['regions'],1):
            x0,y0,x1,y1=region['bbox_normalized'];box=[round(x0*image.width),round(y0*image.height),round(x1*image.width),round(y1*image.height)]
            color=record['palette'][region['type']]['color'];rgb=tuple(int(color[j:j+2],16) for j in (1,3,5))
            draw.rectangle(box,fill=(*rgb,round(255*opacity)),outline=(*rgb,230),width=max(1,round(image.width/750)))
            label=f'{i} '+(region.get('display_label') or record['palette'][region['type']]['label'])
            tx,ty=box[0],max(0,box[1]-size-5);bounds=draw.textbbox((tx+3,ty+1),label,font=font)
            draw.rectangle((tx,ty,min(image.width,bounds[2]+4),bounds[3]+2),fill=(*rgb,235));draw.text((tx+3,ty+1),label,font=font,fill='white')
        painted=Image.alpha_composite(image,layer).convert('RGB')
        canvas=Image.new('RGB',(image.width,image.height+size*7),'#f7f8fb');canvas.paste(painted,(0,size*5))
        pen=ImageDraw.Draw(canvas);pen.text((12,8),f'PDF2Data / Source layout / Page {page["page"]}',font=font,fill='#243044')
        x,y=12,size*2+10
        for key in dict.fromkeys(r['type'] for r in page['regions']):
            value=record['palette'][key];label=value['label'];advance=round(pen.textlength(label,font=font))+34
            if x+advance>image.width:x=12;y+=size+7
            pen.rectangle((x,y+2,x+10,y+12),fill=value['color']);pen.text((x+16,y),label,font=font,fill='#243044');x+=advance
        pen.text((12,canvas.height-size-8),'Candidate regions; classification and source content require review.',font=font,fill='#576475')
        name=f'page-{page["page"]:04d}.overlay.png';canvas.save(output/name)
        artifacts.append(dict(page=page['page'],path=name,sha256=digest(output/name),source_image_sha256=digest(source),regions=len(page['regions'])))
    receipt=dict(schema_version='pdf2data.overlay-preview.v1',source_pdf_sha256=record['source_pdf_sha256'],
                 bundle_manifest_sha256=digest(bundle/'manifest.json'),opacity=opacity,palette=record['palette'],
                 artifacts=artifacts,recognition_rerun=False,publication_eligible=False)
    (output/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return dict(output=str(output),pages=len(artifacts),images=[str(output/a['path']) for a in artifacts],recognition_rerun=False)
