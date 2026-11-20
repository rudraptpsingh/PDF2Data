"""Tables without rulings, found from alignment: consecutive rows whose cells line up in columns (left, right or
centre edges), with at least three columns or a column of figures. Prose in two columns and label/value lines are
not tables."""
import json

from tests.test_styled_headings import lines, pdf


def _right(x_right, y, text, size=10):
    from local_vector_batch.structure_candidates import _GLYPH       # Helvetica widths, 1/1000 em
    width = sum(_GLYPH.get(c, 556) for c in text) * size / 1000
    return f"BT /F1 {size} Tf {x_right - width:.2f} {y} Td ({text}) Tj ET\n"


def _extract(tmp_path, content):
    from local_vector_batch.pdf_ingest import extract_pdf
    extract_pdf(pdf(tmp_path / "d.pdf", content), tmp_path / "out")
    return json.loads((tmp_path / "out" / "tables.json").read_text())


ROWS = [["Item", "Quantity", "Amount"], ["Support", "12", "1,440.00"], ["Training", "3", "240.00"],
        ["Licences", "40", "12,000.00"], ["Total", "", "13,680.00"]]


def test_unruled_table_from_aligned_columns(tmp_path):
    body = lines(60, 790, ["The charges for the period are set out below and are payable in full."])
    table = ""
    for i, row in enumerate(ROWS):
        y = 760 - i * 16
        table += f"BT /F1 10 Tf 60 {y} Td ({row[0]}) Tj ET\n"
        table += _right(300, y, row[1]) if row[1] else ""
        table += _right(450, y, row[2])
    after = lines(60, 660, ["Payment is due within thirty days of the date of this invoice."])
    tables = _extract(tmp_path, body + table + after)
    aligned = [t for t in tables if t["extraction_basis"] == "aligned_text_columns"]
    assert len(aligned) == 1
    assert aligned[0]["cells"] == ROWS
    assert aligned[0]["status"] == "needs_row_column_review"
    assert len(aligned[0]["grid_x"]) == 4 and len(aligned[0]["grid_y"]) == 6


def test_two_column_prose_and_label_lines_are_not_tables(tmp_path):
    L = ["The parties agree that services are", "delivered in accordance with the", "schedule agreed in writing and",
         "each invoice states the period and"]
    R = ["Either party may end this agreement", "by written notice to the other at", "the address given above and the",
         "notice takes effect thirty days"]
    form = lines(60, 600, ["Invoice No: INV-2026-0417", "Invoice Date: 14 March 2026", "Customer: Contoso Ltd"])
    tables = _extract(tmp_path, lines(50, 780, L) + lines(320, 780, R) + form)
    assert [t for t in tables if t["extraction_basis"] == "aligned_text_columns"] == []


def test_cells_that_wrap_over_several_lines_stay_in_their_row():
    """A description that wraps puts lines under its cell with nothing in the first column: they continue that
    cell (joined by newlines), they do not start rows or break the table."""
    from local_vector_batch.aligned_tables import aligned_table_candidates
    L = lambda y, x0, x1, t: {"page": 1, "text": t, "bbox": [x0, y, x1, y + 0.012]}
    lines = [L(0.10, 0.10, 0.15, "No."), L(0.10, 0.18, 0.33, "Description"), L(0.10, 0.73, 0.79, "Amount"),
             L(0.125, 0.10, 0.11, "1"), L(0.125, 0.18, 0.52, "Annual maintenance of the control"), L(0.125, 0.70, 0.79, "48,000.00"),
             L(0.141, 0.18, 0.41, "system, including spares"),
             L(0.168, 0.10, 0.11, "2"), L(0.168, 0.18, 0.34, "On-site support"), L(0.168, 0.70, 0.79, "12,500.00"),
             L(0.193, 0.10, 0.11, "3"), L(0.193, 0.18, 0.50, "Operator training for six staff"), L(0.193, 0.71, 0.79, "9,000.00"),
             L(0.209, 0.18, 0.47, "members over two sessions at"), L(0.225, 0.18, 0.27, "the plant"),
             L(0.27, 0.10, 0.50, "All amounts are exclusive of tax.")]
    [t] = aligned_table_candidates(lines, page=1, page_id="d:p1", pdf_width=595, pdf_height=842)
    assert t["cells"] == [["No.", "Description", "Amount"],
                          ["1", "Annual maintenance of the control\nsystem, including spares", "48,000.00"],
                          ["2", "On-site support", "12,500.00"],
                          ["3", "Operator training for six staff\nmembers over two sessions at\nthe plant", "9,000.00"]]
    assert len(t["grid_y"]) == 5
