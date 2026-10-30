"""Source-bound two-column label/value relations; never flatten columns.

Rows are geometric, one-to-one and noncrossing. Every primary OCR line stays
in its original page text; independent recognition is confined to each cell.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from io import BytesIO
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import statistics

from .column_order import _box, _overlap, _sha, _tokens


def row_layout_eligible(page: dict, quality: dict) -> bool:
    """Select current, source-complete layout-only holds for automatic jobs."""
    if (
        page.get("method") not in {"vision", "tesseract"}
        or page.get("review_decision")
        or page.get("label_value_proof")
        or quality.get("outcome") != "repair_required"
        or quality.get("page") != page.get("page")
        or quality.get("page_text_sha256") != page.get("text_sha256")
        or quality.get("page_complete") is not True
    ):
        return False
    components = quality.get("components", {})
    if components.get("layout", {}).get("state") == "pass":
        return False
    return all(
        components.get(name, {}).get("state") == "pass"
        for name in (
            "source_identity",
            "text_content",
            "language",
            "source_render",
            "words_and_order",
            "ordered_numbers",
            "ordered_operators",
        )
    )


def _visual_lines(indexes, lines):
    rows = []
    for index in sorted(
        indexes, key=lambda i: (lines[i]["bbox"][1], lines[i]["bbox"][0])
    ):
        box = lines[index]["bbox"]
        matches = [
            row
            for row in rows
            if min(row["bbox"][3], box[3]) - max(row["bbox"][1], box[1])
            > 0.3 * min(row["bbox"][3] - row["bbox"][1], box[3] - box[1])
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous_visual_line_baselines")
        if matches:
            row = matches[0]
            row["indexes"].append(index)
            b = row["bbox"]
            row["bbox"] = [
                min(b[0], box[0]),
                min(b[1], box[1]),
                max(b[2], box[2]),
                max(b[3], box[3]),
            ]
        else:
            rows.append({"indexes": [index], "bbox": list(box)})
    for row in rows:
        row["indexes"].sort(key=lambda i: lines[i]["bbox"][0])
    rows.sort(key=lambda r: r["bbox"][1])
    return rows


def _union(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def label_value_geometry(page: dict, regions: list[dict]) -> dict:
    from .pdf_compare import TEXT_REGION_KINDS

    lines = (page.get("ocr") or {}).get("lines") or []
    if not lines or page["text"] != "\n".join(line["text"] for line in lines):
        raise ValueError("original_ocr_line_text_or_literal_offsets_unavailable")
    if any(r["kind"] not in TEXT_REGION_KINDS | {"table"} for r in regions):
        raise ValueError("visual_region_requires_separate_proof")
    body_regions = [
        r for r in regions if r["kind"] not in {"table", "page_header", "page_footer"}
    ]
    components = []
    for r in sorted(body_regions, key=lambda r: r["bbox_normalized"][0]):
        box = _box(r["bbox_normalized"])
        hits = [c for c in components if min(c[1], box[2]) > max(c[0], box[0])]
        if hits:
            if len(hits) > 1:
                raise ValueError("two_column_gutter_not_unique")
            hits[0][0] = min(hits[0][0], box[0])
            hits[0][1] = max(hits[0][1], box[2])
        else:
            components.append([box[0], box[2]])
    components.sort()
    if len(components) != 2 or components[1][0] - components[0][1] < 0.02:
        raise ValueError("two_column_label_value_geometry_unproven")
    cut = (components[0][1] + components[1][0]) / 2
    furniture = [r for r in regions if r["kind"] in {"page_header", "page_footer"}]
    left = []
    right = []
    header = []
    footer = []
    for i, line in enumerate(lines):
        b = _box(line["bbox"])
        area = (b[2] - b[0]) * (b[3] - b[1])
        hits = [r for r in furniture if _overlap(b, r["bbox_normalized"]) / area > 0.5]
        if len(hits) > 1:
            raise ValueError("overlapping_furniture_regions")
        if hits:
            (header if hits[0]["kind"] == "page_header" else footer).append(i)
        elif b[2] < cut:
            left.append(i)
        elif b[0] > cut:
            right.append(i)
        else:
            raise ValueError("primary_ocr_line_crosses_label_value_gutter")
    if not left or not right:
        raise ValueError("empty_label_or_value_column")
    visual = _visual_lines(left, lines)
    typical = statistics.median(r["bbox"][3] - r["bbox"][1] for r in visual)
    labels = []
    for row in visual:
        if not labels or row["bbox"][1] - labels[-1]["bbox"][3] > 1.05 * typical:
            labels.append({"indexes": list(row["indexes"]), "bbox": list(row["bbox"])})
        else:
            labels[-1]["indexes"].extend(row["indexes"])
            labels[-1]["bbox"] = _union([labels[-1]["bbox"], row["bbox"]])
    if len(labels) < 3:
        raise ValueError("insufficient_independent_label_value_rows")
    rows = []
    assigned = []
    for n, label in enumerate(labels):
        start = label["bbox"][1]
        next_start = labels[n + 1]["bbox"][1] if n + 1 < len(labels) else 1
        values = [
            i
            for i in right
            if start - 0.6 * typical <= lines[i]["bbox"][1] < next_start - 0.6 * typical
        ]
        if not values:
            raise ValueError("label_has_no_unique_value")
        vlines = _visual_lines(values, lines)
        value_indexes = [i for r in vlines for i in r["indexes"]]
        valuebox = _union([lines[i]["bbox"] for i in values])
        if abs(valuebox[1] - start) > 0.6 * typical:
            raise ValueError("label_value_row_starts_not_aligned")
        if (
            n + 1 < len(labels)
            and max(label["bbox"][3], valuebox[3]) >= next_start - 0.2 * typical
        ):
            raise ValueError("crossing_label_value_row_boundaries")
        rows.append(
            {
                "row": n,
                "label": {
                    "line_indexes": label["indexes"],
                    "bbox_normalized": label["bbox"],
                },
                "value": {"line_indexes": value_indexes, "bbox_normalized": valuebox},
            }
        )
        assigned.extend(values)
    if sorted(assigned) != sorted(right):
        raise ValueError("value_lines_not_preserved_exactly_once")
    all_indexes = (
        header
        + footer
        + [
            i
            for row in rows
            for key in ("label", "value")
            for i in row[key]["line_indexes"]
        ]
    )
    if sorted(all_indexes) != list(range(len(lines))):
        raise ValueError("original_line_identity_not_preserved_exactly_once")
    source_offsets = []
    offset = 0
    for line in lines:
        source_offsets.append([offset, len(line["text"])])
        offset += len(line["text"]) + 1
    for row in rows:
        for key in ("label", "value"):
            cell = row[key]
            cell["text"] = "\n".join(lines[i]["text"] for i in cell["line_indexes"])
            cell["source_spans"] = [
                {
                    "char_start": source_offsets[i][0],
                    "char_length": source_offsets[i][1],
                    "quote": lines[i]["text"],
                }
                for i in cell["line_indexes"]
            ]
    return {
        "rows": rows,
        "header_line_indexes": header,
        "footer_line_indexes": footer,
        "gutter_normalized": [
            max(lines[i]["bbox"][2] for i in left),
            min(lines[i]["bbox"][0] for i in right),
        ],
        "original_line_count": len(lines),
        "all_source_line_indexes": sorted(all_indexes),
    }


def _label_words(text):
    match = re.match(r"^\s*(\(?\w{1,6}[).])\s*", text)
    ordinal = match.group(1) if match else ""
    remainder = text[match.end() :] if match else text
    return _tokens(remainder)[0], ordinal


def row_consensus_metrics(geometry: dict, crops: dict, page: dict) -> dict:
    from .quality_gate import load_policy

    try:
        from rapidfuzz.distance import Levenshtein

        similarity = Levenshtein.normalized_similarity
    except ImportError:
        from difflib import SequenceMatcher

        similarity = lambda left, right: SequenceMatcher(
            None, left, right, autojunk=False
        ).ratio()

    policy = load_policy()
    overlap = na = nb = 0
    order_sum = order_weight = 0
    numeric_ok = operator_ok = True
    labels_ok = True
    proofs = []
    pairs = []
    for row in geometry["rows"]:
        for kind in ("label", "value"):
            pairs.append((f"row-{row['row']}-{kind}", row[kind]["text"], kind))
    for kind in ("header", "footer"):
        text = "\n".join(
            page["ocr"]["lines"][i]["text"] for i in geometry[kind + "_line_indexes"]
        )
        if text:
            pairs.append((kind, text, kind))
    for cell_id, primary, kind in pairs:
        independent = crops[cell_id]["text"]
        aw, an, ao = _tokens(primary)
        bw, bn, bo = _tokens(independent)
        count = sum((Counter(aw) & Counter(bw)).values())
        overlap += count
        na += len(aw)
        nb += len(bw)
        weight = max(len(aw), len(bw), 1)
        order = similarity(aw, bw)
        order_sum += order * weight
        order_weight += weight
        exact_numbers = an == bn
        exact_ops = ao == bo
        numeric_ok &= exact_numbers
        operator_ok &= exact_ops
        semantic_exact = True
        ordinal_a = ordinal_b = ""
        if kind == "label":
            semantic_a, ordinal_a = _label_words(primary)
            semantic_b, ordinal_b = _label_words(independent)
            # Some recognizers omit the printed ')' on a separate list marker.
            # Align that marker only when all semantic label words are exact.
            if (
                semantic_a != semantic_b
                and ordinal_a
                and not ordinal_b
                and bw[1:] == semantic_a
                and len(bw[0]) <= len(ordinal_a)
            ):
                semantic_b = bw[1:]
                ordinal_b = bw[0]
            ordinal_core = re.sub(r"\W", "", ordinal_a).casefold()
            if (
                semantic_a != semantic_b
                and ordinal_core
                and bw
                and bw[0].startswith(ordinal_core)
                and len(bw[0]) > len(ordinal_core)
                and [bw[0][len(ordinal_core) :], *bw[1:]] == semantic_a
            ):
                semantic_b = semantic_a
                ordinal_b = bw[0][: len(ordinal_core)]
            semantic_exact = semantic_a == semantic_b
            labels_ok &= semantic_exact
        proofs.append(
            {
                "cell_id": cell_id,
                "primary_text_sha256": _sha(primary),
                "independent_text_sha256": _sha(independent),
                "semantic_label_words_exact": semantic_exact,
                "ordered_numbers_exact": exact_numbers,
                "ordered_operators_exact": exact_ops,
                "primary_numbers": an,
                "independent_numbers": bn,
                "primary_operators": ao,
                "independent_operators": bo,
                "primary_ordinal": ordinal_a,
                "independent_ordinal": ordinal_b,
            }
        )
    recall = overlap / na if na else 0
    precision = overlap / nb if nb else 0
    order = order_sum / order_weight if order_weight else 0
    return {
        "word_recall": recall,
        "word_precision": precision,
        "word_order": order,
        "word_policy_pass": recall >= policy["word_recall_min"]
        and precision >= policy["word_precision_min"]
        and order >= policy["word_order_min"],
        "ordered_numbers_exact": bool(numeric_ok),
        "ordered_operators_exact": bool(operator_ok),
        "semantic_labels_exact": bool(labels_ok),
        "cell_proofs": proofs,
    }


def derive_label_value_layout(
    page: dict,
    regions: list[dict],
    source_pdf: Path,
    *,
    cached_crops: dict | None = None,
    recognition_dpi: int = 180,
    baseline_independent: dict | None = None,
) -> dict:
    from .quality_gate import ocr_source_raster_evidence, load_policy, assess_page
    from .ocr import _rapidocr_reader
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from .ocr_consensus import recognition_receipt
    import pypdfium2 as pdfium
    import numpy as np
    import cv2

    source_pdf = Path(source_pdf)
    source_sha = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    base = {
        "kind": "source_bound_label_value_rows",
        "method": "source_raster_label_value_coverage",
        "ready": False,
        "page_complete": False,
        "reading_order_proven": False,
        "source_pdf_sha256": source_sha,
        "source_pdf_path": str(source_pdf.resolve()),
        "page": page["page"],
        "physical_page": page["page"],
        "page_text_sha256": page["text_sha256"],
        "recognition_dpi": recognition_dpi,
    }
    try:
        if recognition_dpi not in {120, 180, 240}:
            raise ValueError("recognition DPI must be 120, 180, or 240")
        if (
            page.get("method") not in {"vision", "tesseract"}
            or _sha(page["text"]) != page["text_sha256"]
        ):
            raise ValueError("source_ocr_page_identity_invalid")
        if not regions or any(
            r.get("source_pdf_sha256") != source_sha
            or r.get("physical_page") != page["page"]
            or r.get("page_text_sha256") != page["text_sha256"]
            for r in regions
        ):
            raise ValueError("layout_region_source_provenance_mismatch")
        geometry = label_value_geometry(page, regions)
        with pdfium.PdfDocument(str(source_pdf)) as document:
            physical = document[page["page"] - 1]
            try:
                bitmap = physical.render(scale=120 / 72)
                try:
                    raster = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
                stream = BytesIO()
                raster.save(stream, format="PNG")
                render_sha = hashlib.sha256(stream.getvalue()).hexdigest()
                if any(r["rendered_image_sha256"] != render_sha for r in regions):
                    raise ValueError("layout_source_render_changed")
                coverage = ocr_source_raster_evidence(
                    page, physical, source_sha, dpi=120
                )
                independent_bitmap = physical.render(scale=recognition_dpi / 72)
                try:
                    recognition_raster = independent_bitmap.to_pil().copy()
                finally:
                    independent_bitmap.close()
            finally:
                physical.close()
        if (
            coverage.get("page_complete") is not True
            or coverage.get("unaccounted_ink_pixels") != 0
            or coverage.get("unresolved_visual_regions") != []
            or coverage.get("latin_strike_crossings") != 0
        ):
            return {
                **base,
                "reason": "source_ink_or_decoration_remains_unaccounted",
                "source_coverage": coverage,
            }
        ink = (
            np.asarray(raster.convert("L"))
            < load_policy()["source_dark_pixel_threshold"]
        )
        left, right = geometry["gutter_normalized"]
        top = min(r["label"]["bbox_normalized"][1] for r in geometry["rows"])
        bottom = max(r["value"]["bbox_normalized"][3] for r in geometry["rows"])
        gutter = ink[
            int(top * raster.height) : int(bottom * raster.height),
            int(left * raster.width) : int(right * raster.width),
        ].astype("uint8")
        n, _, stats, _ = cv2.connectedComponentsWithStats(gutter, 8)
        if any(
            stats[i, cv2.CC_STAT_AREA] > load_policy()["source_speck_max_pixels"]
            for i in range(1, n)
        ):
            raise ValueError("source_ink_crosses_label_value_gutter")
        cells = []
        for row in geometry["rows"]:
            for kind in ("label", "value"):
                cells.append(
                    (
                        f"row-{row['row']}-{kind}",
                        row[kind]["bbox_normalized"],
                        row[kind]["line_indexes"],
                    )
                )
        for kind in ("header", "footer"):
            indexes = geometry[kind + "_line_indexes"]
            if indexes:
                cells.append(
                    (
                        kind,
                        _union([page["ocr"]["lines"][i]["bbox"] for i in indexes]),
                        indexes,
                    )
                )
        model = (
            "devanagari"
            if any("\u0900" <= c <= "\u097f" for c in page["text"])
            else "en"
        )
        raster = recognition_raster
        results = {}
        for cell_id, box, indexes in cells:
            x0 = max(0, math.floor(box[0] * raster.width) - 3)
            y0 = max(0, math.floor(box[1] * raster.height) - 3)
            x1 = min(raster.width, math.ceil(box[2] * raster.width) + 3)
            y1 = min(raster.height, math.ceil(box[3] * raster.height) + 3)
            image = raster.crop((x0, y0, x1, y1))
            stream = BytesIO()
            image.save(stream, format="PNG")
            crop_sha = hashlib.sha256(stream.getvalue()).hexdigest()
            source_lines = []
            line_images = []
            for index in indexes:
                linebox = page["ocr"]["lines"][index]["bbox"]
                bounds = [
                    max(0, math.floor(linebox[0] * raster.width) - 3),
                    max(0, math.floor(linebox[1] * raster.height) - 3),
                    min(raster.width, math.ceil(linebox[2] * raster.width) + 3),
                    min(raster.height, math.ceil(linebox[3] * raster.height) + 3),
                ]
                line_image = raster.crop(bounds)
                line_stream = BytesIO()
                line_image.save(line_stream, format="PNG")
                source_lines.append(
                    {
                        "original_line_index": index,
                        "crop_bbox_pixels": bounds,
                        "crop_sha256": hashlib.sha256(
                            line_stream.getvalue()
                        ).hexdigest(),
                    }
                )
                line_images.append(
                    np.asarray(line_image.convert("RGB"))[:, :, ::-1].copy()
                )
            if cached_crops is not None:
                result = cached_crops[cell_id]
                if (
                    result.get("crop_sha256") != crop_sha
                    or result.get("source_pdf_sha256") != source_sha
                    or result.get("page") != page["page"]
                    or result.get("source_line_crops") != source_lines
                ):
                    raise ValueError("independent_cell_crop_source_changed")
                results[cell_id] = deepcopy(result)
            else:
                import time

                started = time.monotonic()
                recognizer = _rapidocr_reader(model)._load_rec_model()
                recognized = recognizer(TextRecInput(img=line_images))
                text = "\n".join(recognized.txts or ())
                results[cell_id] = {
                    "text": text,
                    "text_sha256": _sha(text),
                    "crop_sha256": crop_sha,
                    "crop_bbox_pixels": [x0, y0, x1, y1],
                    "source_pdf_sha256": source_sha,
                    "page": page["page"],
                    "engine": "rapidocr_onnx",
                    "version": version("rapidocr"),
                    **recognition_receipt(model),
                    "source_line_crops": source_lines,
                    "segmentation_basis": "source_primary_ocr_line_geometry_with_complete_raster_coverage",
                    "session_providers": recognizer.session.session.get_providers(),
                    "scores": [float(s) for s in recognized.scores],
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
        metrics = row_consensus_metrics(geometry, results, page)
        proof = {
            **coverage,
            **base,
            "layout_source_render_sha256": render_sha,
            "geometry": geometry,
            "independent_cells": results,
            "layout_regions": deepcopy(regions),
            "row_metrics": metrics,
            "page_complete": True,
            "gutter_unaccounted_components": [],
        }
        baseline_text = None
        if baseline_independent is not None:
            from .repair_pipeline import _bound_independent_text

            baseline_text = _bound_independent_text(
                baseline_independent,
                source_pdf,
                source_sha=source_sha,
                page=page["page"],
                original_text_sha=page["text_sha256"],
                candidate_engine=page["method"],
            )
            proof["baseline_independent"] = deepcopy(baseline_independent)
        if not (
            (metrics["word_policy_pass"] or baseline_text is not None)
            and metrics["ordered_numbers_exact"]
            and metrics["ordered_operators_exact"]
            and metrics["semantic_labels_exact"]
        ):
            return {
                **proof,
                "reason": "row_local_word_label_number_or_operator_disagreement",
            }
        proof.update(
            ready=True,
            reading_order_proven=True,
            reason="one_to_one_noncrossing_rows_and_independent_cells_proven",
        )
        independent_text = "\n".join(results[key]["text"] for key in results)
        metadata = {
            "source_pdf_sha256": source_sha,
            "page": page["page"],
            "selected_page_text_sha256": page["text_sha256"],
            "text_sha256": _sha(independent_text),
            "engine": "rapidocr_onnx",
            "version": version("rapidocr"),
            **recognition_receipt(model),
        }
        if baseline_text is not None:
            independent_text = baseline_text
            metadata = {
                **baseline_independent["independent"],
                "source_pdf_sha256": source_sha,
                "page": page["page"],
                "selected_page_text_sha256": page["text_sha256"],
            }
        gate = assess_page(
            page,
            source_pdf_sha256=source_sha,
            package_pdf_sha256=source_sha,
            independent_text=independent_text,
            independent_metadata=metadata,
            source_geometry=proof,
            layout_proof=proof,
            table_count=1,
        )
        return {
            **proof,
            "ready": gate["outcome"] == "auto_accepted",
            "quality_gate": gate,
            "independent_text": independent_text,
            "independent_metadata": metadata,
        }
    except (ValueError, KeyError, TypeError) as error:
        return {**base, "reason": str(error)}


def validate_label_value_proof(page: dict, proof: dict) -> bool:
    try:
        if (
            proof.get("kind") != "source_bound_label_value_rows"
            or proof.get("page") != page["page"]
            or proof.get("page_text_sha256") != page["text_sha256"]
            or _sha(page["text"]) != page["text_sha256"]
            or proof.get("page_complete") is not True
            or proof.get("unaccounted_ink_pixels") != 0
            or proof.get("unresolved_visual_regions") != []
            or proof.get("gutter_unaccounted_components") != []
        ):
            return False
        if any(
            r.get("source_pdf_sha256") != proof["source_pdf_sha256"]
            or r.get("physical_page") != page["page"]
            or r.get("page_text_sha256") != page["text_sha256"]
            or r.get("rendered_image_sha256") != proof["layout_source_render_sha256"]
            for r in proof["layout_regions"]
        ):
            return False
        geometry = label_value_geometry(page, proof["layout_regions"])
        if geometry != proof["geometry"]:
            return False
        for result in proof["independent_cells"].values():
            if (
                result["text_sha256"] != _sha(result["text"])
                or result["source_pdf_sha256"] != proof["source_pdf_sha256"]
                or result["page"] != page["page"]
                or result["engine"] != "rapidocr_onnx"
                or not result["version"]
                or re.fullmatch("[0-9a-f]{64}", result["crop_sha256"]) is None
            ):
                return False
        metrics = row_consensus_metrics(geometry, proof["independent_cells"], page)
        baseline = proof.get("baseline_independent")
        baseline_valid = False
        if baseline:
            reader = baseline.get("independent") or {}
            baseline_valid = (
                baseline.get("source_pdf_sha256") == proof["source_pdf_sha256"]
                and baseline.get("page") == page["page"]
                and baseline.get("selected_page_text_sha256") == page["text_sha256"]
                and reader.get("text_sha256") == _sha(reader.get("text", ""))
                and reader.get("engine") == "rapidocr_onnx"
                and bool(reader.get("version"))
                and re.fullmatch(
                    "[0-9a-f]{64}", reader.get("recognition_model_sha256", "")
                )
                is not None
            )
            if not baseline_valid:
                return False
        return (
            metrics == proof["row_metrics"]
            and (metrics["word_policy_pass"] or baseline_valid)
            and all(
                metrics[key]
                for key in (
                    "ordered_numbers_exact",
                    "ordered_operators_exact",
                    "semantic_labels_exact",
                )
            )
        )
    except (ValueError, KeyError, TypeError):
        return False


def apply_label_value_layout(package: Path, receipt: dict) -> dict:
    """Atomically attach freshly proved rows while preserving all page text."""
    import shutil
    import uuid
    from .evidence_package import verify_package, create_package
    from .structure_candidates import _evidence

    package = Path(package)
    source = Path(receipt["source_pdf_path"])
    verify_package(package, source_pdf=source)
    pages = [
        json.loads(line) for line in (package / "pages.jsonl").read_text().splitlines()
    ]
    page = next((row for row in pages if row["page"] == receipt["page"]), None)
    if (
        page is None
        or page.get("review_decision") is not None
        or page["text_sha256"] != receipt["page_text_sha256"]
        or not receipt.get("ready")
    ):
        raise ValueError("label/value source page or review state changed")
    tables = json.loads((package / "tables.json").read_text())
    if any(table["page"] == page["page"] for table in tables):
        raise ValueError("existing page table needs an explicit layout adapter")
    fresh = derive_label_value_layout(
        page,
        receipt["layout_regions"],
        source,
        cached_crops=receipt["independent_cells"],
        recognition_dpi=receipt["recognition_dpi"],
        baseline_independent=receipt.get("baseline_independent"),
    )
    if (
        not fresh.get("ready")
        or fresh.get("quality_gate", {}).get("outcome") != "auto_accepted"
    ):
        raise ValueError("label/value fresh source or quality gates failed")
    temporary = package.parent / f".{package.name}.rows-{uuid.uuid4().hex}"
    backup = package.parent / f".{package.name}.backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(package, temporary)
        stored = {
            key: value
            for key, value in fresh.items()
            if key not in {"quality_gate", "independent_text", "independent_metadata"}
        }
        revised = {**page, "label_value_proof": stored}
        revised["status"] = "review_required"
        pages = [revised if row["page"] == page["page"] else row for row in pages]
        (temporary / "pages.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pages)
        )
        width, height = receipt["layout_regions"][0]["page_size_points"]
        rows = fresh["geometry"]["rows"]
        all_boxes = [
            row[key]["bbox_normalized"] for row in rows for key in ("label", "value")
        ]
        bbox = _union(all_boxes)

        def points(box):
            return [
                round(value * (width if i % 2 == 0 else height), 3)
                for i, value in enumerate(box)
            ]

        table_id = f"{page['id']}:table-label-value"
        table = {
            "id": table_id,
            "page": page["page"],
            "bbox": points(bbox),
            "bbox_coordinates": "PDF points, top-left",
            "cells": [[row["label"]["text"], row["value"]["text"]] for row in rows],
            "status": "needs_row_column_review",
            "layout_verified": True,
            "extraction_basis": "source_bound_dual_reader_label_value_rows",
            "source_pdf_sha256": fresh["source_pdf_sha256"],
            "page_text_sha256": page["text_sha256"],
            "source_render_sha256": fresh["rendered_image_sha256"],
            "cell_spans": [],
        }
        structures = [
            json.loads(line)
            for line in (package / "structure-candidates.jsonl")
            .read_text()
            .splitlines()
            if line
        ]
        for row in rows:
            row_spans = []
            for column, kind in enumerate(("label", "value")):
                cell = row[kind]
                spans = [
                    _evidence(
                        page,
                        span["char_start"],
                        span["char_start"] + span["char_length"],
                    )
                    for span in cell["source_spans"]
                ]
                row_spans.extend(spans)
                table["cell_spans"].append(
                    {
                        "row": row["row"],
                        "column": column,
                        "row_span": 1,
                        "column_span": 1,
                        "bbox": points(cell["bbox_normalized"]),
                        "row_header": column == 0,
                        "column_header": False,
                        "text": cell["text"],
                    }
                )
                structures.append(
                    {
                        "kind": "table_cell",
                        "table_id": table_id,
                        "table_bbox": table["bbox"],
                        "row": row["row"],
                        "column": column,
                        "proposed_header": False,
                        "candidate_header_row": None,
                        "value_text": cell["text"],
                        "evidence": spans,
                        "span_status": "exact_source_line_spans",
                        "layout_verified": True,
                        "review_reasons": ["legal_content_remains_unverified"],
                        "status": "review_required",
                    }
                )
            structures.append(
                {
                    "kind": "label_value_relation",
                    "table_id": table_id,
                    "row": row["row"],
                    "label_column": 0,
                    "value_column": 1,
                    "evidence": row_spans,
                    "layout_verified": True,
                    "status": "review_required",
                }
            )
        tables.append(table)
        (temporary / "tables.json").write_text(
            json.dumps(tables, ensure_ascii=False, indent=2) + "\n"
        )
        (temporary / "structure-candidates.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in structures)
        )
        report = json.loads((package / "report.json").read_text())
        report.update(
            tables=len(tables),
            structure_candidates=len(structures),
            requires_source_review=True,
        )
        report["page_status_counts"] = {
            status: sum(row["status"] == status for row in pages)
            for status in ("ready", "blank", "review_required")
        }
        report.setdefault("issues", []).append(
            {
                "page": page["page"],
                "kind": "source_bound_label_value_rows",
                "rows": len(rows),
                "detail": "One-to-one noncrossing rows and independently read source cells; legal content stays candidate",
            }
        )
        (temporary / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        create_package(temporary)
        verify_package(temporary, source_pdf=source)
        for name in ("embedding-input.jsonl", "fact-candidates.jsonl"):
            if (temporary / name).read_bytes() != (package / name).read_bytes():
                raise ValueError(f"row layout changed immutable {name}")
        if any(
            old["text"] != new["text"] or old["text_sha256"] != new["text_sha256"]
            for old, new in zip(
                [
                    json.loads(x)
                    for x in (package / "pages.jsonl").read_text().splitlines()
                ],
                pages,
            )
        ):
            raise ValueError("row layout changed original page text")
        package.replace(backup)
        try:
            temporary.replace(package)
        except Exception:
            backup.replace(package)
            raise
        shutil.rmtree(backup)
        return {
            "source_pdf_sha256": fresh["source_pdf_sha256"],
            "page": page["page"],
            "rows_added": len(rows),
            "cells_added": len(rows) * 2,
            "page_text_sha256": page["text_sha256"],
            "quality_gate": fresh["quality_gate"],
            "package": str(package),
            "human_review_receipts_created": 0,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
