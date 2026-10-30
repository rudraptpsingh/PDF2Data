"""Exercise the existing Docling-core parser when that optional runtime exists."""
import pytest

pytest.importorskip('docling_core')
from PIL import Image
from local_vector_batch.pdf_compare import parse_doctags_page
from local_vector_batch.pdf_layout_import import _top_left_box


def test_doctags_restore_original_physical_page_and_pdf_points(tmp_path):
    image=tmp_path/'source.png';Image.new('RGB',(2000,1000),'white').save(image)
    tokens='<doctag><text><loc_50><loc_100><loc_250><loc_200>Hello 51%</text></doctag>'
    parsed=parse_doctags_page(tokens,image,physical_page=7,page_size_points=(1000,500))
    assert set(parsed['pages'])=={'7'}
    assert parsed['pages']['7']['size']=={'width':1000,'height':500}
    provenance=parsed['texts'][0]['prov'][0]
    assert provenance['page_no']==7
    assert _top_left_box(provenance['bbox'],1000,500)==[100,100,500,200]
    assert parsed['texts'][0]['text']=='Hello 51%'
    assert 'image' not in parsed['pages']['7']


def test_doctags_reject_invalid_original_page_or_canvas(tmp_path):
    image=tmp_path/'source.png';Image.new('RGB',(20,20),'white').save(image)
    for number,size in ((True,(10,10)),(0,(10,10)),(1,(float('nan'),10)),(1,(0,10))):
        with pytest.raises(ValueError):parse_doctags_page('<doctag></doctag>',image,physical_page=number,page_size_points=size)
