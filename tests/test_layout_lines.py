"""lines.jsonl: every printed line with its box, type, alignment, indent and exact span of the page text, so a
document can be laid out again from what was extracted."""
import json

from tests.test_styled_headings import lines, pdf


def _extract(tmp_path, content):
    from local_vector_batch.pdf_ingest import extract_pdf
    extract_pdf(pdf(tmp_path / "d.pdf", content), tmp_path / "out")
    page = json.loads((tmp_path / "out" / "pages.jsonl").read_text().splitlines()[0])
    rows = [json.loads(l) for l in (tmp_path / "out" / "lines.jsonl").read_text().splitlines()]
    return page, rows


def test_native_lines_carry_box_type_alignment_indent_and_exact_span(tmp_path):
    title = "BT /F3 16 Tf 230 790 Td (Annual Report) Tj ET\n"            # centred, bold, larger
    body = lines(60, 750, ["1. Scope of the agreement covers all services."])
    sub = lines(90, 734, ["(a) delivery of goods to the buyer;", "(b) installation at the site."])
    page, rows = _extract(tmp_path, title + body + sub)
    assert [r["text"] for r in rows] == ["Annual Report", "1. Scope of the agreement covers all services.",
                                         "(a) delivery of goods to the buyer;", "(b) installation at the site."]
    t, b, a = rows[0], rows[1], rows[2]
    assert (t["bold"], b["bold"], t["size"], b["size"]) == (True, False, 16, 11)
    assert t["align"] == "center" and b["align"] == "left"
    assert a["indent"] > b["indent"] >= 0 and round(a["indent"] - b["indent"], 2) == round(30 / 595, 2)
    for r in rows:
        assert len(r["bbox"]) == 4 and all(0 <= v <= 1 for v in r["bbox"]) and r["bbox"][0] < r["bbox"][2]
        assert page["text"][r["char_start"]:r["char_start"] + r["char_length"]] == r["text"]
        assert r["page"] == 1 and r["font"]
    assert rows[0]["bbox"][1] < rows[1]["bbox"][1] < rows[2]["bbox"][1]


def test_two_columns_are_separate_lines(tmp_path):
    left = lines(50, 780, ["The parties agree that services are", "delivered in accordance with the"])
    right = lines(320, 780, ["Either party may end this agreement", "by written notice to the other"])
    page, rows = _extract(tmp_path, left + right)
    assert [r["text"] for r in rows] == ["The parties agree that services are", "delivered in accordance with the",
                                         "Either party may end this agreement", "by written notice to the other"]
    assert rows[2]["bbox"][0] > 0.5 and rows[0]["bbox"][2] < 0.5


def test_ocr_lines_from_reader_boxes():
    from local_vector_batch.layout_lines import ocr_lines
    page = {"page": 1, "text": "Quarterly review\nRevenue rose in the quarter.", "pdf_width": 595, "pdf_height": 842}
    ocr = {"lines": [{"text": "Quarterly review", "bbox": [0.1, 0.05, 0.4, 0.08]},
                     {"text": "Revenue rose in the quarter.", "bbox": [0.1, 0.1, 0.6, 0.118]}]}
    rows = ocr_lines(page, ocr)
    assert [(r["text"], r["char_start"]) for r in rows] == [("Quarterly review", 0), ("Revenue rose in the quarter.", 17)]
    assert rows[0]["size"] > rows[1]["size"] and rows[0]["bold"] is None
