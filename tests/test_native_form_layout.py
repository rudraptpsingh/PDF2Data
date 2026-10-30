import hashlib
import json
from types import SimpleNamespace

from local_vector_batch.native_form_layout import prove_empty_form_grid
from local_vector_batch.quality_gate import assess_page


def _source(cells):
    boxes = [[(0, 0, 100, 20), (100, 0, 200, 20)],
             [(0, 20, 100, 40), (100, 20, 200, 40)]]
    table = SimpleNamespace(bbox=(0, 0, 200, 40), rows=[SimpleNamespace(cells=row) for row in boxes],
                            extract=lambda: cells)
    chars = []
    for i, row in enumerate(cells):
        for j, value in enumerate(row):
            for char in value:
                chars.append({"text": char, "x0": j*100+10, "x1": j*100+12,
                              "top": i*20+5, "bottom": i*20+10})
    return SimpleNamespace(find_tables=lambda: [table], chars=chars), {
        "id": "doc:p3:table1", "bbox": [0, 0, 200, 40], "cells": cells}


def test_empty_form_grid_is_source_bound_and_data_table_stays_held():
    source, table = _source([["Signature:", ""], ["1.", "2."]])
    geometry = {"page_complete": True, "reading_order_proven": True,
                "unaccounted_ink_pixels": 0, "unresolved_visual_regions": [],
                "ink_pixels": 100, "nontext_objects": {"images": 0, "curves": 0, "rects": 4, "lines": 0},
                "rendered_image_sha256": "b"*64, "method": "native_glyph_raster_coverage",
                "ready": True, "source_pdf_sha256": "a"*64, "page": 3,
                "page_text_sha256": "c"*64, "render_dpi": 120,
                "independent_word_order_agrees": True, "independent_number_order_agrees": True}
    proof = prove_empty_form_grid(source, [table], source_pdf_sha256="a"*64,
                                  page_text_sha256="c"*64, physical_page=3,
                                  source_geometry=geometry)
    assert proof and proof["ordinal_placeholders"] == 2
    text = "Signature: 1. 2."
    page = {"page": 3, "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "method": "pdf_text", "selected_quality": {"needs_ocr": False}}
    proof["page_text_sha256"] = page["text_sha256"]
    geometry["page_text_sha256"] = page["text_sha256"]
    result = assess_page(page, source_pdf_sha256="a"*64, package_pdf_sha256="a"*64,
                         independent_text=text, source_geometry=geometry,
                         layout_proof=proof, table_count=1)
    assert result["components"]["layout"]["state"] == "pass"
    assert result["outcome"] == "auto_accepted"
    data_source, data_table = _source([["Rate", "Charge"], ["1.", "2."]])
    assert prove_empty_form_grid(data_source, [data_table], source_pdf_sha256="a"*64,
                                 page_text_sha256="c"*64, physical_page=3,
                                 source_geometry=geometry) is None
    tampered, same_table = _source([["Signature:", "injected"], ["1.", "2."]])
    same_table["cells"][0][1] = ""
    assert prove_empty_form_grid(tampered, [same_table], source_pdf_sha256="a"*64,
                                 page_text_sha256="c"*64, physical_page=3,
                                 source_geometry=geometry) is None
