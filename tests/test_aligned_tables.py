"""Tables without rulings, found from alignment: consecutive rows whose cells line up in columns (left, right or
centre edges), with at least three columns or a column of figures. Prose in two columns and label/value lines are
not tables."""
import json

from tests.test_styled_headings import lines, pdf


def _right(x_right, y, text, size=10):
    width = 0.5 * size * len(text)          # Helvetica figures are 0.556 em; close enough to right-align by eye
    return f"BT /F1 {size} Tf {x_right - 0.556 * size * len(text):.2f} {y} Td ({text}) Tj ET\n"


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
