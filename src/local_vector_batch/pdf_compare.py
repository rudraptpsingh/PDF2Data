"""Independent layout/OCR candidates tied to an immutable PDF evidence package.

Docling is optional. Its output never promotes page text or extracted facts; the
comparison is a reusable review artifact, with physical page and source hashes.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import time

from .evidence_package import verify_package
from .ocr_consensus import compare_ocr
from .pdf_layout_import import _top_left_box, _table_from_docling


TEXT_REGION_KINDS = {"text", "title", "section_header", "list_item", "caption",
                     "footnote", "page_header", "page_footer", "paragraph"}


def parse_doctags_page(tokens: str, source_image: Path, *, physical_page: int,
                       page_size_points: tuple[float, float]) -> dict:
    """Use Docling-core's parser, then restore original page/point coordinates.

    Docling-core creates page1 and image-pixel coordinates for a standalone
    DocTags image. Those are not the original physical PDF page or PDF points.
    Preserve unknown cell boxes; never invent geometry from a table's contents.
    """
    from PIL import Image
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc.doctags import DocTagsDocument
    import math
    if type(physical_page) is not int or physical_page < 1:
        raise ValueError("physical page must be positive")
    if len(page_size_points) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                                         for v in page_size_points):
        raise ValueError("original PDF page dimensions must be positive finite points")
    with Image.open(source_image) as image:
        width, height = image.size
        tags = DocTagsDocument.from_doctags_and_image_pairs([tokens], [image.copy()])
        document = DoclingDocument.load_from_doctags(tags)
    data = document.export_to_dict()
    scale_x, scale_y = page_size_points[0]/width, page_size_points[1]/height
    def restore(value):
        if isinstance(value, dict):
            if {"l", "t", "r", "b", "coord_origin"}.issubset(value):
                for key in ("l", "r"):
                    value[key] *= scale_x
                for key in ("t", "b"):
                    value[key] *= scale_y
            if "page_no" in value:
                if value["page_no"] != 1:
                    raise ValueError("standalone DocTags emitted a foreign page")
                value["page_no"] = physical_page
            for child in value.values():
                restore(child)
        elif isinstance(value, list):
            for child in value:
                restore(child)
    restore(data)
    page = data["pages"].pop("1")
    page["size"] = {"width": page_size_points[0], "height": page_size_points[1]}
    page.pop("image", None)
    data["pages"] = {str(physical_page): page}
    return data


def page_region_records(data: dict, page: dict, *, source_sha: str,
                        render_sha: str, page_size: tuple[float, float],
                        clusters: list[dict] | None = None) -> list[dict]:
    """Normalize Docling's own layout regions without approving their content.

    Preserve original physical-page coordinates and document-tree reading order.
    Empty image-only text clusters survive through the raw cluster fallback;
    their order remains unknown if Docling did not place them in its document.
    Figure, signature, and unknown regions always remain explicit review work.
    """
    import math
    number = page["page"]
    size = (data.get("pages", {}).get(str(number)) or {}).get("size") or {}
    width, height = size.get("width"), size.get("height")
    if (any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
            for v in (width, height, *page_size)) or
            any(abs(a-b) > .01 for a, b in zip((width, height), page_size))):
        raise ValueError("Docling dimensions differ from original physical-page canvas")
    nodes = {item["self_ref"]: item for key in ("texts", "tables", "pictures", "groups")
             for item in data.get(key, []) if item.get("self_ref")}
    ordered = []
    seen = set()

    def visit(ref, scope):
        if ref in seen:
            raise ValueError("Docling document tree repeats a reading-order reference")
        seen.add(ref)
        item = nodes.get(ref)
        if item is None:
            raise ValueError("Docling document tree has an absent reading-order reference")
        if item.get("prov"):
            ordered.append((scope, item))
        for child in item.get("children", []):
            visit(child["$ref"], scope)

    # Furniture has its own sequence; never insert it into body prose order.
    for root_name in ("body", "furniture"):
        for child in (data.get(root_name) or {}).get("children", []):
            visit(child["$ref"], root_name)
    observations = []
    scope_indexes = Counter()
    for scope, item in ordered:
        index = scope_indexes[scope]
        scope_indexes[scope] += 1
        for prov in item.get("prov", []):
            if prov.get("page_no") != number:
                raise ValueError("Docling region lost original physical page provenance")
            box = _top_left_box(prov["bbox"], width, height)
            observations.append({"kind": item.get("label", "unknown"), "bbox": box,
                                 "reading_order_index": index,
                                 "reading_order_scope": scope,
                                 "reading_order_basis": "docling_document_tree_candidate",
                                 "item_ref": item["self_ref"], "candidate_text": item.get("text", "")})
    for cluster in clusters or []:
        box = _top_left_box(cluster["bbox"], width, height)
        # Raw detector clusters can include text that the exported tree omitted.
        # Keep it once; matching requires the same label and near-exact box.
        if any(row["kind"] == cluster["label"] and
               all(abs(a-b) < 1 for a, b in zip(row["bbox"], box)) for row in observations):
            continue
        observations.append({"kind": cluster["label"], "bbox": box,
                             "reading_order_index": None,
                             "reading_order_scope": "unassigned",
                             "reading_order_basis": "unproven_detector_cluster",
                             "cluster_id": cluster["id"], "confidence": cluster.get("confidence"),
                             "candidate_text": ""})
    rows = []
    for index, observation in enumerate(observations, 1):
        kind = observation["kind"]
        rows.append({"id": f"{page['id']}:region-docling{index}", **observation,
                     "source_pdf_sha256": source_sha, "physical_page": number,
                     "page_text_sha256": page["text_sha256"], "rendered_image_sha256": render_sha,
                     "bbox_coordinates": "PDF points, top-left", "page_size_points": [width, height],
                     "bbox_normalized": [round(v/(width if i%2 == 0 else height), 6)
                                         for i, v in enumerate(observation["bbox"])],
                     "route": "table_cell_recovery" if kind == "table" else
                              "text_region_ocr" if kind in TEXT_REGION_KINDS else "visual_region_review",
                     "candidate_engine": "docling", "status": "review_required",
                     "content_verified": False, "reading_order_verified": False})
    return rows


def _source_raster(source: Path, number: int, output: Path, dpi: int = 120) -> dict:
    """Render the original physical page, binding every proposed region to it."""
    from io import BytesIO
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(source)) as document:
        physical = document[number-1]
        try:
            size = tuple(physical.get_size())
            bitmap = physical.render(scale=dpi/72)
            try:
                raster = bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            physical.close()
    buffer = BytesIO()
    raster.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    (output / f"page-{number}.source.png").write_bytes(image_bytes)
    return {"rendered_image_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "render_dpi": dpi, "render_size_pixels": list(raster.size),
            "page_size_points": list(size), "render_engine": "pypdfium2"}


def export_region_crops(regions: list[dict], source_raster: Path, output: Path,
                        *, padding_pixels: int = 3) -> list[dict]:
    """Stage deterministic crops from a hash-verified original-page render.

    A crop is an OCR/table/visual-review input, never a page-coverage approval.
    Unknown and figure regions are preserved with the same review-only status.
    """
    from io import BytesIO
    import math
    from PIL import Image
    if type(padding_pixels) is not int or not 0 <= padding_pixels <= 20:
        raise ValueError("region crop padding must be 0..20 pixels")
    raw = Path(source_raster).read_bytes()
    render_sha = hashlib.sha256(raw).hexdigest()
    if any(region.get("rendered_image_sha256") != render_sha for region in regions):
        raise ValueError("region source raster hash mismatch")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    with Image.open(BytesIO(raw)) as image:
        for index, region in enumerate(regions, 1):
            width, height = region["page_size_points"]
            left, top, right, bottom = region["bbox"]
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError("region crop outside original source canvas")
            bounds = [max(0, math.floor(left/width*image.width)-padding_pixels),
                      max(0, math.floor(top/height*image.height)-padding_pixels),
                      min(image.width, math.ceil(right/width*image.width)+padding_pixels),
                      min(image.height, math.ceil(bottom/height*image.height)+padding_pixels)]
            crop = image.crop(bounds)
            stream = BytesIO()
            crop.save(stream, format="PNG")
            filename = f"page-{region['physical_page']}-region-{index}.png"
            crop_bytes = stream.getvalue()
            (output / filename).write_bytes(crop_bytes)
            rows.append({**region, "crop_path": str((output / filename).resolve()),
                         "crop_sha256": hashlib.sha256(crop_bytes).hexdigest(),
                         "crop_bbox_pixels": bounds, "crop_padding_pixels": padding_pixels})
    return rows


def table_cell_region_records(data: dict, page: dict, *, source_sha: str,
                              render_sha: str, engine_version: str,
                              language: str, device: str) -> list[dict]:
    """Reuse the table importer geometry contract for unverified cell work."""
    size = data["pages"][str(page["page"])]["size"]
    width, height = size["width"], size["height"]
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()
    rows = []
    for index, raw in enumerate(data.get("tables", []), 1):
        table = _table_from_docling(raw, page=page, source_sha=source_sha,
            comparison_sha=digest, index=index, page_width=width, page_height=height,
            engine_version=engine_version, language=language, device=device)
        for cell_index, cell in enumerate(table["cell_spans"], 1):
            if cell["bbox"] is None:
                continue
            rows.append({"id": f"{table['id']}:cell{cell_index}", "table_id": table["id"],
                         "physical_page": page["page"], "kind": "table_cell",
                         "source_pdf_sha256": source_sha, "page_text_sha256": page["text_sha256"],
                         "rendered_image_sha256": render_sha, "comparison_sha256": digest,
                         "bbox": cell["bbox"], "bbox_coordinates": "PDF points, top-left",
                         "page_size_points": [width, height],
                         "row": cell["row"], "column": cell["column"],
                         "row_span": cell["row_span"], "column_span": cell["column_span"],
                         "column_header": cell["column_header"], "row_header": cell["row_header"],
                         "candidate_text": cell["text"], "route": "table_cell_recovery",
                         "candidate_engine": "docling", "status": "review_required",
                         "content_verified": False, "row_column_verified": False,
                         "numeric_value_verified": False})
    return rows


def region_source_observations(page: dict, regions: list[dict], source_page,
                                source_raster: Path) -> dict:
    """Compare proposed blocks with original ink, native glyphs and OCR lines.

    Region coverage localizes work; it does not verify the text inside a block
    or remove any unresolved ink from the independent extraction quality gate.
    """
    from PIL import Image
    import numpy as np
    import math
    raw = Path(source_raster).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if any(region["rendered_image_sha256"] != digest for region in regions):
        raise ValueError("layout observation source raster hash mismatch")
    with Image.open(source_raster) as image:
        ink = np.asarray(image.convert("L")) < 100
        covered = np.zeros(ink.shape, dtype=bool)
        text_covered = np.zeros(ink.shape, dtype=bool)
        for region in regions:
            width, height = region["page_size_points"]
            x0, y0, x1, y1 = region["bbox"]
            left, right = max(0, math.floor(x0/width*image.width)), min(image.width, math.ceil(x1/width*image.width))
            top, bottom = max(0, math.floor(y0/height*image.height)), min(image.height, math.ceil(y1/height*image.height))
            covered[top:bottom, left:right] = True
            if region["route"] == "text_region_ocr":
                text_covered[top:bottom, left:right] = True
    assignments = []
    order = []
    invalid_lines = 0
    for index, line in enumerate((page.get("ocr") or {}).get("lines", [])):
        box = line.get("bbox")
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                not box[0] < box[2] or not box[1] < box[3]):
            invalid_lines += 1
            continue
        area = (box[2]-box[0])*(box[3]-box[1])
        hits = []
        for region in regions:
            x0, y0, x1, y1 = region["bbox_normalized"]
            overlap = max(0, min(box[2], x1)-max(box[0], x0))*max(0, min(box[3], y1)-max(box[1], y0))
            if overlap/area > .2:
                hits.append((overlap/area, region))
        hits.sort(key=lambda pair: pair[0], reverse=True)
        best = hits[0][1] if hits else None
        if best and best["reading_order_index"] is not None:
            order.append(best["reading_order_index"])
        assignments.append({"line_index": index,
                            "line_text_sha256": hashlib.sha256(str(line.get("text", "")).encode()).hexdigest(),
                            "region_ids": [hit[1]["id"] for hit in hits],
                            "best_region": best["id"] if best else None,
                            "best_region_reading_order_index": best["reading_order_index"] if best else None})
    glyphs = getattr(source_page, "chars", []) or []
    glyph_counts = Counter()
    unmapped_glyphs = 0
    invalid_glyphs = 0
    origin_x, origin_y, right, bottom = source_page.cropbox
    glyph_width, glyph_height = right-origin_x, bottom-origin_y
    for glyph in glyphs:
        box = [glyph.get(key) for key in ("x0", "top", "x1", "bottom")]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            invalid_glyphs += 1
            continue
        cx = ((box[0]+box[2])/2-origin_x)/glyph_width
        cy = ((box[1]+box[3])/2-origin_y)/glyph_height
        matches = [region["id"] for region in regions
                   if region["bbox_normalized"][0] <= cx <= region["bbox_normalized"][2] and
                      region["bbox_normalized"][1] <= cy <= region["bbox_normalized"][3]]
        for match in matches:
            glyph_counts[match] += 1
        unmapped_glyphs += int(not matches)
    return {"physical_page": page["page"], "rendered_image_sha256": digest,
            "source_pdf_sha256": regions[0]["source_pdf_sha256"] if regions else None,
            "observation_scope": "unverified_layout_localization_only",
            "native_source_glyphs": len(glyphs), "selected_ocr_lines": len(assignments),
            "native_glyphs_outside_layout_regions": unmapped_glyphs,
            "invalid_native_glyph_boxes": invalid_glyphs,
            "native_glyph_counts_by_region": dict(glyph_counts),
            "invalid_ocr_line_boxes": invalid_lines,
            "ocr_line_assignments": assignments,
            "ocr_lines_with_no_layout_region": sum(not row["region_ids"] for row in assignments),
            "ocr_lines_spanning_multiple_layout_regions": sum(len(row["region_ids"]) > 1 for row in assignments),
            "ocr_line_to_region_order_descents": sum(a > b for a, b in zip(order, order[1:])),
            "source_dark_pixels": int(ink.sum()),
            "source_dark_pixel_threshold": 100,
            "source_dark_pixels_outside_all_layout_regions": int((ink & ~covered).sum()),
            "source_dark_pixels_inside_text_layout_regions": int((ink & text_covered).sum()),
            "page_coverage_verified": False, "text_content_verified": False,
            "unresolved_figures_or_signatures_ignored": False}


def _layout_runtime(converter) -> dict:
    """Read actual resident model tensor devices, rather than requested flags."""
    devices = set()
    engines = set()
    model_sources = set()
    for pipeline in converter.initialized_pipelines.values():
        engine = getattr(getattr(pipeline, "layout_model", None), "engine", None)
        if engine is None:
            continue
        engines.add(type(engine).__name__)
        model = getattr(engine, "_model", None)
        if model is not None:
            devices.update(str(parameter.device) for parameter in model.parameters())
            source = getattr(getattr(model, "config", None), "_name_or_path", None)
            if source:
                model_sources.add(str(source))
    return {"layout_engines": sorted(engines), "layout_parameter_devices": sorted(devices),
            "layout_model_sources": sorted(model_sources),
            "gpu_model_resident": bool(devices) and all(d.startswith(("mps", "cuda")) for d in devices)}


def selected_pages(package: Path, pages: list[int] | None, limit: int) -> list[dict]:
    if limit < 1:
        raise ValueError("page limit must be positive")
    rows = [json.loads(line) for line in jsonl_physical_lines((package / "pages.jsonl").read_text()) if line.strip(' \t\r')]
    if pages is not None:
        if not pages or len(set(pages)) != len(pages) or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError("pages must be unique positive physical page numbers")
        requested = set(pages)
        chosen = [row for row in rows if row["page"] in requested]
        if {row["page"] for row in chosen} != requested:
            raise ValueError("requested physical page is absent from package")
        if len(chosen) > limit:
            raise ValueError("explicit pages exceed the configured limit")
        return chosen
    return [row for row in rows if row.get("status") == "review_required"][:limit]


def complete_candidate_text(data: dict) -> str:
    """Keep OCR text even when Markdown export hides a picture's text children.

    This is an inspection representation. It does not infer reading order or
    relationships that the layout model failed to establish.
    """
    parts = [item["text"] for item in data.get("texts", []) if item.get("text")]
    for table in data.get("tables", []):
        cells = table.get("data", {}).get("table_cells", [])
        rows: dict[int, list[tuple[int, str]]] = {}
        for cell in cells:
            if cell.get("text"):
                rows.setdefault(cell.get("start_row_offset_idx", 0), []).append(
                    (cell.get("start_col_offset_idx", 0), cell["text"]))
        parts.extend(" | ".join(text for _, text in sorted(cells)) for _, cells in sorted(rows.items()))
    return "\n".join(parts)


def compare_pdf(package: Path, source: Path, output: Path, *, pages: list[int] | None = None,
                limit: int = 25, language: str = "iso:en", device: str = "auto",
                layout_only: bool = False) -> dict:
    """Run local Docling OCR/layout and save independent, unverified candidates."""
    if device not in {"auto", "cpu", "mps", "cuda"}:
        raise ValueError("unsupported layout device")
    package, source, output = Path(package), Path(source), Path(output)
    if output.resolve() == package.resolve() or package.resolve() in output.resolve().parents:
        raise ValueError("comparison output must be outside the immutable evidence package")
    if output.exists() and any(output.iterdir()):
        raise ValueError("use an empty output directory to preserve previous comparison receipts")
    verify_package(package, source_pdf=source)
    chosen = selected_pages(package, pages, limit)
    if not chosen:
        raise ValueError("no pages selected for comparison")
    try:
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, OcrMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as error:
        raise RuntimeError("install local-vector-batch[pdf-layout] in an isolated environment") from error
    options = PdfPipelineOptions(
        do_ocr=not layout_only, do_table_structure=True,
        ocr_options=RapidOcrOptions(lang=[language], mode=OcrMode.FULL_PAGE),
        accelerator_options=AcceleratorOptions(device=device, num_threads=4),
    )
    if layout_only:
        # Image-only pages need regions before OCR populates any text cells.
        options.layout_options.keep_empty_clusters = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    converter.initialize_pipeline(InputFormat.PDF)
    initialization_ms = round((time.monotonic()-started)*1000)
    forward_events = []
    hooks = []
    for pipeline in converter.initialized_pipelines.values():
        engine = getattr(getattr(pipeline, "layout_model", None), "engine", None)
        model = getattr(engine, "_model", None)
        if model is not None and hasattr(model, "register_forward_pre_hook"):
            def observe(module, args, kwargs):
                forward_events.append({"input_tensor_devices": sorted({str(value.device)
                    for value in kwargs.values() if hasattr(value, "device")}),
                    "model_parameter_devices": sorted({str(p.device) for p in module.parameters()})})
            hooks.append(model.register_forward_pre_hook(observe, with_kwargs=True))
    rows, counts = [], Counter()
    for page in chosen:
        t0 = time.monotonic()
        event_start = len(forward_events)
        try:
            result = converter.convert(source, page_range=(page["page"], page["page"]))
            data = result.document.export_to_dict()
            markdown = result.document.export_to_markdown()
            candidate = complete_candidate_text(data)
            # The one-page conversion must still carry the physical input number.
            provenance = {p["page_no"] for key in ("texts", "tables")
                          for item in data.get(key, []) for p in item.get("prov", [])}
            if provenance and provenance != {page["page"]}:
                raise ValueError(f"Docling physical page provenance mismatch: {sorted(provenance)}")
            tables = data.get("tables", [])
            raster = _source_raster(source, page["page"], output)
            clusters = [cluster.model_dump(mode="json") for physical in result.pages
                        if physical.page_no == page["page"] and physical.predictions.layout
                        for cluster in physical.predictions.layout.clusters]
            regions = page_region_records(data, page, source_sha=source_sha,
                render_sha=raster["rendered_image_sha256"],
                page_size=tuple(raster["page_size_points"]), clusters=clusters)
            if layout_only:
                regions = export_region_crops(regions,
                    output / f"page-{page['page']}.source.png", output / "regions")
            cell_regions = table_cell_region_records(data, page, source_sha=source_sha,
                render_sha=raster["rendered_image_sha256"], engine_version=version("docling"),
                language=language, device=device)
            if layout_only:
                cell_regions = export_region_crops(cell_regions,
                    output / f"page-{page['page']}.source.png", output / "table-cells")
            import pdfplumber
            with pdfplumber.open(source) as native_document:
                observations = region_source_observations(page, regions,
                    native_document.pages[page["page"]-1], output / f"page-{page['page']}.source.png")
            with (output / "page-regions.jsonl").open("a", encoding="utf-8") as stream:
                for region in regions:
                    stream.write(json.dumps(region, ensure_ascii=False)+"\n")
            with (output / "page-table-cells.jsonl").open("a", encoding="utf-8") as stream:
                for cell in cell_regions:
                    stream.write(json.dumps(cell, ensure_ascii=False)+"\n")
            comparison = compare_ocr(page["text"], candidate,
                vision_confidence=0, independent_confidence=0,
                has_table_or_layout_warning=bool(tables))
            row = {"source_pdf_sha256": source_sha, "source_page": page["page"],
                   "selected_text_sha256": page["text_sha256"], "selected_method": page.get("method"),
                   "candidate_engine": "docling", "candidate_engine_version": version("docling"),
                   "candidate_language": language, "requested_device": device,
                   "layout_only": layout_only, "source_render": raster,
                   "layout_runtime": {**_layout_runtime(converter),
                                      "forward_events": forward_events[event_start:]},
                   "candidate_regions": regions, "raw_layout_clusters": clusters,
                   "candidate_cell_regions": cell_regions,
                   "region_source_observations": observations,
                   "pipeline_timings": {key: value.model_dump(mode="json")
                                        for key, value in result.timings.items()},
                   "candidate_text": candidate,
                   "candidate_markdown": markdown,
                   "candidate_text_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
                   "candidate_tables": tables, "comparison": comparison,
                   "status": "review_required", "elapsed_ms": round((time.monotonic()-t0)*1000)}
            (output / f"page-{page['page']}.docling.json").write_text(json.dumps(data, ensure_ascii=False))
            counts["compared"] += 1
            counts["tables"] += len(tables)
            counts["regions"] += len(regions)
            counts["numeric_disagreements"] += int(not comparison["numeric_exact"])
        except Exception as error:
            row = {"source_pdf_sha256": source_sha, "source_page": page["page"],
                   "selected_text_sha256": page["text_sha256"], "status": "comparison_failed",
                   "error": str(error)[:500], "elapsed_ms": round((time.monotonic()-t0)*1000)}
            counts["failed"] += 1
        rows.append(row)
        with (output / "page-comparisons.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {"source_pdf_sha256": source_sha, "pages_requested": len(chosen),
              "counts": dict(counts), "elapsed_seconds": round(time.monotonic()-started, 2),
              "initialization_ms": initialization_ms, "layout_only": layout_only,
              "layout_runtime": _layout_runtime(converter),
              "source_package_modified": False, "pages_promoted": 0,
              "candidate_status": "review_required", "output": str(output)}
    (output / "comparison-report.json").write_text(json.dumps(report, indent=2) + "\n")
    for hook in hooks:
        hook.remove()
    return report
