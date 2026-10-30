"""Grid recovery tests use rendered geometry, never inferred cell values."""
import pytest

PIL = pytest.importorskip("PIL.Image")
pytest.importorskip("cv2")

from local_vector_batch.ocr_tables import ruled_table_candidates


def scanned_grid(rows, *, missing=None):
    from PIL import ImageDraw
    image = PIL.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = 70, 80, 830, 420
    columns = len(rows[0])
    xs = [round(left + i * (right - left) / columns) for i in range(columns + 1)]
    ys = [round(top + i * (bottom - top) / len(rows)) for i in range(len(rows) + 1)]
    for x in xs:
        draw.line((x, top, x, bottom), fill="black", width=3)
    for y in ys:
        draw.line((left, y, right, y), fill="black", width=3)
    words = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if not value or (row_index, column_index) == missing:
                continue
            x0, x1 = xs[column_index], xs[column_index + 1]
            y0, y1 = ys[row_index], ys[row_index + 1]
            word_box = [(x0 + 12) / 900, (y0 + 12) / 500,
                        (min(x1 - 12, x0 + 15 + len(value) * 6)) / 900, (y0 + 30) / 500]
            words.append({"text": value, "bbox": word_box})
    return image, [{"text": " ".join(word["text"] for word in words), "words": words}]


@pytest.mark.parametrize("rows,expected", [
    ([['Particulars', 'FY2023-24', 'FY2024-25'],
      ['Loan Opening Balance', '10,117.99', '11,986.44'],
      ['New Loan', '2,932.00', '']], (1, 0, 'Loan Opening Balance')),
    ([['Voltage', 'Normal', 'Forest'], ['765kV', '400', '300']], (1, 2, '300')),
    ([['Station', 'Allocation', 'DOCO'], ['Talcher', '8.5 MW', '01.01.1997']], (1, 1, '8.5 MW')),
    ([['Scheme', 'Capacity'], ['Khavda', '27700 MW']], (1, 1, '27700 MW')),
    ([['Year', 'Rate'], ['2025', '51%']], (1, 1, '51%')),
])
def test_scanned_table_preserves_row_column_and_review_status(rows, expected):
    image, lines = scanned_grid(rows)
    tables = ruled_table_candidates(image, lines, page=7, page_id='pdf-source:p7',
                                    pdf_width=612, pdf_height=340)
    assert len(tables) == 1
    table = tables[0]
    assert table['cells'][expected[0]][expected[1]] == expected[2]
    assert table['cells'] == rows
    assert table['page'] == 7 and table['id'] == 'pdf-source:p7:table1'
    assert table['status'] == 'needs_row_column_review'
    assert table['extraction_basis'] == 'ocr_ruling_geometry_word_boxes'
    assert table['bbox_coordinates'] == 'PDF points, top-left'


def test_missing_ocr_word_stays_blank_and_crossing_word_is_unassigned():
    rows = [['Condition', 'Value'], ['Threshold', '51%'], ['Term', '12 years']]
    image, lines = scanned_grid(rows, missing=(1, 1))
    # This box crosses the printed column divider, so its value is not forced
    # into the left or right cell.
    lines[0]['words'].append({'text': 'ambiguous', 'bbox': [.48, .47, .55, .52]})
    table = ruled_table_candidates(image, lines, page=1, page_id='p1',
                                    pdf_width=612, pdf_height=340)[0]
    assert table['cells'][1][1] == ''
    assert 'ambiguous' in table['unassigned_words']


def test_no_printed_grid_produces_no_table_candidate():
    image = PIL.new('RGB', (900, 500), 'white')
    lines = [{'words': [{'text': '51%', 'bbox': [.2, .2, .3, .25]}]}]
    assert ruled_table_candidates(image, lines, page=1, page_id='p1',
                                   pdf_width=612, pdf_height=340) == []
