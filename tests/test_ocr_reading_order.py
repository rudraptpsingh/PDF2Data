"""OCR lines in reading order: rows by vertical overlap (a cell a pixel higher never jumps ahead), and two text
columns read left then right; tables stay row by row."""
from local_vector_batch.layout_order import order_ocr_lines


def line(text, x0, top, x1, bottom):
    return {"text": text, "confidence": 0.99, "bbox": [x0, top, x1, bottom], "words": []}


def texts(lines):
    return [l["text"] for l in order_ocr_lines(lines)]


def test_table_row_with_jittered_tops_reads_left_to_right():
    rows = []
    for r in range(4):
        top = 0.20 + r * 0.03
        rows += [line(f"Item {r}", 0.10, top + 0.002, 0.30, top + 0.018), line(f"{r}6", 0.45, top - 0.001, 0.50, top + 0.016),
                 line(f"{r}3.00", 0.60, top + 0.001, 0.68, top + 0.017), line(f"{r}78.00", 0.80, top, 0.90, top + 0.017)]
    assert texts(rows) == [t for r in range(4) for t in (f"Item {r}", f"{r}6", f"{r}3.00", f"{r}78.00")]


def test_two_text_columns_read_left_then_right():
    left = [line(f"left line {i} of the first column text", 0.08, 0.10 + i * 0.02, 0.46, 0.115 + i * 0.02) for i in range(6)]
    right = [line(f"right line {i} of the second column", 0.54, 0.10 + i * 0.02 + 0.001, 0.92, 0.115 + i * 0.02) for i in range(6)]
    assert texts(right + left) == [l["text"] for l in left] + [r["text"] for r in right]


def test_title_across_columns_stays_first():
    title = line("A title that runs across both columns of the page", 0.08, 0.05, 0.92, 0.07)
    left = [line(f"left {i} words words words", 0.08, 0.10 + i * 0.02, 0.46, 0.115 + i * 0.02) for i in range(5)]
    right = [line(f"right {i} words words words", 0.54, 0.10 + i * 0.02, 0.92, 0.115 + i * 0.02) for i in range(5)]
    assert texts(left + [title] + right) == [title["text"]] + [l["text"] for l in left] + [r["text"] for r in right]


def test_single_column_keeps_top_to_bottom():
    col = [line(f"paragraph line {i}", 0.08, 0.1 + i * 0.02, 0.9, 0.115 + i * 0.02) for i in range(8)]
    assert texts(list(reversed(col))) == [c["text"] for c in col]


def test_skewed_two_columns_still_read_left_then_right():
    # A page scanned 1.5 degrees off: the right column sits lower than its left partner, so no row holds both.
    left = [line(f"left line {i} of the first column text", 0.08, 0.10 + i * 0.025, 0.46, 0.115 + i * 0.025) for i in range(8)]
    right = [line(f"right line {i} of the second column", 0.54, 0.112 + i * 0.025, 0.92, 0.127 + i * 0.025) for i in range(8)]
    assert texts(left + right) == [l["text"] for l in left] + [r["text"] for r in right]


def test_rows_never_chain_down_a_skewed_page():
    from local_vector_batch.layout_order import rows_by_overlap
    # Real OCR boxes from a 1.5-degree skewed two-column scan: each line overlaps its neighbour in the other column.
    boxes = [(0.517, 0.064, 0.804, 0.085), (0.08, 0.072, 0.329, 0.093), (0.518, 0.082, 0.785, 0.101),
             (0.081, 0.091, 0.319, 0.11), (0.519, 0.099, 0.815, 0.119), (0.081, 0.107, 0.371, 0.128),
             (0.519, 0.116, 0.792, 0.138), (0.081, 0.124, 0.38, 0.146)]
    items = [{"x0": a, "top": b, "x1": c, "bottom": d} for a, b, c, d in boxes]
    assert len(rows_by_overlap(items)) >= 4
