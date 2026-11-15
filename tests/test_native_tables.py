from io import BytesIO
from types import SimpleNamespace

import pytest

from local_vector_batch.native_tables import glyph_audit,is_ruling_object,table_shape


def test_shaded_text_background_is_not_a_table_rule():
    assert not is_ruling_object({'object_type':'rect','fill':True,'stroke':False,'width':40,'height':9,'non_stroking_color':[.85,.89,.94]})
    assert is_ruling_object({'object_type':'rect','fill':True,'stroke':False,'width':400,'height':.6,'non_stroking_color':[0,0,0]})
    assert is_ruling_object({'object_type':'char','text':'X'})
    assert is_ruling_object({'object_type':'rect','stroke':True,'width':40,'height':90})


def test_unicode_currency_script_and_math_are_not_classified_as_garbage():
    result=glyph_audit('₹ 9.175 crore · क्षमता 72.6 MW ≤ 100% — “subject to”')
    assert result['non_ascii_characters']>0 and not result['issues']
    for bad in ['(cid:123)','\ufffd','\ue001','x\x00y']:
        assert glyph_audit(bad)['status']=='repair_required'


def test_merged_cell_has_span_and_does_not_fill_covered_positions():
    found=SimpleNamespace(rows=[SimpleNamespace(cells=[(0,0,10,20),(10,0,20,10)]),SimpleNamespace(cells=[None,(10,10,20,20)])])
    shape=table_shape(found,[['Category','Header'],[None,'100%']],'t')
    merged=next(c for c in shape['cell_spans'] if c['text']=='Category')
    assert merged['row_span']==2 and shape['row_count']==2 and shape['column_count']==2
    assert not any(c['row']==1 and c['column']==0 for c in shape['cell_spans'])


def test_mixed_script_lookalike_is_flagged_but_preserved():
    result=glyph_audit('Pаy 100')  # Cyrillic a inside an otherwise Latin word.
    assert result['status']=='repair_required'
    assert result['issues'][0]['literal']=='Pаy'


def test_irregular_grid_keeps_values_at_physical_box_coordinates():
    from local_vector_batch.document_blocks import validated_cells,table_html
    # The first physical row omits an x boundary present in another row.
    found=SimpleNamespace(rows=[SimpleNamespace(cells=[(0,0,20,10),(20,0,30,10)]),SimpleNamespace(cells=[(0,10,10,20),(10,10,30,20)])])
    raw=[['Merged header','Right header'],['Left value','Merged value']]
    shape=table_shape(found,raw,'t')
    assert shape['cells']==[['Merged header',None,'Right header'],['Left value','Merged value',None]]
    assert shape['extracted_cells']==raw and raw[0][1]=='Right header'
    validated_cells(shape)
    html=table_html(shape)
    assert all(html.count(value)==1 for row in raw for value in row)
    assert 'colspan="2"' in html


def test_unmapped_populated_raw_cell_is_rejected_instead_of_lost():
    import pytest
    found=SimpleNamespace(rows=[SimpleNamespace(cells=[(0,0,10,10),None])])
    with pytest.raises(ValueError,match='every populated'):
        table_shape(found,[['Visible','Unmapped value']],'t')


@pytest.mark.parametrize(
    ('prior_middle', 'extend_middle', 'expected_recovery'),
    [(180, True, True), (200, True, False), (180, False, False)],
)
def test_open_top_row_requires_aligned_previous_table_and_all_rulings(
    prior_middle, extend_middle, expected_recovery,
):
    pdfplumber = pytest.importorskip('pdfplumber')
    canvas = pytest.importorskip('reportlab.pdfgen.canvas')
    from local_vector_batch.native_tables import find_native_tables

    data = BytesIO()
    pdf = canvas.Canvas(data, pagesize=(400, 600))
    for x in (50, prior_middle, 320):
        pdf.line(x, 33, x, 78)
    for y in (33, 48, 63, 78):
        pdf.line(50, y, 320, y)
    for label, y in zip(('Voltage', '<132kV', 'Other tier'), (67, 52, 37)):
        pdf.drawString(55, y, label)
        pdf.drawString(215, y, 'Rate')
    pdf.showPage()
    for x in (50, 180, 320):
        top = 580 if x != 180 or extend_middle else 565
        pdf.line(x, 535, x, top)
    for y in (535, 550, 565):
        pdf.line(50, y, 320, y)
    for label, value, y in (('132 kV', 'Rs. 2 crore', 569), ('Higher tier', 'Rate A', 554),
                            ('Top tier', 'Rate B', 539)):
        pdf.drawString(55, y, label)
        pdf.drawString(215, y, value)
    pdf.save()
    data.seek(0)
    with pdfplumber.open(data) as source:
        preceding, = find_native_tables(source.pages[0])
        assert preceding.extract()[1][0] == '<132kV'
        page = source.pages[1]
        table, = find_native_tables(page)
        expected = [['Higher tier', 'Rate A'], ['Top tier', 'Rate B']]
        if expected_recovery:
            expected.insert(0, ['132 kV', 'Rs. 2 crore'])
        assert table.extract() == expected
        shape = table_shape(table, table.extract(), 'continuation')
        assert shape['structure_status'] == 'source_geometry_candidate_requires_cell_review'
        assert (shape['detection_method'] == 'source_rulings_open_top_continuation_row') == expected_recovery
