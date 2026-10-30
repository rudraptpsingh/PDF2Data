"""Source-bound, quotation-only text regions on pages that remain held.

This does not publish, mutate a page, classify unknown images, or establish a
clause's legal completeness. Cached independent OCR is reused, never rerun.
"""

from __future__ import annotations

from hashlib import sha256
from copy import deepcopy
from io import BytesIO
from importlib.resources import files
import json
import math
from pathlib import Path
import re

from .quality_gate import WORD, _normalized, load_policy, ocr_source_raster_evidence
from .column_order import _tokens


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _json_hash(value) -> str:
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _png(image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


_HEADING = re.compile(r"^\d+(?:\.\d+)+\.?\s*(?:\D|$)")
_EXCEPTION = re.compile(
    r"^(?:provided\b|except\b|unless\b|however\b|subject to\b|notwithstanding\b)", re.I
)


def region_candidates(page: dict) -> list[dict]:
    """Nominate complete numbered clauses, otherwise sentence-ended line groups.

    These are selections to check, never approved clauses or inferred roles.
    An unfinished trailing group is retained so its hold is visible.
    """
    lines = (page.get("ocr") or {}).get("lines") or []
    headings = [i for i, line in enumerate(lines) if _HEADING.match(line["text"])]
    if headings:
        return [
            {"kind": "numbered_clause", "line_indexes": list(range(start, end))}
            for start, end in zip(headings, [*headings[1:], len(lines)])
        ]
    result, start = [], 0
    for i, line in enumerate(lines):
        if re.search(r"[.!?][\"')]*$", line["text"].strip()):
            result.append(
                {"kind": "text_region", "line_indexes": list(range(start, i + 1))}
            )
            start = i + 1
    if start < len(lines):
        result.append(
            {"kind": "text_region", "line_indexes": list(range(start, len(lines)))}
        )
    return result


def _selection(page, indexes, kind):
    lines = (page.get("ocr") or {}).get("lines") or []
    if (
        not indexes
        or any(type(i) is not int for i in indexes)
        or indexes != list(range(indexes[0], indexes[-1] + 1))
        or indexes[0] < 0
        or indexes[-1] >= len(lines)
    ):
        raise ValueError("region_requires_contiguous_complete_source_lines")
    if "\n".join(line["text"] for line in lines) != page["text"]:
        raise ValueError("page_text_does_not_preserve_original_lines")
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line["text"]) + 1
    start = offsets[indexes[0]]
    end = offsets[indexes[-1]] + len(lines[indexes[-1]]["text"])
    quote = page["text"][start:end]
    if kind not in {"numbered_clause", "text_region"}:
        raise ValueError("unsupported_region_kind")
    if {"kind": kind, "line_indexes": indexes} not in region_candidates(page):
        raise ValueError("selection_is_not_a_complete_candidate_context")
    if not re.search(r"[.!?][\"')]*$", quote.strip()):
        raise ValueError("unfinished_context_or_page_continuation")
    if indexes[-1] + 1 < len(lines) and _EXCEPTION.match(
        lines[indexes[-1] + 1]["text"].strip()
    ):
        raise ValueError("excluded_adjacent_exception_or_condition")
    return lines, start, end, quote


def _independent_span(quote, independent):
    # Original codepoint offsets are kept even if Unicode normalization changes
    # a token. A normalization producing multiple tokens is not guessed.
    matches = list(WORD.finditer(independent))
    tokens = [_normalized(m.group()) for m in matches]
    target = [_normalized(m.group()) for m in WORD.finditer(quote)]
    starts = [
        i
        for i in range(len(tokens) - len(target) + 1)
        if tokens[i : i + len(target)] == target
    ]
    if len(starts) != 1 or not target:
        raise ValueError("independent_region_tokens_not_exact_and_unique")
    i = starts[0]
    start, end = matches[i].start(), matches[i + len(target) - 1].end()
    while (
        end < len(independent)
        and not independent[end].isspace()
        and not independent[end].isalnum()
    ):
        end += 1
    # Never trim an independent negation, condition, or extra OCR token from a
    # source line merely to manufacture an exact matching substring.
    if independent[independent.rfind("\n", 0, start) + 1 : start].strip():
        raise ValueError("independent_region_starts_inside_extra_line_text")
    line_end = independent.find("\n", end)
    if independent[end : line_end if line_end >= 0 else len(independent)].strip():
        raise ValueError("independent_region_ends_before_extra_line_text")
    selected = independent[start:end]
    if _tokens(quote) != _tokens(selected):
        raise ValueError("independent_region_number_or_operator_mismatch")
    return start, end, selected


