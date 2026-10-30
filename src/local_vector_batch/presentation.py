"""Deterministic, browsable views of a source-bound PDF evidence package."""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path
import re


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _html(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{escape(title)}</title><style>body{{font:16px/1.55 system-ui;max-width:76ch;"
            "margin:2rem auto;padding:0 1rem;color:#20242a}}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
            "font:inherit}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd2d9;"
            "padding:.35rem;text-align:left}small{color:#606b78}</style>"
            f"<main>{body}</main></html>\n")


def export_presentation(output: Path, report: dict) -> dict:
    """Render selected page text; never silently present candidate tables as law."""
    output = Path(output)
    root = output / "presentation"
    pages = _rows(output / "pages.jsonl")
    tables = json.loads((output / "tables.json").read_text(encoding="utf-8"))
    structures = _rows(output / "structure-candidates.jsonl")
    facts = _rows(output / "fact-candidates.jsonl")
    paddle_path = output / "paddle-layout-candidates.jsonl"
    paddle = _rows(paddle_path) if paddle_path.is_file() else []
    paddle_by_page = {row["page"]: row for row in paddle}
    tables_by_page: dict[int, list[dict]] = {}
    for table in tables:
        tables_by_page.setdefault(table["page"], []).append(table)
    structures_by_page: dict[int, list[dict]] = {}
    for candidate in structures:
        evidence = candidate.get("evidence") or []
        if evidence:
            structures_by_page.setdefault(evidence[0]["page"], []).append(candidate)
    facts_by_page: dict[int, list[dict]] = {}
    for candidate in facts:
        facts_by_page.setdefault(candidate["page"], []).append(candidate)
    source = report.get("source_url")
    files: dict[str, str] = {}
    page_entries = []
    layout_rows = []
    for page in pages:
        number = page["page"]
        stem = f"pages/page-{number:04d}"
        cite = f"{source}#page={number}" if source else None
        label = f"PDF page {number}"
        status = page["status"].replace("_", " ")
        metadata = f"{status} · {page['method']} · text SHA-256 {page['text_sha256']}"
        source_link = f"[Open original PDF page]({cite})\n\n" if cite else ""
        table_links = "".join(
            f"- [Table candidate {index}](../tables/{re.sub(r'[^a-zA-Z0-9._-]', '_', table['id'])}.html) — verify rows against the PDF\n"
            for index, table in enumerate(tables_by_page.get(number, []), 1))
        paddle_row = paddle_by_page.get(number)
        paddle_link = f"[Inspect Paddle layout candidate](page-{number:04d}-paddle.html)\n\n" if paddle_row else ""
        markdown = (f"# {label}\n\n{source_link}{metadata}\n\n"
                    f"````text\n{page['text']}\n````\n"
                    + (f"\n## Table candidates\n\n{table_links}" if table_links else "") + paddle_link)
        html_link = (f'<p><a href="{escape(cite, quote=True)}">Open original PDF page</a></p>'
                     if cite else "")
        html = _html(label, f"<h1>{label}</h1>{html_link}<small>{escape(metadata)}</small>"
                     f"<pre>{escape(page['text'])}</pre>"
                     + ("<h2>Table candidates</h2><ul>" + "".join(
                         f'<li><a href="../tables/{escape(re.sub(r"[^a-zA-Z0-9._-]", "_", table["id"]), quote=True)}.html">'
                         f'Table {index}</a> · needs row and column review</li>'
                         for index, table in enumerate(tables_by_page.get(number, []), 1)) + "</ul>"
                         if table_links else "")
                     + (f'<p><a href="page-{number:04d}-paddle.html">Inspect Paddle layout candidate</a></p>'
                        if paddle_row else ""))
        files[stem + ".md"] = _write(root / (stem + ".md"), markdown)
        files[stem + ".html"] = _write(root / (stem + ".html"), html)
        if paddle_row:
            blocks_html = "".join(
                f"<section><h2>{escape(block['label'])}</h2><small>bbox {escape(str(block['bbox']))}</small>"
                f"<pre>{escape(block['content'])}</pre></section>" for block in paddle_row["blocks"])
            files[stem + "-paddle.html"] = _write(root / (stem + "-paddle.html"),
                _html(f"Paddle candidate · page {number}",
                      f"<h1>Paddle layout candidate · page {number}</h1>{html_link}"
                      "<p>Unverified OCR and table layout. Check every value against the original PDF.</p>" + blocks_html))
        page_entries.append({"page": number, "page_id": page["id"],
                             "status": page["status"], "method": page["method"],
                             "text_sha256": page["text_sha256"], "source_pdf_sha256": report["source_pdf_sha256"],
                             "citation_url": cite, "markdown": stem + ".md", "html": stem + ".html"})
        layout_rows.append({"page": number, "page_id": page["id"], "source_pdf_sha256": report["source_pdf_sha256"],
                            "page_text_sha256": page["text_sha256"], "status": page["status"],
                            "blocks": [{"type": "text", "text": page["text"], "method": page["method"],
                                        "status": page["status"]}] + [
                                {"type": "table_candidate", "table_id": table["id"],
                                 "bbox": table.get("bbox"), "cells": table.get("cells", []),
                                 "cell_spans": table.get("cell_spans", []), "status": table.get("status", "review_required")}
                                for table in tables_by_page.get(number, [])],
                            "structure_candidates": [{"kind": item["kind"], "evidence": item.get("evidence", []),
                                                      "status": item["status"]}
                                                     for item in structures_by_page.get(number, [])],
                            "fact_candidates": [{"kind": item["kind"], "value_text": item["value_text"],
                                                 "char_start": item["char_start"], "char_length": item["char_length"],
                                                 "status": item["status"]}
                                                for item in facts_by_page.get(number, [])],
                            "paddle_candidate": paddle_row})
    table_entries = []
    for table in tables:
        page = table["page"]
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", table["id"])
        stem = f"tables/{safe_id}"
        status = table.get("status", "review_required")
        warning = "Unverified table candidate — check the original PDF before using any value."
        cells = table.get("cells", [])
        rows_html = "".join("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
                            for row in cells)
        body = f"<h1>PDF page {page} · table candidate</h1><p>{escape(warning)}</p>"
        body += f"<table>{rows_html}</table>"
        files[stem + ".html"] = _write(root / (stem + ".html"), _html(f"Page {page} table", body))
        table_entries.append({"id": table["id"], "page": page, "status": status,
                              "html": stem + ".html", "source_pdf_sha256": report["source_pdf_sha256"]})
    links = "\n".join(f"- [PDF page {p['page']}]({p['markdown']}) — {p['status']}"
                      for p in page_entries)
    files["index.md"] = _write(root / "index.md", f"# {report['title']}\n\n{links}\n")
    links_html = "".join(f'<li><a href="{escape(p["html"], quote=True)}">PDF page {p["page"]}</a>'
                         f' <small>{escape(p["status"])}</small></li>' for p in page_entries)
    files["index.html"] = _write(root / "index.html", _html(report["title"],
        f"<h1>{escape(report['title'])}</h1><ol>{links_html}</ol>"))
    files["layout.jsonl"] = _write(root / "layout.jsonl", "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in layout_rows))
    manifest = {"schema_version": 1, "document_id": report["document_id"],
                "source_url": source, "source_pdf_sha256": report["source_pdf_sha256"],
                "pages": page_entries, "table_candidates": table_entries,
                "files": files}
    _write(root / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return {"manifest": "presentation/manifest.json",
            "manifest_sha256": _sha((root / "manifest.json").read_bytes()),
            "page_count": len(page_entries), "table_candidates": len(table_entries)}


def verify_presentation(output: Path, package: dict, pages: list[dict]) -> None:
    entry = package.get("presentation")
    if entry is None:  # Older immutable packages remain readable.
        return
    path = output / entry["manifest"]
    if _sha(path.read_bytes()) != entry.get("manifest_sha256"):
        raise ValueError("presentation manifest hash mismatch")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("document_id") != package["document"]["id"] or
            manifest.get("source_pdf_sha256") != package["document"]["pdf_sha256"] or
            manifest.get("source_url") != package["document"]["source_url"] or
            len(manifest.get("pages", [])) != len(pages)):
        raise ValueError("presentation source or page identity mismatch")
    for page, rendered in zip(pages, manifest["pages"]):
        if (page["id"], page["page"], page["text_sha256"], page["status"]) != (
                rendered["page_id"], rendered["page"], rendered["text_sha256"], rendered["status"]):
            raise ValueError("presentation page differs from selected PDF text")
    layout = _rows(output / "presentation" / "layout.jsonl")
    if len(layout) != len(pages):
        raise ValueError("presentation layout page coverage mismatch")
    for page, row in zip(pages, layout):
        if (row.get("page_id") != page["id"] or row.get("page_text_sha256") != page["text_sha256"] or
                row.get("source_pdf_sha256") != package["document"]["pdf_sha256"] or
                not row.get("blocks") or row["blocks"][0].get("text") != page["text"]):
            raise ValueError("presentation layout differs from source page")
    for name, expected in manifest.get("files", {}).items():
        if Path(name).is_absolute() or ".." in Path(name).parts or _sha((output / "presentation" / name).read_bytes()) != expected:
            raise ValueError("presentation artifact hash mismatch: " + name)
