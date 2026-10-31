import json

import fitz
from pypdf import PdfReader

from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.quality_gate import assess_package


def test_native_quality_uses_fresh_geometric_reading_order_without_losing_footer_digits(tmp_path):
    original=tmp_path/'footer-first.pdf';pdf=fitz.open();page=pdf.new_page(width=600,height=800)
    page.insert_text((50,770),'Page 1 of 1',fontsize=10)  # First in the PDF content stream.
    page.insert_text((50,80),'EXAMPLE NOTIFICATION 2026',fontsize=12)
    page.insert_text((50,120),'The threshold is 51% and the contract duration is 12 years.',fontsize=10)
    page.insert_text((50,150),'The original page includes all conditions, quantities and its printed page label.',fontsize=10)
    pdf.save(original);pdf.close()
    assert PdfReader(original).pages[0].extract_text().startswith('Page 1 of 1')
    package=tmp_path/'package';extract_pdf(original,package,ocr='off',tables=False,native_consensus=True)
    selected=json.loads((package/'pages.jsonl').read_text())['text']
    assert selected.index('2026')<selected.index('Page 1 of 1')
    result=assess_package(package,original,derive_ocr_source_raster=True)['results'][0]
    assert result['components']['ordered_numbers']['state']=='pass'
    assert result['components']['ordered_operators']['state']=='pass'
    assert result['components']['source_render']['state']=='pass'
    assert result['components']['source_render']['unaccounted_ink_pixels']==0
    assert result['independent_reader_basis']=='fresh_native_reader_and_source_geometry_consensus'
