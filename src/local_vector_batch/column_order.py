"""Conservative source-bound repair of plain, separated OCR text columns.

This never approves tables, drops OCR lines, or changes a line's text. Model
regions nominate blocks; non-overlap, gutters, two OCR streams and original
source ink must independently support the proposed order.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path


class ColumnOrderHold(ValueError):
    def __init__(self, reason: str, **evidence):
        super().__init__(reason)
        self.evidence = evidence


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _box(value):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(
            type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1
            for v in value
        )
        or not value[0] < value[2]
        or not value[1] < value[3]
    ):
        raise ValueError("invalid_column_geometry_box")
    return value


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def _tokens(text):
    from .quality_gate import WORD, NUMBER, _normalized, _operators, load_policy

    normalized = _normalized(text)
    return (
        WORD.findall(normalized),
        NUMBER.findall(normalized),
        _operators(text, load_policy()),
    )


def _line_text(lines):
    return "\n".join(str(line.get("text", "")) for line in lines)


def column_geometry_consensus(
    page: dict, independent_lines: list[dict], regions: list[dict]
) -> dict:
    """Derive a geometric DAG and exact per-block two-reader token consensus."""
    from .pdf_compare import TEXT_REGION_KINDS

    primary = (page.get("ocr") or {}).get("lines") or []
    if not primary or not independent_lines:
        raise ValueError("column_ocr_line_boxes_missing")
    if _tokens(page["text"]) != _tokens(_line_text(primary)):
        raise ValueError("selected_text_does_not_preserve_original_ocr_lines")
    all_words, all_numbers, _ = _tokens(page["text"])
    if len(all_numbers) >= 4 and len(all_numbers) / max(1, len(all_words)) > 0.35:
        raise ValueError("number_dense_grid_requires_table_proof")
    if any(region.get("kind") not in TEXT_REGION_KINDS for region in regions):
        raise ValueError("table_or_visual_region_requires_separate_proof")
    body = [r for r in regions if r["kind"] not in {"page_header", "page_footer"}]
    if len(body) < 4:
        raise ValueError("insufficient_independent_body_blocks")
    boxes = {r["id"]: _box(r["bbox_normalized"]) for r in body}
    if len(boxes) != len(body):
        raise ValueError("duplicate_layout_region_identity")
    body_top = min(box[1] for box in boxes.values())
    body_bottom = max(box[3] for box in boxes.values())
    left_edge = min(box[0] for box in boxes.values())
    # A left-margin section heading may start a new horizontal band only when
    # no other source block crosses that cut. This keeps separate address
    # sections together rather than reading the entire left page column first.
    anchors = [
        r
        for r in body
        if r["kind"] == "section_header"
        and boxes[r["id"]][0] <= left_edge + 0.03
        and not any(
            other[1] < boxes[r["id"]][1] < other[3]
            for ref, other in boxes.items()
            if ref != r["id"]
        )
    ]
    cuts = sorted({body_top, *[boxes[r["id"]][1] for r in anchors], body_bottom})
    heading_ids = {r["id"] for r in anchors}
    bands, gutters, gutter_regions = [], [], []
    ordered_body = []
    has_columns = False
    for band_top, band_bottom in zip(cuts, cuts[1:]):
        members = [
            ref for ref, box in boxes.items() if band_top <= box[1] < band_bottom
        ]
        headings = sorted(
            (ref for ref in members if ref in heading_ids),
            key=lambda ref: boxes[ref][1],
        )
        pending = set(members) - set(headings)
        columns = []
        while pending:
            group = {pending.pop()}
            changed = True
            while changed:
                changed = False
                for candidate in sorted(pending):
                    if any(
                        min(boxes[candidate][2], boxes[member][2])
                        > max(boxes[candidate][0], boxes[member][0])
                        for member in group
                    ):
                        pending.remove(candidate)
                        group.add(candidate)
                        changed = True
            columns.append(
                sorted(group, key=lambda ref: (boxes[ref][1], boxes[ref][0]))
            )
        columns.sort(key=lambda refs: min(boxes[ref][0] for ref in refs))
        if len(columns) > 3:
            raise ValueError("separated_text_columns_unproven")
        has_columns |= len(columns) > 1
        for left, right in zip(columns, columns[1:]):
            aligned_pairs = [
                [a, b]
                for a in left
                for b in right
                if abs(boxes[a][1] - boxes[b][1]) <= 0.008
                and min(boxes[a][3], boxes[b][3]) - max(boxes[a][1], boxes[b][1])
                > 0.5 * min(boxes[a][3] - boxes[a][1], boxes[b][3] - boxes[b][1])
            ]
            if len(aligned_pairs) >= 2:
                raise ColumnOrderHold(
                    "row_aligned_label_value_layout_requires_table_proof",
                    paired_region_ids=aligned_pairs,
                )
            start = max(boxes[ref][2] for ref in left)
            end = min(boxes[ref][0] for ref in right)
            if end - start < 0.02:
                raise ValueError("column_gutter_too_narrow_or_overlapping")
            top = min(boxes[ref][1] for ref in left + right)
            bottom = max(boxes[ref][3] for ref in left + right)
            gutters.append([start, end])
            gutter_regions.append([start, top, end, bottom])
        for column in columns:
            for a, b in zip(column, column[1:]):
                if boxes[a][3] > boxes[b][1]:
                    raise ValueError("overlapping_body_regions")
        band_order = [*headings, *[ref for column in columns for ref in column]]
        ordered_body.extend(band_order)
        bands.append(
            {
                "columns": columns,
                "heading_region_ids": headings,
                "vertical_extent": [band_top, band_bottom],
                "region_order": band_order,
            }
        )
    if not has_columns or sorted(ordered_body) != sorted(boxes):
        raise ValueError("separated_text_columns_unproven")
    boxes = {
        "__header__": [0, 0, 1, body_top],
        **boxes,
        "__footer__": [0, body_bottom, 1, 1],
    }
    order = ["__header__", *ordered_body, "__footer__"]

    def assign(lines):
        groups = {ref: [] for ref in order}
        for index, line in enumerate(lines):
            box = _box(line.get("bbox"))
            area = (box[2] - box[0]) * (box[3] - box[1])
            hits = [
                (ref, _overlap(box, region) / area)
                for ref, region in boxes.items()
                if ref not in {"__header__", "__footer__"}
            ]
            substantial = [(ref, overlap) for ref, overlap in hits if overlap > 0.2]
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            chosen = (
                substantial[0][0]
                if len(substantial) == 1 and substantial[0][1] >= 0.5
                else None
            )
            if chosen and not (
                boxes[chosen][0] <= cx <= boxes[chosen][2]
                and boxes[chosen][1] <= cy <= boxes[chosen][3]
            ):
                chosen = None
            if chosen is None and not substantial and box[3] <= body_top:
                chosen = "__header__"
            if chosen is None and not substantial and box[1] >= body_bottom:
                chosen = "__footer__"
            if chosen is None:
                raise ColumnOrderHold(
                    "unmapped_or_multiregion_ocr_line",
                    line_index=index,
                    line_text_sha256=_sha(str(line.get("text", ""))),
                    bbox=box,
                    region_overlap_fractions=[
                        [ref, round(overlap, 6)] for ref, overlap in hits if overlap
                    ],
                )
            groups[chosen].append(index)
        for ref, indexes in groups.items():
            indexes.sort(key=lambda i: (lines[i]["bbox"][1], lines[i]["bbox"][0]))
            for position, a in enumerate(indexes):
                for b in indexes[position + 1 :]:
                    aa, bb = lines[a]["bbox"], lines[b]["bbox"]
                    smaller = min(
                        (aa[2] - aa[0]) * (aa[3] - aa[1]),
                        (bb[2] - bb[0]) * (bb[3] - bb[1]),
                    )
                    if _overlap(aa, bb) > 0.05 * smaller:
                        raise ValueError("overlapping_ocr_lines")
        flattened = [i for ref in order for i in groups[ref]]
        if sorted(flattened) != list(range(len(lines))):
            raise ValueError("ocr_line_identity_not_preserved_exactly_once")
        return groups, flattened

    primary_groups, primary_order = assign(primary)
    independent_groups, independent_order = assign(independent_lines)
    # The model's tight box is only a proposal. Both OCR streams must expand
    # it to a single, nonoverlapping source block; the resulting gutter is then
    # checked against original raster ink below, not accepted from model boxes.
    expanded = {}
    for ref in ordered_body:
        source_boxes = [
            boxes[ref],
            *[primary[i]["bbox"] for i in primary_groups[ref]],
            *[independent_lines[i]["bbox"] for i in independent_groups[ref]],
        ]
        expanded[ref] = [
            min(b[0] for b in source_boxes),
            min(b[1] for b in source_boxes),
            max(b[2] for b in source_boxes),
            max(b[3] for b in source_boxes),
        ]
    for position, ref in enumerate(ordered_body):
        if any(
            _overlap(expanded[ref], expanded[other]) > 0
            for other in ordered_body[position + 1 :]
        ):
            raise ValueError("expanded_source_ocr_blocks_overlap")
    gutters, gutter_regions = [], []
    for band in bands:
        for column in band["columns"]:
            if len(band["columns"]) > 1 and any(
                sum(len(groups[ref]) for ref in column) < 2
                for groups in (primary_groups, independent_groups)
            ):
                raise ValueError("independently_populated_columns_unproven")
        for left, right in zip(band["columns"], band["columns"][1:]):
            start = max(expanded[ref][2] for ref in left)
            end = min(expanded[ref][0] for ref in right)
            if end - start < 0.02:
                raise ValueError("expanded_source_gutter_unproven")
            top = min(expanded[ref][1] for ref in left + right)
            bottom = max(expanded[ref][3] for ref in left + right)
            gutters.append([start, end])
            gutter_regions.append([start, top, end, bottom])
    block_proofs = []
    for ref in order:
        a = _line_text([primary[i] for i in primary_groups[ref]])
        b = _line_text([independent_lines[i] for i in independent_groups[ref]])
        if ref not in {"__header__", "__footer__"} and not (a.strip() and b.strip()):
            raise ValueError("body_region_not_read_by_both_engines")
        aw, an, ao = _tokens(a)
        bw, bn, bo = _tokens(b)
        if aw != bw or an != bn or ao != bo:
            raise ValueError("block_local_words_numbers_or_operators_disagree")
        if len(an) >= 8 and len(an) / max(1, len(aw)) > 0.5:
            raise ValueError("number_dense_grid_requires_table_proof")
        block_proofs.append(
            {
                "region_id": ref,
                "primary_line_indexes": primary_groups[ref],
                "independent_line_indexes": independent_groups[ref],
                "ordered_words": aw,
                "ordered_numbers": an,
                "ordered_operators": ao,
            }
        )
    return {
        "bands": bands,
        "gutters_normalized": gutters,
        "gutter_regions_normalized": gutter_regions,
        "expanded_source_blocks": expanded,
        "body_vertical_extent": [body_top, body_bottom],
        "region_order": order,
        "dag_edges": [[a, b] for a, b in zip(order, order[1:])],
        "primary_line_order": primary_order,
        "independent_line_order": independent_order,
        "block_proofs": block_proofs,
        "proposed_text": _line_text([primary[i] for i in primary_order]),
        "independent_text": _line_text(
            [independent_lines[i] for i in independent_order]
        ),
    }


def derive_column_order_repair(
    page: dict,
    independent_lines: list[dict],
    regions: list[dict],
    source_pdf: Path,
    *,
    independent_metadata: dict,
    table_count: int = 0,
    dpi: int = 120,
) -> dict:
    """Re-render original bytes; return a complete repair or an explicit hold."""
    from .quality_gate import ocr_source_raster_evidence, load_policy, assess_page
    from .pdf_ingest import text_quality
    import cv2
    import numpy as np
    import pypdfium2 as pdfium

    source_pdf = Path(source_pdf)
    source_sha = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    base = {
        "method": "source_raster_column_order_coverage",
        "ready": False,
        "page_complete": False,
        "reading_order_proven": False,
        "source_pdf_sha256": source_sha,
        "page": page["page"],
        "source_pdf_path": str(source_pdf.resolve()),
        "original_page_text_sha256": page["text_sha256"],
        "page_text_sha256": page["text_sha256"],
    }
    try:
        if table_count or page.get("method") == "pdf_text":
            raise ValueError("column_repair_requires_nontable_ocr_page")
        if page["text_sha256"] != _sha(page["text"]):
            raise ValueError("original_page_text_hash_mismatch")
        if not regions or any(
            r.get("source_pdf_sha256") != source_sha
            or r.get("physical_page") != page["page"]
            or r.get("page_text_sha256") != page["text_sha256"]
            for r in regions
        ):
            raise ValueError("layout_region_source_provenance_mismatch")
        # Cheap primary-geometry preflight only: reject tables, overlaps and
        # missing blocks before requesting independent OCR work. Passing this
        # is never a consensus proof; the separate reader is required below.
        column_geometry_consensus(
            page, (page.get("ocr") or {}).get("lines") or [], regions
        )
        if (
            independent_metadata.get("source_pdf_sha256") != source_sha
            or independent_metadata.get("page") != page["page"]
            or independent_metadata.get("text_sha256")
            != _sha(_line_text(independent_lines))
            or not independent_metadata.get("engine")
            or not independent_metadata.get("version")
        ):
            raise ValueError("independent_ocr_line_provenance_mismatch")
        geometric = column_geometry_consensus(page, independent_lines, regions)
        proposed = deepcopy(page)
        proposed["text"] = geometric["proposed_text"]
        proposed["text_sha256"] = _sha(proposed["text"])
        proposed["chars"] = len(proposed["text"])
        proposed["selected_quality"] = text_quality(proposed["text"])
        proposed["ocr"]["lines"] = [
            deepcopy(page["ocr"]["lines"][i]) for i in geometric["primary_line_order"]
        ]
        with pdfium.PdfDocument(str(source_pdf)) as document:
            physical = document[page["page"] - 1]
            try:
                bitmap = physical.render(scale=dpi / 72)
                try:
                    raster = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
                stream = BytesIO()
                raster.save(stream, format="PNG")
                render_sha = hashlib.sha256(stream.getvalue()).hexdigest()
                if any(r["rendered_image_sha256"] != render_sha for r in regions):
                    raise ValueError("layout_source_render_changed")
                gray = (
                    np.asarray(raster.convert("L"))
                    < load_policy()["source_dark_pixel_threshold"]
                )
                gutter_regions = []
                for left, top, right, bottom in geometric["gutter_regions_normalized"]:
                    crop = gray[
                        int(top * raster.height) : int(bottom * raster.height),
                        int(left * raster.width) : int(right * raster.width),
                    ].astype("uint8")
                    nlabels, _, stats, _ = cv2.connectedComponentsWithStats(crop, 8)
                    gutter_regions.extend(
                        int(stats[i, cv2.CC_STAT_AREA])
                        for i in range(1, nlabels)
                        if stats[i, cv2.CC_STAT_AREA]
                        > load_policy()["source_speck_max_pixels"]
                    )
                if gutter_regions:
                    raise ValueError("source_ink_crosses_column_gutter")
                coverage = ocr_source_raster_evidence(
                    proposed, physical, source_sha, dpi=dpi
                )
            finally:
                physical.close()
        if (
            coverage.get("unaccounted_ink_pixels") != 0
            or coverage.get("page_complete") is not True
            or coverage.get("unresolved_visual_regions") != []
            or coverage.get("latin_strike_crossings") != 0
        ):
            return {
                **base,
                "reason": "source_ink_or_decoration_remains_unaccounted",
                "source_coverage": coverage,
                "column_geometry": geometric,
            }
        raw_metadata = deepcopy(independent_metadata)
        raw_metadata.update(
            selected_page_text_sha256=proposed["text_sha256"],
            text_sha256=_sha(geometric["independent_text"]),
        )
        proof = {
            **coverage,
            **base,
            "ready": True,
            "page_complete": True,
            "reading_order_proven": True,
            "page_text_sha256": proposed["text_sha256"],
            "layout_source_render_sha256": render_sha,
            "reason": "separated_columns_block_consensus_and_source_ink_proven",
            "column_geometry": geometric,
            "original_primary_text": page["text"],
            "original_primary_lines": deepcopy(page["ocr"]["lines"]),
            "original_independent_lines": deepcopy(independent_lines),
            "layout_regions": deepcopy(regions),
            "gutter_unaccounted_components": [],
            "independent_metadata": raw_metadata,
            "original_independent_metadata": deepcopy(independent_metadata),
        }
        proposed["column_order_proof"] = proof
        gate = assess_page(
            proposed,
            source_pdf_sha256=source_sha,
            package_pdf_sha256=source_sha,
            independent_text=geometric["independent_text"],
            independent_metadata=raw_metadata,
            source_geometry=proof,
        )
        return {
            **proof,
            "ready": gate["outcome"] == "auto_accepted",
            "reason": (
                proof["reason"]
                if gate["outcome"] == "auto_accepted"
                else "column_repair_quality_gate_held"
            ),
            "proposed_page": proposed,
            "independent_text": geometric["independent_text"],
            "quality_gate": gate,
        }
    except (ValueError, KeyError, TypeError) as error:
        return {
            **base,
            "reason": str(error),
            "blocked_evidence": getattr(error, "evidence", {}),
        }


def validate_column_order_proof(page: dict, proof: dict) -> bool:
    """Recompute line identities, DAG and block-local consensus from a receipt."""
    try:
        import re

        if (
            page.get("method") == "pdf_text"
            or any(
                re.fullmatch(r"[0-9a-f]{64}", proof.get(key, "")) is None
                for key in (
                    "source_pdf_sha256",
                    "layout_source_render_sha256",
                    "rendered_image_sha256",
                )
            )
            or not proof.get("layout_regions")
            or any(
                r.get("source_pdf_sha256") != proof["source_pdf_sha256"]
                or r.get("physical_page") != page["page"]
                or r.get("page_text_sha256") != proof["original_page_text_sha256"]
                or r.get("rendered_image_sha256")
                != proof["layout_source_render_sha256"]
                for r in proof["layout_regions"]
            )
        ):
            return False
        metadata = proof["original_independent_metadata"]
        if (
            metadata.get("source_pdf_sha256") != proof["source_pdf_sha256"]
            or metadata.get("page") != page["page"]
            or metadata.get("text_sha256")
            != _sha(_line_text(proof["original_independent_lines"]))
            or not metadata.get("engine")
            or not metadata.get("version")
        ):
            return False
        original = {
            **page,
            "text": proof["original_primary_text"],
            "text_sha256": proof["original_page_text_sha256"],
            "ocr": {**page["ocr"], "lines": proof["original_primary_lines"]},
        }
        if original["text_sha256"] != _sha(original["text"]):
            return False
        result = column_geometry_consensus(
            original, proof["original_independent_lines"], proof["layout_regions"]
        )
        return (
            result == proof["column_geometry"]
            and result["proposed_text"] == page["text"]
            and _sha(page["text"]) == proof["page_text_sha256"]
            and page["ocr"]["lines"]
            == [
                proof["original_primary_lines"][i] for i in result["primary_line_order"]
            ]
            and proof.get("gutter_unaccounted_components") == []
            and proof.get("page_complete") is True
            and proof.get("unaccounted_ink_pixels") == 0
            and proof.get("unresolved_visual_regions") == []
            and proof.get("latin_strike_crossings") == 0
        )
    except (ValueError, KeyError, TypeError):
        return False
