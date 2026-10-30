"""Create a generic source PDF, not an extraction path. Requires reportlab."""
import argparse
from hashlib import sha256
import json
from pathlib import Path


def generate(root):
    from reportlab.pdfgen import canvas
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'source.pdf'
    doc = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    doc.setFont('Helvetica-Bold', 16)
    doc.drawString(50, 742, 'Equipment inspection')
    doc.setFont('Helvetica', 11)
    for x, y, text in [(50, 705, '(a) Inspect equipment.'), (70, 684, '(i) Check voltage.'),
                       (70, 663, '(ii) Record current.'), (50, 642, '(b) Review results.')]:
        doc.drawString(x, y, text)
    doc.setFont('Helvetica-Bold', 12)
    doc.drawString(50, 605, 'Readings:')
    for y in (580, 550, 520):
        doc.line(50, y, 450, y)
    for x in (50, 450):
        doc.line(x, 520, x, 580)
    doc.line(250, 520, 250, 550)
    doc.setFont('Helvetica-Bold', 11)
    doc.drawString(60, 560, 'Electrical measurements')
    doc.setFont('Helvetica', 11)
    doc.drawString(60, 530, 'Voltage')
    doc.drawString(260, 530, '230 V')
    doc.setFont('Helvetica-Bold', 12)
    doc.drawString(50, 485, 'Next step:')
    doc.setFont('Helvetica', 11)
    doc.drawString(50, 465, '(c) Sign the checklist.')
    doc.save()
    (root / 'manifest.json').write_text(json.dumps({'schema_version': 'powerlex.legal-l1.v1',
        'sources': [{'id': 'holdout', 'path': str(path.resolve()),
                     'sha256': sha256(path.read_bytes()).hexdigest(), 'family': 'technical_procedure'}]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    generate(parser.parse_args().output)
