"""Optional Word exporter, executed with an explicitly selected document runtime."""
import json,re,sys
from pathlib import Path

def main():
    from docx import Document
    from docx.shared import Inches,Pt,RGBColor
    from docx.oxml import OxmlElement
    value=json.loads(Path(sys.argv[1]).read_text());doc=Document();section=doc.sections[0]
    section.top_margin=section.bottom_margin=Inches(.65);section.left_margin=section.right_margin=Inches(.7)
    normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(10);normal.paragraph_format.space_after=Pt(5)
    for name in ['Title','Heading 1','Heading 2']:
        doc.styles[name].font.name='Arial';doc.styles[name].font.color.rgb=RGBColor(0,0,0)
    title=re.sub(r'[^\w\s]',' ',value['title']);title=' '.join(title.split()) or 'Extracted PDF content'
    doc.add_paragraph(title,'Title')
    doc.add_paragraph('Continuous extracted source text with paragraph boundaries and source page references. Quality flags are retained. Legal interpretation and currentness have not been verified.')
    from document_blocks import validated_cells,validate_blocks
    def add_table(item):
        table=doc.add_table(rows=0,cols=max(map(len,item['cells'])));table.style='Table Grid'
        for row in item['cells']:
            table.add_row()
        for row in table.rows:
            row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        for cell in validated_cells(item):
            target=table.cell(cell['row'],cell['column'])
            if cell['row_span']>1 or cell['column_span']>1:target=target.merge(table.cell(cell['row']+cell['row_span']-1,cell['column']+cell['column_span']-1))
            target.text='' if cell.get('text') is None else str(cell['text'])
            for paragraph in target.paragraphs:
                paragraph.paragraph_format.space_after=Pt(0)
                for run in paragraph.runs:run.font.size=Pt(8 if len(item['cells'][0])>5 else 10)
            if cell.get('role_candidate')=='header':
                for paragraph in target.paragraphs:
                    for run in paragraph.runs:run.bold=True
    blocks=value.get('document_blocks')
    if not blocks:raise ValueError('structured Word export requires source document blocks')
    validate_blocks(blocks,value['pages']);tables={t['id']:t for t in value['tables']}
    for block in blocks['blocks']:
        kind=block['kind']
        if kind in ('footer','header','whitespace','alternative_reading'):continue
        if kind=='table':add_table(tables[block['table_id']]);continue
        text='\n'.join(s['exact_quote'].rstrip('\n') for s in block['spans']).strip('\n')
        style='Heading '+str(min(3,block.get('heading_level_candidate',1)+1)) if kind=='heading' else 'List Paragraph' if kind=='list_item' else 'Normal'
        paragraph=doc.add_paragraph(text,style)
        if kind=='paragraph' and block.get('bold_candidate'):
            for run in paragraph.runs:run.bold=True
        if kind=='list_item':paragraph.paragraph_format.left_indent=Inches(.2+.2*block.get('list_depth_candidate',0))
    if blocks['unresolved_tables']:
        doc.add_paragraph('Unresolved table candidates','Heading 1')
        doc.add_paragraph('Raw text is retained above because exact inline replacement was not proved.')
        for unresolved in blocks['unresolved_tables']:add_table(tables[unresolved['table_id']])
    for root in (doc.styles.element,doc.element):
        for border in list(root.xpath('.//w:pBdr')):border.getparent().remove(border)
    doc.core_properties.author='PDF2Data';doc.core_properties.title=title
    doc.save(sys.argv[2]);body='\n'.join(p.text for p in Document(sys.argv[2]).paragraphs)
    for block in blocks['blocks']:
        if block['kind'] in ('footer','header','whitespace','table','alternative_reading'):continue
        text='\n'.join(s['exact_quote'].rstrip('\n') for s in block['spans']).strip('\n')
        if text not in body:raise ValueError('Word output lost structured source text')
    rendered_cells=[c.text for table in Document(sys.argv[2]).tables for row in table.rows for c in row.cells]
    for table in value['tables']:
        for cell in validated_cells(table):
            if str(cell.get('text') or '') not in rendered_cells:raise ValueError('Word output lost source table cell')

if __name__=='__main__':main()
