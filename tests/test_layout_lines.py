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
    title = "BT /F3 16 Tf 242 790 Td (Annual Report) Tj ET\n"            # centred, bold, larger
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
    L = ["The parties agree that services are", "delivered in accordance with the", "schedule agreed in writing and",
         "each invoice states the period and", "the agreed rate for that period."]
    R = ["Either party may end this agreement", "by written notice to the other at", "the address given above and the",
         "notice takes effect thirty days", "after the day it is received there."]
    page, rows = _extract(tmp_path, lines(50, 780, L) + lines(320, 780, R))
    assert [r["text"] for r in rows] == L + R
    assert rows[len(L)]["bbox"][0] > 0.5 and rows[0]["bbox"][2] < 0.5


def test_ocr_lines_from_reader_boxes():
    from local_vector_batch.layout_lines import ocr_lines
    page = {"page": 1, "text": "Quarterly review\nRevenue rose in the quarter.", "pdf_width": 595, "pdf_height": 842}
    ocr = {"lines": [{"text": "Quarterly review", "bbox": [0.1, 0.05, 0.4, 0.08]},
                     {"text": "Revenue rose in the quarter.", "bbox": [0.1, 0.1, 0.6, 0.118]}]}
    rows = ocr_lines(page, ocr)
    assert [(r["text"], r["char_start"]) for r in rows] == [("Quarterly review", 0), ("Revenue rose in the quarter.", 17)]
    assert rows[0]["size"] > rows[1]["size"] and rows[0]["bold"] is None


def test_indented_justified_lines_are_left_and_split_words_still_locate():
    from local_vector_batch.layout_lines import _finish
    page = {"page": 1, "text": "TITLE\nDate: 13th May\n(a) The licensee shall comply with all of it\nand with the rest of the order too"}
    mk = lambda x0, x1, top, words: {"x0": x0, "x1": x1, "top": top, "bottom": top + 10, "words": words, "size": 11,
                                     "bold": False, "italic": False, "font": "Times"}
    rows = _finish(page, [mk(270, 330, 50, ["TITLE"]), mk(60, 200, 70, ["Date:", "13", "th", "May"]),
                          mk(107, 523, 90, ["(a)", "The", "licensee", "shall", "comply", "with", "all", "of", "it"]),
                          mk(107, 523, 104, ["and", "with", "the", "rest", "of", "the", "order", "too"]),
                          mk(60, 535, 120, ["x"])], 595, 842)
    assert [r["align"] for r in rows[:4]] == ["center", "left", "left", "left"]
    assert rows[1]["text"] == "Date: 13th May" and rows[1]["char_start"] == 6
