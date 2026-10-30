"""Optional Word exporter, executed with an explicitly selected document runtime."""
import json,sys
from pathlib import Path

def main():
    from docx import Document
    from docx.shared import Inches,Pt,RGBColor
    value=json.loads(Path(sys.argv[1]).read_text());doc=Document();section=doc.sections[0]
    section.top_margin=section.bottom_margin=Inches(.65);section.left_margin=section.right_margin=Inches(.7)
    normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(10);normal.paragraph_format.space_after=Pt(5)
    for name in ['Title','Heading 1','Heading 2']:
        doc.styles[name].font.name='Arial';doc.styles[name].font.color.rgb=RGBColor(0,0,0)
    doc.add_paragraph('Serentica CERC record of proceedings' if value['source_id']=='serentica-cerc-rop' else 'Extracted PDF content','Title')
    doc.add_paragraph('Continuous extracted source text with paragraph boundaries and source page references. Quality flags are retained. Legal interpretation and currentness have not been verified.')
    for unit in value['structure']['units']:
        doc.add_paragraph('Paragraph '+unit['label'] if unit['label'].isdigit() else 'Document heading and opening text','Heading 1')
        doc.add_paragraph(''.join(s['exact_quote'] for s in unit['spans']))
        doc.add_paragraph('Source pages '+', '.join(str(n) for n in dict.fromkeys(s['page'] for s in unit['spans'])))
    for item in value['tables']:
        if not item.get('cells'):continue
        doc.add_paragraph('Reconstructed table candidate on page '+str(item['page']),'Heading 2')
        table=doc.add_table(rows=0,cols=max(map(len,item['cells'])));table.style='Table Grid'
        for row in item['cells']:
            cells=table.add_row().cells
            for col,text in enumerate(row):cells[col].text='' if text is None else str(text)
    for root in (doc.styles.element,doc.element):
        for border in list(root.xpath('.//w:pBdr')):border.getparent().remove(border)
    doc.core_properties.author='PDF2Data';doc.core_properties.title='Extracted PDF content'
    doc.save(sys.argv[2]);body='\n'.join(p.text for p in Document(sys.argv[2]).paragraphs)
    if any(''.join(s['exact_quote'] for s in unit['spans']) not in body for unit in value['structure']['units']):raise ValueError('Word output lost source text')

if __name__=='__main__':main()