def derive_text_region(
    page: dict,
    source_pdf: Path,
    independent_receipt: dict,
    selection: dict,
    *,
    table_count: int = 0,
    dpi: int = 120,
) -> dict:
    """Fresh source raster + exact cached two-reader region proof.

    Only a ready receipt is evidence for quotation, with outside visuals still
    unknown. It is never an input to the full-page acceptance gate.
    """
    import numpy as np
    import pypdfium2 as pdfium
    from .repair_pipeline import _bound_independent_text

    source_pdf = Path(source_pdf)
    source_sha = sha256(source_pdf.read_bytes()).hexdigest()
    policy = load_policy()
    result = {
        "schema_version": 1,
        "kind": "source_verified_text_region",
        "ready": False,
        "acceptance_scope": "none",
        "page_complete": False,
        "source_pdf_sha256": source_sha,
        "page": page["page"],
        "raw_page_text_sha256": page["text_sha256"],
        "selection": selection,
        "policy_sha256": sha256(
            files("local_vector_batch").joinpath("quality_policy.json").read_bytes()
        ).hexdigest(),
        "page_status_preserved": page.get("status"),
        "primary_page_snapshot": deepcopy(page),
        "independent_receipt_sha256": _json_hash(independent_receipt),
    }
    try:
        if page.get("status") != "review_required":
            raise ValueError("region_pilot_requires_held_source_page")
        if not 72 <= dpi <= 240:
            raise ValueError("region_render_dpi_out_of_bounds")
        if _hash(page["text"]) != page["text_sha256"]:
            raise ValueError("raw_page_text_sha_mismatch")
        if table_count or independent_receipt.get("table_on_page"):
            raise ValueError("table_cells_and_roles_require_separate_proof")
        lines, start, end, quote = _selection(
            page, selection["line_indexes"], selection["kind"]
        )
        result.update(
            char_start=start,
            char_length=end - start,
            offset_encoding="codepoint",
            offset_scope="raw_page",
            exact_quote=quote,
            region_text_sha256=_hash(quote),
        )
        independent = _bound_independent_text(
            independent_receipt,
            source_pdf,
            source_sha=source_sha,
            page=page["page"],
            original_text_sha=page["text_sha256"],
            candidate_engine=(page.get("ocr") or {}).get("engine", page.get("method")),
        )
        a, b, independent_quote = _independent_span(quote, independent)
        reader = independent_receipt.get("independent") or independent_receipt
        script = (
            "devanagari" if any("\u0900" <= c <= "\u097f" for c in quote) else "latin"
        )
        languages = reader.get("recognition_languages") or []
        if not (
            script == "latin"
            and "en" in languages
            or script == "devanagari"
            and any(x.startswith(("hi", "hin")) for x in languages)
        ):
            raise ValueError("matching_independent_language_model_unproven")
        result["independent"] = {
            "char_start": a,
            "char_length": b - a,
            "exact_quote": independent_quote,
            "region_text_sha256": _hash(independent_quote),
            "engine": reader["engine"],
            "model": reader["recognition_model"],
            "model_sha256": reader["recognition_model_sha256"],
            "ordered_words_exact": True,
            "ordered_numbers_exact": True,
            "ordered_operators_exact": True,
            "recognition_languages": languages,
        }
        pdf = pdfium.PdfDocument(str(source_pdf))
        try:
            source_page = pdf[page["page"] - 1]
            try:
                geometry = ocr_source_raster_evidence(
                    page, source_page, source_sha, dpi=dpi, policy=policy
                )
                image = source_page.render(scale=dpi / 72).to_pil().convert("L")
                source_size, source_box = list(source_page.get_size()), list(
                    source_page.get_bbox()
                )
                independent_image = source_page.render(
                    scale=independent_receipt["render_dpi"] / 72
                ).to_pil()
                result["independent_source_render_sha256"] = sha256(
                    _png(independent_image)
                ).hexdigest()
            finally:
                source_page.close()
        finally:
            pdf.close()
        selected = selection["line_indexes"]
        boxes = []
        for line in lines:
            box = line.get("bbox")
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                    for v in box
                )
                or not box[0] < box[2]
                or not box[1] < box[3]
            ):
                raise ValueError("invalid_original_line_geometry")
            pad = policy["ocr_box_padding_px"]
            boxes.append(
                [
                    max(0, int(box[0] * image.width) - pad),
                    max(0, int(box[1] * image.height) - pad),
                    min(image.width, int(box[2] * image.width) + pad + 1),
                    min(image.height, int(box[3] * image.height) + pad + 1),
                ]
            )
        bounds = [
            min(boxes[i][0] for i in selected),
            min(boxes[i][1] for i in selected),
            max(boxes[i][2] for i in selected),
            max(boxes[i][3] for i in selected),
        ]
        for i, box in enumerate(boxes):
            if (
                i not in selected
                and min(box[2], bounds[2]) > max(box[0], bounds[0])
                and min(box[3], bounds[3]) > max(box[1], bounds[1])
            ):
                raise ValueError("context_intersects_excluded_source_line")
        ink = np.asarray(image) < policy["source_dark_pixel_threshold"]
        mask = np.zeros(ink.shape, dtype=bool)
        for i in selected:
            x0, y0, x1, y1 = boxes[i]
            mask[y0:y1, x0:x1] = True
        x0, y0, x1, y1 = bounds
        unaccounted = int((ink & ~mask)[y0:y1, x0:x1].sum())
        result.update(
            render_dpi=dpi,
            render_size=list(image.size),
            rendered_image_sha256=sha256(_png(image)).hexdigest(),
            source_pdfium_bbox=source_box,
            source_size_points=source_size,
            bbox_pixels=bounds,
            bbox_normalized=[
                x0 / image.width,
                y0 / image.height,
                x1 / image.width,
                y1 / image.height,
            ],
            bbox_points=[
                x0 / image.width * source_size[0],
                y0 / image.height * source_size[1],
                x1 / image.width * source_size[0],
                y1 / image.height * source_size[1],
            ],
            bbox_coordinate_system="pdf_points_top_left_rendered_cropbox",
            crop_sha256=sha256(_png(image.crop(bounds))).hexdigest(),
            source_lines_sha256=_json_hash(lines),
            context_unaccounted_ink_pixels=unaccounted,
            outside_unverified_source_lines=[
                {
                    "line_index": i,
                    "bbox_pixels": box,
                    "status": "held",
                    "role": "unknown",
                    "unverified_text": lines[i]["text"],
                    "crop_sha256": sha256(_png(image.crop(box))).hexdigest(),
                }
                for i, box in enumerate(boxes)
                if i not in selected
            ],
            outside_unresolved_visual_regions=[
                {
                    **r,
                    "role": "unknown",
                    "status": "held",
                    "crop_sha256": sha256(
                        _png(image.crop(r["bbox_pixels"]))
                    ).hexdigest(),
                }
                for r in geometry.get("unresolved_visual_regions", [])
            ],
            source_page_visual_complete=geometry.get("page_complete", False),
            source_page_reading_order_proven=geometry.get(
                "reading_order_proven", False
            ),
        )
        if unaccounted:
            raise ValueError("unexplained_source_ink_inside_context")
        # Reuse existing strike/column/order checks on the selected source lines,
        # preserving their original full-page coordinate system.
        region_page = {
            **page,
            "text": quote,
            "text_sha256": _hash(quote),
            "ocr": {**page["ocr"], "lines": [lines[i] for i in selected]},
        }
        pdf = pdfium.PdfDocument(str(source_pdf))
        try:
            p = pdf[page["page"] - 1]
            try:
                local = ocr_source_raster_evidence(
                    region_page, p, source_sha, dpi=dpi, policy=policy
                )
            finally:
                p.close()
        finally:
            pdf.close()
        if not local.get("reading_order_proven"):
            raise ValueError("region_columns_strikes_or_line_order_unproven")
        result.update(
            ready=True,
            acceptance_scope="verified_transcription_only",
            limitations=[
                "whole_page_held",
                "outside_visual_roles_unknown",
                "legal_status_and_clause_completeness_unverified",
            ],
            reason="exact_two_reader_tokens_and_source_zero_inside_context",
        )
    except (ValueError, KeyError, IndexError) as error:
        result["reason"] = str(error)
    result["receipt_sha256"] = _json_hash(result)
    return result


