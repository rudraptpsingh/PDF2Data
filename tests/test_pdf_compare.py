import json

import pytest

from local_vector_batch.pdf_compare import complete_candidate_text, selected_pages, compare_pdf, page_region_records, export_region_crops, table_cell_region_records, region_source_observations


def test_review_selection_is_bounded_and_explicit_physical_pages_are_checked(tmp_path):
    rows = [{"page": 1, "status": "ready"}, {"page": 2, "status": "review_required"},
            {"page": 3, "status": "review_required"}]
    (tmp_path / "pages.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    assert selected_pages(tmp_path, None, 1) == [rows[1]]
    assert selected_pages(tmp_path, [3, 1], 2) == [rows[0], rows[2]]
    for pages in ([0], [4], [2, 2], [True]):
        with pytest.raises(ValueError):
            selected_pages(tmp_path, pages, 2)
    with pytest.raises(ValueError):
        selected_pages(tmp_path, [1, 2, 3], 2)


def test_picture_text_and_exact_table_cell_coordinates_survive_export():
    data = {"texts": [{"text": "Rate table", "parent": {"$ref": "#/pictures/0"}}],
            "tables": [{"data": {"table_cells": [
                {"text": "₹2.50", "start_row_offset_idx": 1, "start_col_offset_idx": 1},
                {"text": "51%", "start_row_offset_idx": 1, "start_col_offset_idx": 0},
                {"text": "Condition", "start_row_offset_idx": 0, "start_col_offset_idx": 0},
            ]}}]}
    assert complete_candidate_text(data) == "Rate table\nCondition\n51% | ₹2.50"


def test_comparison_cannot_write_inside_immutable_package(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        compare_pdf(tmp_path, tmp_path / "source.pdf", tmp_path / "comparison")


def _layout_data():
    return {"pages": {"3": {"size": {"width": 600, "height": 800}}},
            "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/tables/0"}]},
            "texts": [{"self_ref": "#/texts/0", "label": "text", "text": "51%",
                       "prov": [{"page_no": 3, "bbox": {"l": 20, "t": 760, "r": 200,
                                  "b": 700, "coord_origin": "BOTTOMLEFT"}}]}],
            "tables": [{"self_ref": "#/tables/0", "label": "table", "prov": [
                {"page_no": 3, "bbox": {"l": 20, "t": 120, "r": 500, "b": 600,
                 "coord_origin": "TOPLEFT"}}]}]}


def _regions(data, **kwargs):
    return page_region_records(data, {"id": "pdf-a:p3", "page": 3, "text_sha256": "c"*64},
        source_sha="a"*64, render_sha="b"*64, page_size=(600, 800), **kwargs)


def test_layout_regions_keep_true_coordinates_tree_order_and_source_binding():
    rows = _regions(_layout_data())
    assert [row["route"] for row in rows] == ["text_region_ocr", "table_cell_recovery"]
    assert rows[0]["bbox"] == [20, 40, 200, 100]
    assert [row["reading_order_index"] for row in rows] == [0, 1]
    assert rows[0]["reading_order_scope"] == "body"
    assert all(row["physical_page"] == 3 and row["source_pdf_sha256"] == "a"*64 and
               row["rendered_image_sha256"] == "b"*64 and row["page_text_sha256"] == "c"*64 and
               row["status"] == "review_required" and not row["content_verified"] and
               not row["reading_order_verified"] for row in rows)


def test_image_only_clusters_survive_without_invented_order_or_ignored_figure():
    data = {"pages": {"3": {"size": {"width": 600, "height": 800}}}}
    clusters = [{"id": 2, "label": "text", "confidence": .9, "bbox": {
        "l": 20, "t": 40, "r": 200, "b": 100, "coord_origin": "TOPLEFT"}},
        {"id": 3, "label": "picture", "confidence": .8, "bbox": {
        "l": 20, "t": 120, "r": 200, "b": 300, "coord_origin": "TOPLEFT"}}]
    rows = _regions(data, clusters=clusters)
    assert len(rows) == 2
    assert rows[0]["route"] == "text_region_ocr"
    assert rows[1]["route"] == "visual_region_review"
    assert all(row["reading_order_index"] is None and row["reading_order_scope"] == "unassigned"
               for row in rows)


