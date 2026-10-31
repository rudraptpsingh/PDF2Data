"""Real PDF negatives for missing source prose, hidden glyphs and native grids."""
from pathlib import Path
import json,hashlib
import pytest,pdfplumber
from pypdf import PdfWriter
from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject,NumberObject,ArrayObject
from local_vector_batch.native_zero_table_prose import prove_zero_table_source


def source(tmp_path,kind='plain'):
 path=tmp_path/(kind+'.pdf');writer=PdfWriter();page=writer.add_blank_page(612,842)
 name='/Devnagari-ChanakyaNormalA'if kind=='legacy_font'else'/Helvetica'
 font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject(name)}))
 if kind=='legacy_font':
  descriptor=DictionaryObject({NameObject('/Type'):NameObject('/FontDescriptor'),NameObject('/FontName'):NameObject(name),NameObject('/Flags'):NumberObject(32),NameObject('/FontBBox'):ArrayObject([NumberObject(n)for n in(0,-200,1000,900)]),NameObject('/Ascent'):NumberObject(800),NameObject('/Descent'):NumberObject(-200),NameObject('/CapHeight'):NumberObject(700),NameObject('/ItalicAngle'):NumberObject(0),NameObject('/StemV'):NumberObject(80)})
  font.get_object()[NameObject('/FontDescriptor')]=writer._add_object(descriptor)
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
 stream=''
 if kind=='clip':stream+='q 50 695 20 20 re W n\n'
 if kind=='clip_safe':stream+='q 40 0 480 842 re W n\n'
 if kind=='white':stream+='1 1 1 rg\n'
 stream+='BT /F1 12 Tf 50 700 Td (The deadline is 30 days and remains conditional.) Tj ET\n'
 if kind in('clip','clip_safe'):stream+='Q\n'
 if kind=='covered':stream+='1 1 1 rg 40 690 400 30 re f\n'
 if kind=='grid':stream+='40 550 400 80 re S 40 590 m 440 590 l S 240 550 m 240 630 l S\n'
 stream+='40 740 m 500 740 l S\n'
 content=DecodedStreamObject();content.set_data(stream.encode());page[NameObject('/Contents')]=writer._add_object(content);writer.write(path);return path


def test_actual_unframed_prose_is_not_itself_a_quality_clearance(tmp_path):
 path=source(tmp_path);digest=hashlib.sha256(path.read_bytes()).hexdigest()
 with pdfplumber.open(path)as pdf:proof=prove_zero_table_source(path,1,pdf.pages[0],digest)
 assert proof['fresh_native_table_count']==0 and proof['source_quality_page_cleared']is False


@pytest.mark.parametrize('kind',['grid','white','covered','clip','legacy_font'])
def test_zero_table_predicate_rejects_hidden_table_ink_and_font_mapping(tmp_path,kind):
 path=source(tmp_path,kind);digest=hashlib.sha256(path.read_bytes()).hexdigest()
 with pdfplumber.open(path)as pdf:
  with pytest.raises(ValueError):prove_zero_table_source(path,1,pdf.pages[0],digest)


def test_region_cannot_drop_selected_native_prose_even_with_rehashed_page(tmp_path):
 from local_vector_batch.native_table_regions import derive_regions
 path=source(tmp_path);parent=tmp_path/'package';(parent/'extraction').mkdir(parents=True);(parent/'original.pdf').write_bytes(path.read_bytes())
 text='The deadline is 30 days.';text_sha=hashlib.sha256(text.encode()).hexdigest();source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
 (parent/'extraction/pages.jsonl').write_text(json.dumps({'page':1,'text':text,'text_sha256':text_sha})+'\n')
 snapshot={'source_pdf_sha256':source_sha,'page':1,'page_text_sha256':text_sha,'tables':[]}
 with pytest.raises(ValueError,match='Selected text differs'):
  derive_regions(parent,snapshot,{'status':'validated','tables':[]})


def test_source_clip_containing_every_glyph_and_path_is_not_a_false_hold(tmp_path):
 path=source(tmp_path,'clip_safe');digest=hashlib.sha256(path.read_bytes()).hexdigest()
 with pdfplumber.open(path)as pdf:proof=prove_zero_table_source(path,1,pdf.pages[0],digest)
 assert proof['source_content_bbox'][0]>=40 and proof['source_content_bbox'][2]<=520
 assert proof['source_quality_page_cleared']is False
