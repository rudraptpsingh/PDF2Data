import json

import pytest

pdfplumber = pytest.importorskip("pdfplumber")

from local_vector_batch.evidence_package import verify_package
from local_vector_batch.pdf_ingest import extract_pdf
from local_vector_batch.pipeline import Config, run


class _Page:
    objects = {"char": [1]}
    images = []

    def extract_text(self):
        return "The greater of 99% ACE or the reference amount applies. BESS charging needs at least 51%."

    def find_tables(self):
        return []


class _PDF:
    pages = [_Page()]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _extracted(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(source)
    monkeypatch.setattr(pdfplumber, "open", lambda _: _PDF())
    output = tmp_path / "out"
    extract_pdf(source, output, source_url="https://example.gov/official.pdf", ocr="off")
    return output


def test_evidence_package_links_original_page_candidates_and_vectors(tmp_path, monkeypatch):
    output = _extracted(tmp_path, monkeypatch)
    receipt = verify_package(output)
    presentation = json.loads((output / "presentation/manifest.json").read_text())
    assert presentation["source_url"] == "https://example.gov/official.pdf"
    assert presentation["pages"][0]["citation_url"] == "https://example.gov/official.pdf#page=1"
    assert "Open original PDF page" in (output / "presentation/pages/page-0001.md").read_text()
    assert "https://example.gov/official.pdf#page=1" in (output / "presentation/pages/page-0001.html").read_text()
    layout = json.loads((output / "presentation/layout.jsonl").read_text())
    assert layout["page_id"] == presentation["pages"][0]["page_id"]
    assert layout["blocks"][0]["text"] == json.loads((output / "pages.jsonl").read_text())["text"]
    assert receipt["pages"] == receipt["embedding_inputs"] == 1
    assert receipt["fact_candidates"] == 2
    assert sum(receipt["fact_candidates_by_kind"].values()) == receipt["fact_candidates"]
    assert receipt["fact_candidates_by_status"] == {"unverified_candidate": 2}
    assert sum(receipt["structure_candidates_by_kind"].values()) == receipt["structure_candidates"]
    assert receipt["structure_candidates_by_status"] == {"review_required": receipt["structure_candidates"]}
    assert receipt["checked_structure_spans"] >= 2
    assert receipt["verified_facts"] == 0 and not receipt["version_resolved"]
    assert receipt["integrity"] == "passed"
    assert receipt["page_text_readiness"] == "review_required"
    assert receipt["review_required_pages"] == [1]
    assert verify_package(output, source_pdf=tmp_path / "source.pdf")["original_pdf_checked"]
    wrong = tmp_path / "wrong.pdf"
    wrong.write_bytes(b"%PDF-other")
    with pytest.raises(ValueError, match="original PDF hash mismatch"):
        verify_package(output, source_pdf=wrong)
    package = json.loads((output / "evidence-package.json").read_text())
    assert package["document"]["version"]["status"] == "unresolved"
    assert package["relations"]["status"] == "review_required"

    class Encoder:
        device = "test:cpu"

        def encode(self, texts):
            return [[1., 0., 0.] for _ in texts]

    vectors = tmp_path / "vectors"
    run(Config(output / "embedding-input.jsonl", vectors, min_chars=1), encoder_factory=Encoder)
    assert verify_package(output, vectors)["vectors_checked"] == 1
    batch = vectors / "batch-000000.ndjson"
    rows = [json.loads(line) for line in batch.read_text().splitlines()]
    rows[0]["metadata"]["page_text_sha256"] = "0" * 64
    batch.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="batch checksum mismatch"):
        verify_package(output, vectors)


def test_evidence_package_detects_tampering_and_cannot_promote_candidates(tmp_path, monkeypatch):
    output = _extracted(tmp_path, monkeypatch)
    package_path = output / "evidence-package.json"
    package = json.loads(package_path.read_text())
    package["facts"]["status"] = "verified"
    package_path.write_text(json.dumps(package))
    with pytest.raises(ValueError, match="improperly promoted"):
        verify_package(output)
    package["facts"]["status"] = "unverified_candidates_only"
    package_path.write_text(json.dumps(package))
    with (output / "pages.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        verify_package(output)


def test_presentation_tampering_is_rejected(tmp_path, monkeypatch):
    output = _extracted(tmp_path, monkeypatch)
    path = output / "presentation/pages/page-0001.html"
    path.write_text(path.read_text() + "changed")
    with pytest.raises(ValueError, match="presentation artifact hash mismatch"):
        verify_package(output)


def test_paddle_layout_import_stays_candidate_and_source_bound(tmp_path, monkeypatch):
    import hashlib
    from local_vector_batch.paddle_layout_import import import_paddle_layout
    output = _extracted(tmp_path, monkeypatch)
    report = json.loads((output / "report.json").read_text())
    candidate_dir = tmp_path / "paddle" / "page-1"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "page-1_res.json").write_text(json.dumps({"page_index": None,
        "parsing_res_list": [{"block_label": "table", "block_content": "<table><tr><td>51%</td></tr></table>",
                              "block_bbox": [1, 2, 100, 200], "block_order": 1, "block_id": 0}]}))
    image_path = tmp_path / "paddle" / "page-1.png"
    image_path.write_bytes(b"source page raster")
    manifest = tmp_path / "paddle" / "manifest.json"
    manifest.write_text(json.dumps({"engine": "PPStructureV3", "source_pdf_sha256": report["source_pdf_sha256"],
        "pages": [{"physical_pdf_page": 1, "output_dir": str(candidate_dir),
                   "source_image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}]}))
    result = import_paddle_layout(output, tmp_path / "source.pdf", manifest)
    assert result["blocks"] == 1 and result["status"] == "review_required"
    assert verify_package(output)["verified_facts"] == 0
    assert "Unverified OCR" in (output / "presentation/pages/page-0001-paddle.html").read_text()
    manifest.write_text(json.dumps({"engine": "PPStructureV3", "source_pdf_sha256": "b" * 64, "pages": []}))
    with pytest.raises(ValueError, match="source PDF or engine identity mismatch"):
        import_paddle_layout(output, tmp_path / "source.pdf", manifest)


def test_physical_pdf_page_sequence_is_verified_even_with_consistent_artifact_hashes(tmp_path, monkeypatch):
    import hashlib
    output = _extracted(tmp_path, monkeypatch)
    page = json.loads((output / "pages.jsonl").read_text())
    page["page"] = 3
    page["id"] = page["id"].rsplit(":p", 1)[0] + ":p3"
    (output / "pages.jsonl").write_text(json.dumps(page)+"\n")
    path = output / "evidence-package.json"
    package = json.loads(path.read_text())
    package["files"]["pages.jsonl"]["sha256"] = hashlib.sha256((output / "pages.jsonl").read_bytes()).hexdigest()
    path.write_text(json.dumps(package))
    with pytest.raises(ValueError, match="page sequence"):
        verify_package(output)
