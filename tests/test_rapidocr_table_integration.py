"""A real raster PDF checks provider-to-table wiring; OCR is a declared fixture.

No OCR inference or legal/source-quality approval is claimed by these tests.
"""
import hashlib,json
from pathlib import Path
import pytest

Image=pytest.importorskip('PIL.Image')
pytest.importorskip('cv2')
from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.ocr import LocalOCR
from local_vector_batch.evidence_package import verify_package


def source_fixture(tmp_path,ruled):
 from PIL import ImageDraw
 image=Image.new('RGB',(900,500),'white');draw=ImageDraw.Draw(image)
 xs=[70,450,830];ys=[80,193,306,420]
 cells=[['Condition and source evidence','Exact candidate value'],['Capacity threshold example','51 percent'],['Original term duration example','12 years']]
 words=[]
 if ruled:
  for x in xs:draw.line((x,ys[0],x,ys[-1]),fill='black',width=3)
  for y in ys:draw.line((xs[0],y,xs[-1],y),fill='black',width=3)
 for i,row in enumerate(cells):
  for j,text in enumerate(row):
   x,y=xs[j]+12,ys[i]+12;draw.text((x,y),text,fill='black')
   words.append({'text':text,'bbox':[x/900,y/500,(x+len(text)*6)/900,(y+20)/500]})
 png=tmp_path/'synthetic-source-grid.png';image.save(png)
 source=tmp_path/'synthetic-source.pdf';image.save(source,'PDF',resolution=180)
 return source,cells,words


@pytest.mark.parametrize('ruled',[True,False])
def test_rapidocr_words_reach_actual_source_grid_parser_without_promoting_quality(tmp_path,monkeypatch,ruled):
 source,cells,words=source_fixture(tmp_path,ruled)
 def fixture_read(self,page):
  self.last_rendered_image=page.to_image(resolution=180).original
  return {'engine':'rapidocr','text':'\n'.join(' | '.join(row)for row in cells),
   'lines':[{'text':' '.join(w['text']for w in words),'words':words,'bbox':[.05,.1,.95,.9]}],
   'render_dpi':180,'mean_confidence':.99,'device_used':'cpu','fixture_not_actual_inference':True}
 monkeypatch.setattr(LocalOCR,'recognize',fixture_read)
 out=tmp_path/'candidate';report=extract_pdf(source,out,ocr='rapidocr',tables=True)
 page=json.loads((out/'pages.jsonl').read_text().splitlines()[0]);tables=json.loads((out/'tables.json').read_text())
 assert page['method']=='rapidocr' and page['status']=='review_required'
 assert report['source_pdf_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
 if ruled:
  assert len(tables)==1 and tables[0]['cells']==cells
  table=tables[0]
  assert table['extraction_basis']=='ocr_ruling_geometry_word_boxes'
  assert table['source_pdf_sha256']==report['source_pdf_sha256']
  assert table['page_text_sha256']==page['text_sha256'] and table['page']==1
  assert table['ocr_engine']=='rapidocr' and table['status']=='needs_row_column_review'
  assert any(i['kind']=='ocr_ruled_table_candidate'for i in report['issues'])
 else:assert tables==[]
 assert verify_package(out,source_pdf=source)['pages']==1
