"""Native two-column pages read left column then right; single-column pages and ruled tables are unchanged."""
import pdfplumber
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from local_vector_batch.native_columns import column_ordered_text

LEFT = ["The parties agree that services", "are delivered in accordance with", "the schedule. Each invoice states",
        "the period, the quantity and the", "agreed rate. Payment is due within", "thirty days of receipt."]
RIGHT = ["Either party may end this", "agreement by written notice.", "Notices are sent to the",
         "addresses given above. This", "agreement is governed by the", "laws stated in clause nine."]


def pdf(path, content):
    w = PdfWriter()
    page = w.add_blank_page(595, 842)
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Times-Roman")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    s = DecodedStreamObject()
    s.set_data(content.encode("latin-1"))
    page[NameObject("/Contents")] = w._add_object(s)
    with open(path, "wb") as f:
        w.write(f)
    return path


def lines(x, y, rows, size=11, lead=15):
    return f"BT /F1 {size} Tf {lead} TL {x} {y} Td " + " ".join(f"({r}) Tj T*" for r in rows) + " ET\n"


def text_of(path):
    with pdfplumber.open(path) as doc:
        return column_ordered_text(doc.pages[0])


def test_two_columns_read_left_then_right(tmp_path):
    out = text_of(pdf(tmp_path / "c.pdf", lines(50, 780, LEFT) + lines(320, 780, RIGHT)))
    assert out.split("\n") == LEFT + RIGHT


def test_full_width_title_and_footer_stay_in_place(tmp_path):
    title, footer = "Quarterly operations review and outlook for the year ahead", "Page 1 of 1"
    out = text_of(pdf(tmp_path / "t.pdf", lines(50, 800, [title], 14) + lines(50, 760, LEFT) + lines(320, 760, RIGHT)
                      + lines(270, 40, [footer], 9)))
    assert out.split("\n") == [title, *LEFT, *RIGHT, footer]


def test_single_column_page_is_not_reordered(tmp_path):
    rows = [l + " " + r for l, r in zip(LEFT, RIGHT)]
    assert text_of(pdf(tmp_path / "s.pdf", lines(50, 780, rows))) is None


def test_ruled_table_is_not_reordered(tmp_path):
    grid = "0.5 w " + " ".join(f"50 {780 - i * 20} m 545 {780 - i * 20} l S" for i in range(7)) + " 297 780 m 297 660 l S\n"
    assert text_of(pdf(tmp_path / "tb.pdf", grid + lines(55, 765, LEFT, 10, 20) + lines(302, 765, RIGHT, 10, 20))) is None


def test_short_label_value_pairs_are_not_columns(tmp_path):
    labels = ["Name", "Date", "Total", "Ref", "Due", "Tax"]
    values = ["Northwind", "3 March", "1,250.00", "PO-7", "30 days", "18%"]
    assert text_of(pdf(tmp_path / "kv.pdf", lines(60, 780, labels) + lines(330, 780, values))) is None


def test_extraction_uses_column_order_and_records_it(tmp_path):
    from local_vector_batch.pdf_ingest import extract_pdf
    import json
    source = pdf(tmp_path / "c.pdf", lines(50, 780, LEFT) + lines(320, 780, RIGHT))
    out = tmp_path / "out"
    extract_pdf(source, out)
    page = json.loads((out / "pages.jsonl").read_text().splitlines()[0])
    assert page["text"].split("\n") == LEFT + RIGHT
    issues = [json.loads(l) for l in (out / "issues.jsonl").read_text().splitlines()] if (out / "issues.jsonl").exists() else []
    report = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
    kinds = [i.get("kind") for i in issues] + [i.get("kind") for i in report.get("issues", [])]
    assert "native_column_order" in kinds
