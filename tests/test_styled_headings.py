"""Headings without numbers (larger or bold type, short, not a sentence) become styled_heading candidates."""
import json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from local_vector_batch.structure_candidates import styled_heading_candidates


def pdf(path, content):
    w = PdfWriter()
    page = w.add_blank_page(595, 842)
    fonts = {k: w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                                NameObject("/BaseFont"): NameObject(b)}))
             for k, b in (("/F1", "/Times-Roman"), ("/F3", "/Helvetica-Bold"))}
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject(k): v for k, v in fonts.items()})})
    s = DecodedStreamObject()
    s.set_data(content.encode("latin-1"))
    page[NameObject("/Contents")] = w._add_object(s)
    with open(path, "wb") as f:
        w.write(f)
    return path


def lines(x, y, rows, font="/F1", size=11, lead=16):
    return f"BT {font} {size} Tf {lead} TL {x} {y} Td " + " ".join(f"({r}) Tj T*" for r in rows) + " ET\n"


BODY = ["The amount of each line is its quantity times its rate.", "Payment is due within thirty days of receipt.",
        "Either party may end this agreement by written notice."]


def test_native_heading_by_size_and_weight(tmp_path):
    from local_vector_batch.pdf_ingest import extract_pdf
    source = pdf(tmp_path / "h.pdf", lines(60, 790, ["Calculation note"], "/F3", 14) + lines(60, 760, BODY)
                 + lines(60, 700, ["Payment schedule"], "/F3", 11) + lines(60, 680, BODY[:1]))
    extract_pdf(source, tmp_path / "out")
    rows = [json.loads(l) for l in (tmp_path / "out" / "structure-candidates.jsonl").read_text().splitlines()]
    styled = [r for r in rows if r["kind"] == "styled_heading"]
    assert [r["evidence"][0]["quote"] for r in styled] == ["Calculation note", "Payment schedule"]
    page = json.loads((tmp_path / "out" / "pages.jsonl").read_text().splitlines()[0])
    for r in styled:
        e = r["evidence"][0]
        assert page["text"][e["char_start"]:e["char_start"] + e["char_length"]] == e["quote"]


def test_sentences_and_body_lines_are_not_headings():
    page = {"page": 1, "id": "d:p1", "text_sha256": "x", "method": "pdf_text",
            "text": "INVOICE\nInvoice No: INV-2026-0417\nThe total is due within thirty days of receipt of this invoice.\nNotes."}
    metrics = [("INVOICE", 16, True), ("Invoice No: INV-2026-0417", 11, False),
               ("The total is due within thirty days of receipt of this invoice.", 11, False), ("Notes.", 14, True)]
    out = styled_heading_candidates(page, metrics)
    assert [r["evidence"][0]["quote"] for r in out] == ["INVOICE"]


def test_ocr_heading_by_line_height():
    page = {"page": 1, "id": "d:p1", "text_sha256": "x", "method": "rapidocr",
            "text": "Quarterly review\nRevenue grew in every region this quarter.\nCosts were flat.\nOutlook"}
    metrics = [("Quarterly review", 0.030, False), ("Revenue grew in every region this quarter.", 0.018, False),
               ("Costs were flat.", 0.018, False), ("Outlook", 0.029, False)]
    assert [r["evidence"][0]["quote"] for r in styled_heading_candidates(page, metrics)] == ["Quarterly review", "Outlook"]


def test_bold_names_signatures_and_label_values_are_not_headings():
    """Front matter and signature blocks set in body-size bold: names with roles, repeated signature marks and
    label/value lines are not headings; a short bold section title among them is."""
    from local_vector_batch.structure_candidates import styled_heading_candidates
    body = "The applicant has made the present application under the relevant section of the act."
    lines = ["In the matter of", "Shri A. Kumar, Chairperson", "Date of Order: 13 November 2026", "ORDER", body,
             "Sd/- Sd/- Sd/-", "(A. Kumar) (B. Rao) (C. Das)", "Member Member Chairperson"]
    page = {"page": 1, "text": "\n".join(lines)}
    metrics = [(t, 12, t != body) for t in lines] + [(body, 12, False)] * 5
    out = styled_heading_candidates(page, metrics)
    assert [r["evidence"][0]["quote"] for r in out] == ["In the matter of", "ORDER"]
