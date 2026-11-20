"""Run-in headings: a bold phrase opening a line, followed by regular text on the same line ("Background. The
petitioner filed ..."), become run_in_heading candidates, and the line's lines.jsonl row carries lead_bold."""
import json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


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


def plain(y, text, font="/F1"):
    return f"BT {font} 11 Tf 60 {y} Td ({text}) Tj ET\n"


def run_in(y, bold, rest):
    """One line: a bold run then regular text, two Tj in one BT block with a font switch."""
    return f"BT /F3 11 Tf 60 {y} Td ({bold}) Tj /F1 11 Tf ( {rest}) Tj ET\n"


BODY = ["The petitioner has filed the present petition for approval of tariff.",
        "The respondents have filed their replies to the petition in time.",
        "The matter was heard on the dates noted in the record of proceedings."]


def body(y):
    return "".join(plain(y - 16 * i, t) for i, t in enumerate(BODY))


def extract(tmp_path, content):
    from local_vector_batch.pdf_ingest import extract_pdf
    source = pdf(tmp_path / "r.pdf", content)
    extract_pdf(source, tmp_path / "out")
    out = tmp_path / "out"
    structures = [json.loads(l) for l in (out / "structure-candidates.jsonl").read_text().splitlines()]
    lines = [json.loads(l) for l in (out / "lines.jsonl").read_text().splitlines()]
    page = json.loads((out / "pages.jsonl").read_text().splitlines()[0])
    return [r for r in structures if r["kind"] == "run_in_heading"], lines, page


def test_run_in_headings_ending_in_period_colon_and_dash(tmp_path):
    content = (body(800)
               + run_in(740, "Background.", "The petitioner filed the petition on the date noted.")
               + run_in(720, "Submissions of the Respondent:", "The respondent opposed the prayer.")
               + run_in(700, "Issue No. 1 -", "Whether the petition is maintainable at all.")
               + body(680))
    found, lines, page = extract(tmp_path, content)
    quotes = [r["evidence"][0]["quote"] for r in found]
    assert quotes == ["Background.", "Submissions of the Respondent:", "Issue No. 1 -"]
    for r in found:
        assert r["status"] == "review_required" and r["review_reasons"] == ["hierarchy_not_inferred"]
        e = r["evidence"][0]
        assert page["text"][e["char_start"]:e["char_start"] + e["char_length"]] == e["quote"]
    leads = {l["text"]: l["lead_bold"] for l in lines if "lead_bold" in l}
    assert sorted(leads.values()) == sorted(len(q) for q in quotes)
    for text, n in leads.items():
        assert text[:n] in quotes
    assert all("lead_bold" not in l for l in lines if l["text"] in BODY)


def test_capital_after_bold_prefix_without_punctuation(tmp_path):
    found, _, _ = extract(tmp_path, body(800) + run_in(740, "Background", "The petitioner filed the petition.") + body(720))
    assert [r["evidence"][0]["quote"] for r in found] == ["Background"]


def test_fully_bold_line_is_a_styled_heading_not_run_in(tmp_path):
    found, lines, _ = extract(tmp_path, body(800) + plain(740, "Submissions of the Respondent:", "/F3") + body(720))
    assert found == []
    assert all("lead_bold" not in l for l in lines)


def test_bold_list_marker_alone_is_not_a_heading(tmp_path):
    found, lines, _ = extract(tmp_path, body(800) + run_in(740, "1.", "The petitioner filed the petition on time.")
                              + run_in(720, "(a)", "The respondent filed a reply.") + body(700))
    assert found == []
    assert all("lead_bold" not in l for l in lines)


def test_bold_word_inside_a_line_is_not_a_heading(tmp_path):
    content = (body(800) + "BT /F1 11 Tf 60 740 Td (The petitioner relies on) Tj /F3 11 Tf ( Regulation 5.) Tj "
               "/F1 11 Tf ( of the Regulations.) Tj ET\n" + body(720))
    found, lines, _ = extract(tmp_path, content)
    assert found == []
    assert all("lead_bold" not in l for l in lines)


def test_body_set_in_bold_has_no_run_in_headings(tmp_path):
    bold_body = "".join(plain(800 - 16 * i, t, "/F3") for i, t in enumerate(BODY + BODY))
    found, lines, _ = extract(tmp_path, bold_body + run_in(680, "Background.", "The petitioner filed the petition."))
    assert found == []
    assert all("lead_bold" not in l for l in lines)


def test_prefix_rule_on_words():
    """The prefix rule on (text, bold) words, including an en dash ending."""
    from local_vector_batch.layout_lines import run_in_prefix
    w = lambda bold, rest: [(t, True) for t in bold.split()] + [(t, False) for t in rest.split()]
    assert run_in_prefix(w("Issue No. 1 –", "whether the petition is maintainable")) == 4
    assert run_in_prefix(w("Background.", "the petitioner filed")) == 1
    assert run_in_prefix(w("Shri A. Kumar", "appeared for the petitioner")) == 0
    assert run_in_prefix(w("(iv)", "The respondent")) == 0
    assert run_in_prefix(w("1.2", "The respondent")) == 0
    assert run_in_prefix(w("one two three four five six seven eight nine ten eleven.", "The rest")) == 0
