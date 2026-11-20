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


def test_skewed_table_rows_stay_whole_when_the_slope_is_known():
    # 2 degrees of skew: across the page a row drifts about one row height. Each OCR line carries its baseline slope.
    import math
    slope = math.tan(math.radians(2)) * 0.7   # in normalised units (page aspect folded in)
    rows, expect = [], []
    for r in range(5):
        base = 0.2 + r * 0.03
        for c, x in enumerate((0.1, 0.4, 0.6, 0.8)):
            top = base + slope * x
            rows.append(dict(line(f"r{r}c{c}", x, top, x + 0.08, top + 0.016), baseline_slope=slope))
            expect.append(f"r{r}c{c}")
    assert texts(list(reversed(rows))) == expect


def test_real_skewed_table_scan_reads_row_by_row():
    import json
    from pathlib import Path
    # RapidOCR lines (boxes and baseline slopes) from a ruled table scanned 1.5 degrees off.
    lines = json.loads((Path(__file__).parent / "fixtures_ocr_skew_table.json").read_text())
    out = texts(lines)
    assert out[:4] == ["Item", "Quantity", "Rate", "Amount"]
    for k in range(1, 7):
        i = out.index(f"Service line {k}")
        assert len(out[i + 1:i + 4]) == 3 and not any(t.startswith("Service") for t in out[i + 1:i + 4]), out


def test_ocr_renders_pages_with_antialiasing():
    # Without antialiasing, downsampling a 300 dpi scan to the OCR resolution drops thin strokes, and the detector
    # skipped whole lines of 8 pt text (68 of 70). The page is rendered antialiased.
    from local_vector_batch.ocr import LocalOCR
    from PIL import Image
    seen = {}

    class Page:
        width, height = 595, 842

        def to_image(self, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after render arguments")
    ocr = LocalOCR(engine="rapidocr")
    try:
        ocr.recognize(Page())
    except RuntimeError:
        pass
    assert seen.get("antialias") is True


def test_devanagari_pages_render_at_240_dpi_or_more():
    # The Devanagari recogniser garbles matras and conjuncts at 180 dpi (CER 0.47 on a shaped Hindi scan) and reads
    # the same page exactly at 240 dpi. English keeps its 180 dpi default.
    from local_vector_batch.ocr import LocalOCR
    seen = []

    class Page:
        width, height = 595, 842

        def to_image(self, **kwargs):
            seen.append(kwargs["resolution"])
            raise RuntimeError("stop after render arguments")
    for language in ("hin", "eng"):
        try:
            LocalOCR(engine="rapidocr", language=language).recognize(Page())
        except RuntimeError:
            pass
    assert seen[0] >= 240 and seen[1] == 180
