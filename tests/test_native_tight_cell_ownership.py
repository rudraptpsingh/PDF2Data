import numpy as np
from local_vector_batch.native_tight_cell_ownership import contained,intersects,outside_parts,ink_count,pixels

def test_boundary_touch_has_no_false_positive_area():
 assert not intersects([0,0,10,10],[10,0,20,10]);assert contained([1,1,9,9],[0,0,10,10]);assert not contained([-1,1,9,9],[0,0,10,10])
def test_outside_parts_conserve_crossing_rectangle_without_filling_interiors():
 pieces=outside_parts([0,0,10,10],[3,3,8,8]);assert len(pieces)==4;assert sum((x1-x0)*(y1-y0)for x0,y0,x1,y1 in pieces)==75
 assert outside_parts([0,0,2,2],[3,3,8,8])==[[0,0,2,2]]
def test_pixel_count_is_strict_nonwhite_and_conservative_at_fractional_edges():
 ink=np.zeros((10,10),dtype=bool);ink[4,4]=True;assert ink_count(ink,[4.99,4.99,5,5],1)==1;assert ink_count(ink,[5,5,6,6],1)==0;assert pixels([-1,-1,12,12],1,10,10)==(0,0,10,10)


def _pdf(path,*,cover=False,clip=False,white=False,duplicate=False):
 from pypdf import PdfWriter
 from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
 writer=PdfWriter();page=writer.add_blank_page(width=240,height=320);font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}));page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
 prefix=b'q 0 0 35 320 re W n\n'if clip else b'';color=b'1 1 1 rg\n'if white else b''
 data=b'BT /F1 20 Tf 20 258 Td (HEADER) Tj ET\n'+prefix+color+b'BT /F1 12 Tf 20 240 Td (6) Tj /F1 8 Tf 6.7 6 Td (th) Tj /F1 12 Tf 6.7 -6 Td ( Street) Tj ET\n'
 if duplicate:data+=b'BT /F1 12 Tf 20 240 Td (6) Tj /F1 8 Tf 6.7 6 Td (th) Tj /F1 12 Tf 6.7 -6 Td ( Street) Tj ET\n'
 if clip:data+=b'Q\n'
 if cover:data+=b'1 1 1 rg 18 235 95 20 re f\n'
 stream=DecodedStreamObject();stream.set_data(data);page[NameObject('/Contents')]=writer._add_object(stream)
 with path.open('wb')as file:writer.write(file)


def test_actual_gray_or_black_text_ownership_is_not_a_blank_or_lowres_shortcut(tmp_path):
 import pytest
 pytest.importorskip('pymupdf')
 from local_vector_batch.native_tight_cell_ownership import audit_cells
 p=tmp_path/'source.pdf';_pdf(p)
 r=audit_cells(p,1,[{'bbox':[19,65,110,84],'selected':'6th Street'}])[0]
 assert r['strict_geometry_ownership_verified'];assert not r['actual_ink_crossings'];assert not r['unaccounted_cell_ink_pixels'];assert r['owned_characters'];assert not r['source_quality_page_cleared']
 with pytest.raises(ValueError,match='fixed240'):audit_cells(p,1,[],dpi=72)


def test_actual_occlusion_white_text_clipping_and_duplicate_glyphs_do_not_pass(tmp_path):
 import pytest
 pytest.importorskip('pymupdf')
 from local_vector_batch.native_tight_cell_ownership import audit_cells
 for mode in('cover','clip','white','duplicate'):
  p=tmp_path/(mode+'.pdf');_pdf(p,**{mode:True});r=audit_cells(p,1,[{'bbox':[19,65,110,84],'selected':'6th Street'}])[0]
  assert not r['strict_geometry_ownership_verified'],mode
  assert not r['source_quality_page_cleared']


def test_actual_source_ink_split_by_estimated_cell_stays_held(tmp_path):
 import pytest
 pytest.importorskip('pymupdf')
 from local_vector_batch.native_tight_cell_ownership import audit_cells
 p=tmp_path/'crossing.pdf';_pdf(p);r=audit_cells(p,1,[{'bbox':[22,65,110,84],'selected':'6th Street'}])[0]
 assert r['actual_ink_crossings'];assert not r['strict_geometry_ownership_verified']


def test_raw_annotation_presence_never_resolves_cyclic_or_indirect_evidence():
 from types import SimpleNamespace
 from local_vector_batch.native_tight_cell_ownership import raw_annotation_presence
 class Page:
  @property
  def annots(self):raise AssertionError('Recursive annotation property must not be touched')
 def page(raw):
  p=Page();p.page_obj=SimpleNamespace(attrs={'Annots':raw});return p
 cyclic={};cyclic['Parent']=cyclic
 class Indirect:
  def resolve(self):raise AssertionError('Indirect annotation array must not be resolved')
 for raw in(cyclic,Indirect(),[cyclic],(),{}):
  proof=raw_annotation_presence(page(raw));assert proof['has_annotations']and proof['annotation_evidence_preserved_in_original_PDF']
  assert proof['inspection_basis']=='raw_Annots_presence_no_resolution'
 for raw in(None,[]):assert raw_annotation_presence(page(raw))['has_annotations']is False
 assert raw_annotation_presence(Page())['has_annotations']is True


def test_actual_cyclic_annotation_prevents_tight_ownership_without_recursion(tmp_path):
 from pypdf import PdfReader,PdfWriter
 from pypdf.generic import NameObject,DictionaryObject,ArrayObject,NumberObject
 from local_vector_batch.native_tight_cell_ownership import audit_cells
 plain=tmp_path/'plain.pdf';_pdf(plain);writer=PdfWriter();writer.add_page(PdfReader(plain).pages[0]);page=writer.pages[0]
 annotation=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):ArrayObject([NumberObject(1),NumberObject(1),NumberObject(2),NumberObject(2)])})
 reference=writer._add_object(annotation);annotation[NameObject('/Parent')]=reference;page[NameObject('/Annots')]=ArrayObject([reference]);source=tmp_path/'cyclic.pdf';writer.write(source)
 report=audit_cells(source,1,[{'bbox':[19,65,110,84],'selected':'6th Street'}])[0]
 assert report['raw_annotation_guard']['has_annotations']
 assert not report['strict_geometry_ownership_verified']and not report['source_quality_page_cleared']
 assert any(r['kind']=='unresolved_raw_annotation_presence'for r in report['overlapping_images_or_curves'])