def test_layout_region_geometry_rejects_foreign_page_canvas_and_references():
    data = _layout_data()
    data["texts"][0]["prov"][0]["page_no"] = 1
    with pytest.raises(ValueError, match="physical page"):
        _regions(data)
    data = _layout_data()
    data["pages"]["3"]["size"]["height"] = 1600
    with pytest.raises(ValueError, match="canvas"):
        _regions(data)
    data = _layout_data()
    data["body"]["children"].append({"$ref": "#/texts/0"})
    with pytest.raises(ValueError, match="repeats"):
        _regions(data)


def test_region_crops_bind_exact_original_render_and_transform_points_to_pixels(tmp_path):
    import hashlib
    from PIL import Image
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), "white").save(source)
    rows = _regions(_layout_data())
    rows = [{**row, "rendered_image_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            for row in rows]
    crops = export_region_crops(rows, source, tmp_path / "crops")
    assert crops[0]["crop_bbox_pixels"] == [37, 77, 403, 203]
    assert crops[0]["route"] == "text_region_ocr"
    assert crops[1]["route"] == "table_cell_recovery"
    assert Image.open(crops[0]["crop_path"]).size == (366, 126)
    with pytest.raises(ValueError, match="raster hash mismatch"):
        export_region_crops([{**rows[0], "rendered_image_sha256": "0"*64}], source, tmp_path / "bad")


def test_cell_regions_reuse_import_geometry_and_preserve_unverified_merged_headers():
    data = _layout_data()
    data["tables"][0]["data"] = {"num_rows": 1, "num_cols": 2, "table_cells": [
        {"text": "51%", "start_row_offset_idx": 0, "end_row_offset_idx": 1,
         "start_col_offset_idx": 0, "end_col_offset_idx": 2, "column_header": True,
         "bbox": {"l": 20, "t": 680, "r": 500, "b": 600, "coord_origin": "BOTTOMLEFT"}}]}
    rows = table_cell_region_records(data, {"id": "pdf-a:p3", "page": 3, "text_sha256": "c"*64},
        source_sha="a"*64, render_sha="b"*64, engine_version="test", language="iso:en", device="mps")
    assert len(rows) == 1 and rows[0]["bbox"] == [20, 120, 500, 200]
    assert rows[0]["column_span"] == 2 and rows[0]["column_header"] is True
    assert rows[0]["route"] == "table_cell_recovery"
    assert rows[0]["content_verified"] is False and rows[0]["row_column_verified"] is False
    assert rows[0]["numeric_value_verified"] is False


def test_region_observations_expose_ocr_order_changes_and_preserve_outside_ink(tmp_path):
    import hashlib
    from PIL import Image, ImageDraw
    source = tmp_path / "source.png"
    image = Image.new("RGB", (1200, 1600), "white")
    ImageDraw.Draw(image).rectangle((1000, 1500, 1100, 1590), fill="black")
    image.save(source)
    regions = [{**row, "rendered_image_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
               for row in _regions(_layout_data())]
    class NativePage:
        cropbox = [40, 80, 640, 880]
        chars = [{"text": "5", "x0": 60, "top": 120, "x1": 70, "bottom": 130}]
    page = {"page": 3, "ocr": {"lines": [
        {"text": "table first", "bbox": [.05, .2, .3, .25]},
        {"text": "paragraph next", "bbox": [.05, .06, .3, .1]}]}}
    result = region_source_observations(page, regions, NativePage(), source)
    assert result["ocr_line_to_region_order_descents"] == 1
    assert result["native_source_glyphs"] == 1 and result["native_glyphs_outside_layout_regions"] == 0
    assert result["source_dark_pixels_outside_all_layout_regions"] > 1000
    assert result["page_coverage_verified"] is False and result["text_content_verified"] is False