def validate_text_region(
    page: dict,
    source_pdf: Path,
    independent_receipt: dict,
    receipt: dict,
    *,
    table_count: int = 0,
) -> bool:
    """Recheck immutable source proof; parent OCR SHA is original lineage only.

    A later unrelated page OCR edit does not revoke a physical-source quotation.
    The original snapshot is checked against its own hash and re-rendered PDF;
    neither a new transcription nor a changed quote is substituted into it.
    """
    if receipt.get("ready") is not True or page.get("page") != receipt.get("page"):
        return False
    original = receipt.get("primary_page_snapshot") or {}
    try:
        fresh = derive_text_region(
            original,
            source_pdf,
            independent_receipt,
            receipt.get("selection", {}),
            table_count=table_count,
            dpi=receipt.get("render_dpi", 120),
        )
        return fresh.get("ready") is True and fresh == receipt
    except (ValueError, KeyError, TypeError, IndexError):
        return False


def write_region_images(receipt: dict, source_pdf: Path, output: Path) -> dict:
    """Export unchanged source crops for a ready receipt, including unknown ink."""
    import pypdfium2 as pdfium

    if (
        not receipt.get("ready")
        or sha256(Path(source_pdf).read_bytes()).hexdigest()
        != receipt["source_pdf_sha256"]
    ):
        raise ValueError("region_export_requires_ready_receipt_and_exact_source")
    pdf = pdfium.PdfDocument(str(source_pdf))
    try:
        p = pdf[receipt["page"] - 1]
        try:
            image = p.render(scale=receipt["render_dpi"] / 72).to_pil().convert("L")
        finally:
            p.close()
    finally:
        pdf.close()
    if sha256(_png(image)).hexdigest() != receipt["rendered_image_sha256"]:
        raise ValueError("region_export_render_sha_mismatch")
    crops = [
        (
            "source-page",
            [0, 0, image.width, image.height],
            receipt["rendered_image_sha256"],
        ),
        ("context", receipt["bbox_pixels"], receipt["crop_sha256"]),
    ]
    crops.extend(
        (f"outside-unknown-{i}", r["bbox_pixels"], r["crop_sha256"])
        for i, r in enumerate(receipt["outside_unresolved_visual_regions"])
    )
    crops.extend(
        (f"outside-line-{r['line_index']}", r["bbox_pixels"], r["crop_sha256"])
        for r in receipt["outside_unverified_source_lines"]
    )
    encoded = [(name, _png(image.crop(box)), digest) for name, box, digest in crops]
    if any(sha256(data).hexdigest() != digest for _, data, digest in encoded):
        raise ValueError("region_export_crop_sha_mismatch")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, data, _ in encoded:
        (output / f"{name}.png").write_bytes(data)
    return {name: str(output / f"{name}.png") for name, _, _ in encoded}
