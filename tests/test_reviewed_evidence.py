import hashlib
import json

import pytest

pdfplumber = pytest.importorskip("pdfplumber")

from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.reviewed_evidence import review_package


class _Table:
    bbox = (0, 0, 100, 100)

    def extract(self):
        return [["Category", "Value"], ["Battery ESS", "100"]]


class _Page:
    objects = {"char": [1]}
    images = []

    def extract_text(self):
        return "Table 2: Battery ESS\nCategory Value\nBattery ESS 100\nProvided that grid use shall not exceed 10% annually."

    def find_tables(self):
        return [_Table()]


class _PDF:
    pages = [_Page()]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _case(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(source)
    monkeypatch.setattr(pdfplumber, "open", lambda _: _PDF())
    output = tmp_path / "out"
    extract_pdf(source, output, source_url="https://example.gov/source.pdf", ocr="off")
    page = json.loads((output / "pages.jsonl").read_text().splitlines()[0])
    text = page["text"]
    row_quote = "Battery ESS 100"
    clause_quote = "Provided that grid use shall not exceed 10% annually."
    units = []
    for kind, quote in (("table_row", row_quote), ("clause", clause_quote)):
        start = text.index(quote)
        unit = {"kind": kind, "page": 1, "page_text_sha256": page["text_sha256"],
                "char_start": start, "char_end": start + len(quote), "exact_quote": quote,
                "heading_path": ["Table 2: Battery ESS"], "subject_key": "battery_ess",
                "logic_operator": "NONE", "conditions": [],
                "facts": [{"source_value_text": "100", "cell_index": 1}] if kind == "table_row" else
                         [{"source_value_text": "10%"}]}
        if kind == "table_row":
            unit.update({"table_id": json.loads((output / "tables.json").read_text())[0]["id"],
                         "row_index": 1, "header_row_index": 0, "cell_indices": [0, 1],
                         "row_cells": ["Battery ESS", "100"],
                         "header_cells": ["Category", "Value"], "caption": "Table 2: Battery ESS"})
        units.append(unit)
    request = {"schema_version": 1, "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "display_rights": "cite_only", "reviewer": "test-reviewer", "reviewed_at": "2026-10-28",
               "basis": "original_pdf_visual_and_text_check", "units": units}
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps(request))
    return source, output, decisions, request


def test_reviewed_export_preserves_table_header_row_and_clause(tmp_path, monkeypatch):
    source, output, decisions, _ = _case(tmp_path, monkeypatch)
    result = review_package(output, decisions, source)
    assert result["reviewed_units"] == 2 and result["reviewed_facts"] == 2
    artifact = json.loads((output / "reviewed-evidence.json").read_text())
    table, clause = artifact["units"]
    assert table["table_anchor"]["header_cells"] == ["Category", "Value"]
    assert "Table 2: Battery ESS" in table["parent_text"]
    assert clause["exact_quote"].endswith("annually.")
    assert all(u["review"]["state"] == "reviewed" for u in artifact["units"])
    assert artifact["document"]["version_status"] == "unresolved"


@pytest.mark.parametrize("change,match", [
    (lambda r: r.update(source_pdf_sha256="0" * 64), "source PDF SHA"),
    (lambda r: r["units"][0].update(row_cells=["Battery ESS", "75"]), "source cells"),
    (lambda r: r["units"][0].update(header_cells=["Category", "Rate"]), "source cells"),
    (lambda r: r["units"][0].update(exact_quote="Battery ESS 75"), "literal"),
    (lambda r: r["units"][1]["facts"][0].update(source_value_text="50%"), "absent"),
])
def test_reviewed_export_rejects_unanchored_or_inherited_claim(tmp_path, monkeypatch, change, match):
    source, output, decisions, request = _case(tmp_path, monkeypatch)
    change(request)
    decisions.write_text(json.dumps(request))
    with pytest.raises(ValueError, match=match):
        review_package(output, decisions, source)


def test_printed_clause_label_cannot_be_silently_normalized(tmp_path, monkeypatch):
    source, output, decisions, request = _case(tmp_path, monkeypatch)
    clause = request["units"][1]
    clause.update(printed_label="8.52", normalized_label="8.5.2",
                  label_normalization_status="reviewer_interpretation")
    decisions.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="printed label must be literal"):
        review_package(output, decisions, source)
