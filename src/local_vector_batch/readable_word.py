"""Optional editable Word export from the shared source model, never a PDF reader."""
import json,re,sys
from pathlib import Path


def export_word(value,output):
    from docx import Document
    from docx.shared import Pt,RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH,WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from local_vector_batch.document_blocks import validated_cells,validate_blocks
    from local_vector_batch.document_model import build_document_model,validate_document_model
    blocks=value.get('document_blocks')
    if not blocks:raise ValueError('structured Word export requires source document blocks')
    validate_blocks(blocks,value['pages'])
    model=value.get('document_model') or build_document_model(value)
    validate_document_model(model,value)
    nodes={n['id']:n for n in model['elements']};tables={t['id']:t for t in value['tables']}
    doc=Document();section=doc.sections[0];first=model['physical_pages'][0]
    section.page_width=Pt(first['width_points'] or 595.3);section.page_height=Pt(first['height_points'] or 841.9)
    section.top_margin=Pt(40);section.bottom_margin=Pt(56);section.left_margin=section.right_margin=Pt(54)
    section.header_distance=Pt(15);section.footer_distance=Pt(15)
    normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(11)
    normal.paragraph_format.space_after=Pt(4);normal.paragraph_format.line_spacing=1.05
    for name in ['Title','Subtitle','Heading 1','Heading 2','Heading 3','Caption']:
        doc.styles[name].font.name='Arial';doc.styles[name].font.color.rgb=RGBColor(0,0,0)
    modes={};inline_stories=set();used_tables=set();repeated_headers=0
    def story_template(story):
        per_page=[]
        for p in model['physical_pages']:
            members=[nodes[i] for i in model['stories'][story] if any(s['page']==p['page'] for s in nodes[i]['source_spans'])]
            if not members:return None
            text='\n'.join(n['text'].strip('\n') for n in members)
            if story=='footer':text=re.sub(r'\bPage\s+'+str(p['page'])+r'(?:\s+of\s+\d+)?\s*$',lambda m:'Page {PDF2DATA_PAGE}'+(' of {PDF2DATA_NUMPAGES}' if re.search(r'\bof\b',m.group(),re.I) else ''),text,flags=re.I)
            per_page.append(text)
        return per_page[0] if len(set(per_page))==1 else None
    for story in ('header','footer'):
        ids=model['stories'][story]
        if not ids:modes[story]='absent';continue
        template=story_template(story)
        if template is None:
            inline_stories.add(story);modes[story]='varying_source_content_preserved_inline';continue
        paragraph=getattr(section,story).paragraphs[0]
        paragraph.paragraph_format.space_after=Pt(0)
        paragraph.paragraph_format.tab_stops.add_tab_stop(section.page_width-section.left_margin-section.right_margin,WD_TAB_ALIGNMENT.RIGHT)
        if '{PDF2DATA_PAGE}' in template:
            template=re.sub(r'\s+Page \{PDF2DATA_PAGE\}',r'\tPage {PDF2DATA_PAGE}',template)
            for part in re.split(r'(\{PDF2DATA_(?:PAGE|NUMPAGES)\})',template):
                if part not in ('{PDF2DATA_PAGE}','{PDF2DATA_NUMPAGES}'):paragraph.add_run(part);continue
                run=paragraph.add_run();field='PAGE' if part=='{PDF2DATA_PAGE}' else 'NUMPAGES'
                for tag,attrs,text in [('w:fldChar',{'w:fldCharType':'begin'},None),('w:instrText',{},' '+field+' '),('w:fldChar',{'w:fldCharType':'separate'},None),('w:t',{},'1'),('w:fldChar',{'w:fldCharType':'end'},None)]:
                    element=OxmlElement(tag)
                    for key,v in attrs.items():element.set(qn(key),v)
                    element.text=text;run._r.append(element)
            modes[story]='shared_story_with_page_field'
        else:paragraph.add_run(template);modes[story]='shared_source_story'
        for run in paragraph.runs:run.font.name='Arial';run.font.size=Pt(8)
    def add_table(item):
        nonlocal repeated_headers
        cols=max(map(len,item['cells']));table=doc.add_table(rows=len(item['cells']),cols=cols);table.style='Table Grid';table.autofit=False
        bbox=item.get('bbox');width=bbox[2]-bbox[0] if bbox else float(section.page_width-section.left_margin-section.right_margin)/12700
        width=min(width,float(section.page_width-section.left_margin-section.right_margin)/12700)
        widths=[]
        for c in range(cols):
            spans=[s for s in item.get('cell_spans',[]) if s['column']==c and s.get('column_span',1)==1 and s.get('bbox')]
            widths.append(spans[0]['bbox'][2]-spans[0]['bbox'][0] if spans else 1)
        total=sum(widths)
        for c,points in enumerate(widths):
            table.columns[c].width=Pt(width*points/total)
            for cell in table.columns[c].cells:cell.width=Pt(width*points/total)
        for row in table.rows:row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        cells=validated_cells(item)
        header_rows={r for r in range(len(item['cells'])) if any(c['row']==r for c in cells) and all(c.get('role_candidate')=='header' for c in cells if c['row']==r)}
        for r in sorted(header_rows):
            if r!=0 and r-1 not in header_rows:continue
            table.rows[r]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'));repeated_headers+=1
        for cell in cells:
            target=table.cell(cell['row'],cell['column'])
            if cell['row_span']>1 or cell['column_span']>1:target=target.merge(table.cell(cell['row']+cell['row_span']-1,cell['column']+cell['column_span']-1))
            target.text='' if cell.get('text') is None else str(cell['text'])
            tcpr=target._tc.get_or_add_tcPr();borders=OxmlElement('w:tcBorders')
            for side in ('top','left','bottom','right'):
                edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single');edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'D9D9D9');borders.append(edge)
            tcpr.append(borders)
            margins=OxmlElement('w:tcMar')
            for side in ('top','left','bottom','right'):
                margin=OxmlElement('w:'+side);margin.set(qn('w:w'),'40');margin.set(qn('w:type'),'dxa');margins.append(margin)
            tcpr.append(margins)
            if cell.get('role_candidate')=='header':
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'E8EDF2');tcpr.append(shade)
            for paragraph in target.paragraphs:
                paragraph.paragraph_format.space_after=Pt(0);paragraph.paragraph_format.line_spacing=1
                for run in paragraph.runs:run.font.size=Pt(7.5 if cols>5 else 10);run.bold=cell.get('role_candidate')=='header'
        doc.add_paragraph().paragraph_format.space_after=Pt(2)
        used_tables.add(item['id'])
    for n in model['elements']:
        if n['story'] in ('whitespace','alternative_reading'):continue
        if n['story'] in ('header','footer') and n['story'] not in inline_stories:continue
        if n['kind']=='table':add_table(tables[n['table_id']]);continue
        role=n['role_candidate'];style='Title' if role=='title' else 'Subtitle' if role=='subtitle' else 'Caption' if role=='caption' else 'Heading '+str(min(3,n.get('heading_level_candidate',1))) if role=='heading' else 'Normal'
        paragraph=doc.add_paragraph(style=style)
        paragraph.paragraph_format.keep_with_next=role in ('title','subtitle','heading','caption') or n['text'].rstrip().endswith(':')
        fmt=n.get('paragraph_format',{});alignment=fmt.get('alignment_candidate')
        if alignment=='center_candidate' and role in ('title','subtitle','masthead','heading'):paragraph.alignment=WD_ALIGN_PARAGRAPH.CENTER
        elif alignment=='right_candidate' and role=='metadata':paragraph.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        box=n.get('bbox')
        if box and paragraph.alignment!=WD_ALIGN_PARAGRAPH.CENTER and len({s['page'] for s in n['source_spans']})==1:
            paragraph.paragraph_format.left_indent=Pt(max(0,box[0]-54))
            body_right=fmt.get('reference_body_margins_points',[54,float(section.page_width)/12700-54])[1]
            paragraph.paragraph_format.right_indent=Pt(max(0,float(section.page_width)/12700-54-body_right))
        if role=='list_item':
            paragraph.paragraph_format.left_indent=Pt((float(paragraph.paragraph_format.left_indent or 0)/12700)+12)
            paragraph.paragraph_format.first_line_indent=Pt(-12)
        for item in n['runs']:
            # PDF line wrapping is not a Word paragraph break. The model keeps
            # exact source newlines; the editable paragraph reflows naturally.
            text=item['text'].replace('\n',' ');run=paragraph.add_run(text);fmt=item['format']
            if fmt['state']=='source_glyph_candidate':
                font=fmt.get('font_name','');font=re.sub(r'^[A-Z]{6}\+','',font)
                if font:run.font.name=re.sub(r'[-,]?(?:BoldItalic|BoldMT|ItalicMT|Bold|Italic|Regular|MT)$','',font) or 'Arial'
                if fmt.get('font_size_points'):run.font.size=Pt(fmt['font_size_points'])
                run.bold=fmt['bold'];run.italic=fmt['italic']
        # Avoid an extra trailing line while retaining the original text in the model.
        if paragraph.runs:paragraph.runs[-1].text=paragraph.runs[-1].text.rstrip()
    for table in value['tables']:
        if table['id'] not in used_tables:
            doc.add_paragraph('Unresolved table candidate','Heading 1');add_table(table)
    for root in (doc.styles.element,doc.element):
        for border in list(root.xpath('.//w:pBdr')):border.getparent().remove(border)
    doc.core_properties.author='PDF2Data';doc.core_properties.title=value['title']
    doc.save(output);saved=Document(output);body='\n'.join(p.text for p in saved.paragraphs)
    for n in model['elements']:
        if n['story'] not in ({'body'}|inline_stories) or n['kind']=='table':continue
        if ' '.join(n['text'].split()) not in ' '.join(body.split()):raise ValueError('Word output lost structured source text')
    rendered_cells=[c.text for table in saved.tables for row in table.rows for c in row.cells]
    for table in value['tables']:
        for cell in validated_cells(table):
            if str(cell.get('text') or '') not in rendered_cells:raise ValueError('Word output lost source table cell')
    receipt=dict(schema_version='pdf2data.word-export.v1',source_pdf_sha256=value['source_pdf_sha256'],
                 model_schema=model['schema_version'],story_modes=modes,editable_tables=len(saved.tables),
                 repeated_header_rows=repeated_headers,body_text_readback='passed',table_cell_readback='passed',
                 automatic_legal_numbering=False,source_pagination_preserved=False,render_verified=False,
                 pdf_line_breaks_reflowed=True,
                 unsupported_structure=model['unsupported_structure'],publication_eligible=False)
    Path(output).with_name('document-word-validation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    if not __package__:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    export_word(json.loads(Path(sys.argv[1]).read_text()),Path(sys.argv[2]))

if __name__=='__main__':main()
