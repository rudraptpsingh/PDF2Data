"""Optional OOXML contracts; the document runtime is selected explicitly."""
from hashlib import sha256
import json,os,subprocess,sys
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import pytest
from local_vector_batch.document_blocks import build_blocks
from local_vector_batch.document_model import build_document_model
from local_vector_batch.legal_l1 import structure_document

RUNTIME=os.environ.get('PDF2DATA_DOCX_PYTHON')
pytestmark=pytest.mark.skipif(not RUNTIME,reason='set PDF2DATA_DOCX_PYTHON to an optional document runtime')
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def value(footer=lambda n:f'Instrument footer Page {n} of 2'):
    pages=[];layout=[]
    for n in (1,2):
        lines=[(f'{n}. Ordinary source paragraph.',100),(footer(n),960)]
        text='\n'.join(t for t,y in lines);pages.append(dict(id=f'd:p{n}',page=n,text=text,text_sha256=sha256(text.encode()).hexdigest()))
        glyphs=[dict(text=c,bbox=[50+i*4,y,54+i*4,y+10],font='Arial',size=10) for t,y in lines for i,c in enumerate(t)]
        layout.append(dict(page=n,width=600,height=1000,glyphs=glyphs))
    data=dict(title='Source document',source_id='d',source_pdf_sha256='a'*64,pages=pages,layout=layout,tables=[],structure=structure_document(pages,'a'*64,'generic'))
    data['document_blocks']=build_blocks(data);data['document_model']=build_document_model(data)
    return data


def run(tmp_path,data):
    src=tmp_path/'data.json';src.write_text(json.dumps(data));out=tmp_path/'document.docx'
    result=subprocess.run([RUNTIME,'-m','local_vector_batch.readable_word',str(src),str(out)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    return out,json.loads((tmp_path/'document-word-validation.json').read_text())


def test_common_footer_uses_real_story_and_dynamic_page_and_total_fields(tmp_path):
    output,receipt=run(tmp_path,value())
    with ZipFile(output) as z:
        root=ET.fromstring(z.read('word/document.xml'));footer=ET.fromstring(z.read('word/footer1.xml'))
    body=''.join(root.itertext());instructions=[n.text.strip() for n in footer.findall('.//w:instrText',NS)]
    assert instructions==['PAGE','NUMPAGES']
    assert 'Instrument footer' not in body
    assert root.find('.//w:footerReference',NS) is not None
    assert root.find('.//w:numPr',NS) is None
    assert receipt['story_modes']['footer']=='shared_story_with_page_field'
    assert receipt['body_text_readback']=='passed' and not receipt['source_pagination_preserved']


def test_different_footer_versions_are_preserved_instead_of_omitted(tmp_path):
    output,receipt=run(tmp_path,value(lambda n:f'Instrument variant {n} Page {n}'))
    with ZipFile(output) as z:root=ET.fromstring(z.read('word/document.xml'))
    body=''.join(root.itertext())
    assert 'Instrument variant 1 Page 1' in body and 'Instrument variant 2 Page 2' in body
    assert receipt['story_modes']['footer']=='varying_source_content_preserved_inline'
