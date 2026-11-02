"""Orthogonal, source-bound extraction gates; no weighted confidence score.

An auto acceptance is a machine evidence decision, never a human source-visual
review receipt or a verified legal fact. Unknown evidence remains held.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from collections import Counter
import hashlib
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import re
from datetime import datetime, timezone
import unicodedata
import uuid

from .evidence_package import verify_package


from .unicode_words import WORD
NUMBER = re.compile(r"(?<!\w)\d+(?:[.,:/-]\d+)*(?:\s*%)?(?!\w)")
STATES = {"pass", "fail", "unknown"}


def load_policy(path: Path | None = None) -> dict:
    raw = Path(path).read_text(encoding="utf-8") if path else files("local_vector_batch").joinpath("quality_policy.json").read_text(encoding="utf-8")
    policy = json.loads(raw)
    visual_fraction=policy.get('native_raster_region_min_page_fraction',0.0)
    containment_tolerance=policy.get('table_geometry_containment_tolerance_points',0.25)
    if (type(visual_fraction) not in (int,float) or not 0<=visual_fraction<=1 or
        type(containment_tolerance) not in (int,float) or not 0<=containment_tolerance<=2):
        raise ValueError('invalid native visual geometry policy')
    if (policy.get("schema_version") != 1 or policy.get('require_exact_marked_word_tokens')is not True or
            any(not 0 <= policy[key] <= 1 for key in ("word_recall_min", "word_precision_min", "word_order_min")) or
            type(policy.get("source_ink_unaccounted_max")) is not int or policy["source_ink_unaccounted_max"] < 0 or
            any(type(policy.get(key)) is not int or policy[key] < 0 for key in
                ("source_dark_pixel_threshold", "source_edge_exclusion_px", "ocr_box_padding_px", "source_speck_max_pixels", "footer_rule_min_aspect")) or
            not 0 < policy["source_dark_pixel_threshold"] < 256 or
            any(type(policy.get(key)) not in (float, int) or not 0 < policy[key] < 1 for key in
                ("footer_rule_min_width_fraction", "footer_rule_max_height_fraction", "header_rule_max_bottom_fraction", "ocr_box_max_normalized_overhang")) or
            not isinstance(policy.get("operators"), list) or not policy["operators"] or
            any(not isinstance(term, str) or not term.strip() for term in policy["operators"]) or
            any(type(policy.get(key)) is not bool for key in (
                "require_exact_ordered_numbers", "require_exact_ordered_operators",
                "require_exact_marked_word_tokens",
                "require_source_render_coverage", "require_row_column_proof_for_tables"))):
        raise ValueError("invalid extraction quality policy")
    required_scripts = policy.get('native_scripts_requiring_raster_linguistic_proof')
    if (not isinstance(required_scripts, list) or not required_scripts or
            len(required_scripts) != len(set(required_scripts)) or
            any(name not in policy['supported_scripts'] for name in required_scripts) or
            type(policy.get('native_complex_script_min_characters')) is not int or
            policy['native_complex_script_min_characters'] < 1):
        raise ValueError('invalid native complex-script verification policy')
    font_routes = policy.get('native_font_families_requiring_raster_proof', {})
    if (not isinstance(font_routes, dict) or any(
            not isinstance(name, str) or not name or name != name.casefold() or
            script not in required_scripts for name, script in font_routes.items())):
        raise ValueError('invalid native font verification policy')
    return policy


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)


def _operator_pattern(policy: dict):
    terms = sorted((_normalized(term) for term in policy["operators"]), key=len, reverse=True)
    parts = []
    for term in terms:
        escaped = re.escape(term).replace(r'\ ',r'\s+')
        parts.append((r"(?<!\w)" if term[0].isalnum() else "") + escaped +
                     (r"(?!\w)" if term[-1].isalnum() else ""))
    return re.compile("(?:" + "|".join(parts) + ")")


def _operators(text: str, policy: dict) -> list[str]:
    return [' '.join(m.group().split()) for m in _operator_pattern(policy).finditer(_normalized(text))]


def operator_source_spans(text: str, policy: dict):
    """Raw codepoint spans; refuse to guess offsets through Unicode expansion."""
    pattern=re.compile(_operator_pattern(policy).pattern,re.IGNORECASE)
    rows=[dict(canonical_operator=' '.join(_normalized(m.group()).split()),exact_quote=m.group(),
        char_start=m.start(),char_length=m.end()-m.start(),offset_encoding='codepoint') for m in pattern.finditer(text)]
    return rows if [r['canonical_operator'] for r in rows]==_operators(text,policy) else None


def compare_cached_native_operators(package: Path, source_pdf: Path) -> dict:
    """New comparison candidate only; every original page outcome is retained."""
    from copy import deepcopy
    from .legal_l1 import verify_document,rows
    from .field_witness import SOURCE_FILES
    from .hashing import file_sha256
    package=Path(package).resolve();source_pdf=Path(source_pdf).resolve();verify_document(package)
    before={n:file_sha256(package/n) for n in SOURCE_FILES}
    if file_sha256(source_pdf)!=before['original.pdf']:raise ValueError('Cached operator source PDF differs')
    quality=json.loads((package/'quality.json').read_text());result=deepcopy(quality)
    pages={r['page']:r for r in rows(package/'extraction/pages.jsonl')}
    layout={r['page']:r for r in rows(package/'layout.jsonl')};policy=load_policy();comparisons=[]
    for old in quality['results']:
        n=old['page'];page=pages[n];native=layout[n];independent=native.get('quality_independent_text')
        if native['page_text_sha256']!=page['text_sha256'] or old['page_text_sha256']!=page['text_sha256']:
            raise ValueError('Cached operator page identity differs')
        if independent is not None and hashlib.sha256(independent.encode()).hexdigest()!=native.get('quality_independent_text_sha256'):
            raise ValueError('Cached independent operator reading hash differs')
        a=operator_source_spans(page['text'],policy);b=operator_source_spans(independent,policy) if independent else None
        left=[r['canonical_operator'] for r in a] if a is not None else None
        right=[r['canonical_operator'] for r in b] if b is not None else None
        comparisons.append(dict(page=n,page_text_sha256=page['text_sha256'],
            independent_text_sha256=native.get('quality_independent_text_sha256'),
            independent_offset_scope='frozen_layout.quality_independent_text_not_selected_page',
            original_component=deepcopy(old['components']['ordered_operators']),
            candidate_state='pass' if a is not None and b is not None and left==right else 'held',
            selected=left,independent=right,selected_source_spans=a,independent_reader_spans=b))
    if before!={n:file_sha256(package/n) for n in SOURCE_FILES} or file_sha256(source_pdf)!=before['original.pdf']:
        raise ValueError('Cached operator source changed during comparison')
    result['operator_comparison']=dict(schema_version='pdf2data.cached-native-operator-comparison.v1',
        source_files_sha256=before,implementation_sha256=file_sha256(Path(__file__)),
        policy_file_sha256=file_sha256(Path(__file__).with_name('quality_policy.json')),results=comparisons,
        comparison_scope='ordered_operator_tokens_only_not_semantic_equivalence',
        original_ledger_preserved=True,original_page_outcomes_preserved=True,
        source_geometry_rederived=False,new_content_extraction=False,new_ocr_calls=0,model_calls=0,
        source_quality_approval_granted=False,language_layout_or_raster_clearance=False,publication_eligible=False)
    return result


def _component(state: str, reason: str | None = None, **evidence) -> dict:
    assert state in STATES
    return {"state": state, **({"reason": reason} if reason else {}), **evidence}


def native_source_raster_evidence(page: dict, source_page, source_pdf_sha256: str,
                                  independent_text: str | None, *, dpi: int = 120,
                                  policy: dict | None = None, table_count: int = 0) -> dict:
    """Audit original native glyph geometry against rendered physical-page ink.

    Images and vector objects are recorded, then judged by their visible ink.
    Exact independent word/number order and bidirectional glyph coverage are
    required in addition to zero unresolved source regions. This is machine
    evidence, never a human review receipt or proof of table cell relationships.
    """
    if not 72 <= dpi <= 240:
        raise ValueError("native source audit DPI must be 72..240")
    from .pdf_ingest import independent_text_coverage
    import math
    import numpy as np
    import cv2

    policy = policy or load_policy()
    text = page.get("text", "")
    from .text_script import native_font_script_evidence
    result = {"method": "native_glyph_raster_coverage", "ready": False,
              "page_complete": False, "reading_order_proven": False,
              "source_pdf_sha256": source_pdf_sha256, "page": page["page"],
              "page_text_sha256": page["text_sha256"],
              "native_font_script_evidence": native_font_script_evidence(
                  getattr(source_page, 'chars', []) or [], policy),
              "nontext_objects": {kind: len(getattr(source_page, kind, []) or [])
                                  for kind in ("images", "curves", "rects", "lines")}}
    if hashlib.sha256(text.encode()).hexdigest() != page["text_sha256"]:
        return {**result, "reason": "page_text_hash_mismatch"}
    if not text or independent_text is None or not independent_text:
        return {**result, "reason": "missing_native_or_independent_text"}
    coverage = independent_text_coverage(text, independent_text)
    reverse = independent_text_coverage(independent_text, text)
    same_words = WORD.findall(_normalized(text)) == WORD.findall(_normalized(independent_text))
    same_numbers = NUMBER.findall(_normalized(text)) == NUMBER.findall(_normalized(independent_text))
    result.update(independent_coverage=coverage, native_coverage_by_independent=reverse,
                  independent_text_sha256=hashlib.sha256(independent_text.encode()).hexdigest(),
                  independent_word_order_agrees=same_words,
                  independent_number_order_agrees=same_numbers)
    if any(item["char_recall"] != 1 or item["digit_recall"] not in (None, 1)
           for item in (coverage, reverse)):
        return {**result, "reason": "independent_text_disagrees"}
    chars = getattr(source_page, "chars", []) or []
    if not chars:
        return {**result, "reason": "missing_native_glyph_geometry"}
    glyph_text = "".join(str(char.get("text", "")) for char in chars)
    glyph_coverage = independent_text_coverage(text, glyph_text)
    glyph_reverse = independent_text_coverage(glyph_text, text)
    result.update(native_glyph_coverage=glyph_coverage,
                  native_glyph_reverse_coverage=glyph_reverse, glyph_count=len(chars))
    if any(item["char_recall"] != 1 or item["digit_recall"] not in (None, 1)
           for item in (glyph_coverage, glyph_reverse)):
        return {**result, "reason": "native_text_omits_pdf_character"}
    try:
        # Hash the unmodified original PNG, also used by native repair receipts.
        rendered = source_page.to_image(resolution=dpi)
        raster = rendered.original
        stream = BytesIO()
        raster.save(stream, format="PNG")
        result.update(render_dpi=dpi, render_size=list(raster.size),
                      rendered_image_sha256=hashlib.sha256(stream.getvalue()).hexdigest())
        ink = np.asarray(raster.convert("L")) < policy["source_dark_pixel_threshold"]
        edge = policy["source_edge_exclusion_px"]
        if edge:
            ink[:edge, :] = ink[-edge:, :] = False
            ink[:, :edge] = ink[:, -edge:] = False
        covered = np.zeros(ink.shape, dtype=bool)
        body_bottom = 0
        # pdfplumber coordinates include the page origin for cropped pages.
        origin_x, origin_y, right, bottom = rendered.bbox
        result["source_bbox"] = list(rendered.bbox)
        scale_x, scale_y = raster.width / (right-origin_x), raster.height / (bottom-origin_y)
        pad = policy["ocr_box_padding_px"]
        glyph_boxes = []
        for char in chars:
            box = [char.get(key) for key in ("x0", "top", "x1", "bottom")]
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in box) or
                    box[0] > box[2] or box[1] > box[3]):
                return {**result, "reason": "invalid_native_glyph_box"}
            # Zero-width spaces do not authorize a mask over adjacent ink.
            if not str(char.get("text", "")).strip():
                continue
            if box[0] == box[2] or box[1] == box[3]:
                return {**result, "reason": "invalid_native_glyph_box"}
            x0 = max(0, int((box[0]-origin_x)*scale_x)-pad)
            x1 = min(raster.width, int((box[2]-origin_x)*scale_x)+pad+1)
            y0 = max(0, int((box[1]-origin_y)*scale_y)-pad)
            y1 = min(raster.height, int((box[3]-origin_y)*scale_y)+pad+1)
            if x0 >= x1 or y0 >= y1:
                return {**result, "reason": "invalid_native_glyph_box"}
            covered[y0:y1, x0:x1] = True
            glyph_boxes.append(box)
            if y0 < .85*raster.height:
                body_bottom = max(body_bottom, y1)
        result["glyph_boxes_sha256"] = hashlib.sha256(json.dumps(glyph_boxes, separators=(",", ":")).encode()).hexdigest()
        # Explicit native underline rectangles explain visible rule ink in gaps
        # between glyph boxes. Retain these as style evidence; do not relax the
        # zero-unaccounted-ink threshold or mask general graphics.
        from .native_styles import source_underlines
        underline_regions = source_underlines(source_page, table_count=table_count)
        for region in underline_regions:
            x0, y0, x1, y1 = region["source_bbox"]
            left=max(0,int((x0-origin_x)*scale_x));right=min(raster.width,int((x1-origin_x)*scale_x)+1)
            top=max(0,int((y0-origin_y)*scale_y));bottom=min(raster.height,int((y1-origin_y)*scale_y)+1)
            covered[top:bottom,left:right]=True
        result["source_style_regions"] = underline_regions
        body_limit = min(raster.height, body_bottom+int(.04*raster.height)) if body_bottom else raster.height
        outside = (ink & ~covered).astype("uint8")
        nlabels, _, stats, _ = cv2.connectedComponentsWithStats(outside, 8)
        from .source_rules import dotted_horizontal_rules
        dotted_indices, decorative = dotted_horizontal_rules(ink, stats, body_limit=body_limit,
            table_count=table_count, policy=policy)
        unresolved = []
        for index in range(1, nlabels):
            if index in dotted_indices:
                continue
            x, y, width, height, area = [int(value) for value in stats[index]]
            if area <= policy["source_speck_max_pixels"]:
                continue
            region = {"bbox_pixels": [x, y, x+width, y+height], "ink_pixels": area,
                      "bbox_normalized": [round(x/raster.width, 6), round(y/raster.height, 6),
                                          round((x+width)/raster.width, 6), round((y+height)/raster.height, 6)]}
            horizontal = (width >= policy["footer_rule_min_width_fraction"]*raster.width and
                          height <= policy["footer_rule_max_height_fraction"]*raster.height and
                          width/max(1, height) >= policy["footer_rule_min_aspect"])
            footer = horizontal and y >= body_limit
            header = horizontal and table_count == 0 and y+height <= policy["header_rule_max_bottom_fraction"]*raster.height
            if footer or header:
                region["classification"] = "footer_horizontal_rule" if footer else "header_horizontal_rule"
                decorative.append(region)
            else:
                unresolved.append(region)
        result.update(ink_pixels=int(ink.sum()),
                      unaccounted_ink_pixels=sum(region["ink_pixels"] for region in unresolved),
                      unresolved_visual_regions=unresolved, decorative_regions=decorative)
        if unresolved:
            return {**result, "reason": "source_ink_outside_native_glyphs"}
        if not (same_words and same_numbers):
            return {**result, "reason": "independent_order_or_numbers_disagree"}
        return {**result, "ready": True, "page_complete": True, "reading_order_proven": True,
                "reason": "native_glyphs_account_for_source_ink"}
    except Exception as error:
        return {**result, "reason": "source_render_failed", "error": str(error)[:180]}


def ocr_source_raster_evidence(page: dict, source_page, source_pdf_sha256: str,
                               *, dpi: int = 120, policy: dict | None = None,
                               table_count: int = 0) -> dict:
    """Re-render one original physical page and audit recorded OCR line boxes.

    A scanned backing image is expected and is not an extraction failure. Dark
    source ink outside every OCR box, parallel columns, strike marks crossing
    Latin text, or disagreement between recorded lines and selected text hold
    the page. This is an algorithmic receipt, never a human visual review.
    """
    if dpi < 72 or dpi > 240:
        raise ValueError("OCR source audit DPI must be 72..240")
    policy = policy or load_policy()
    import numpy as np
    import cv2
    lines = (page.get("ocr") or {}).get("lines") or []
    if not lines:
        return {"source_pdf_sha256": source_pdf_sha256, "page": page["page"],
                "page_text_sha256": page["text_sha256"], "ready": False,
                "reason": "ocr_line_boxes_missing", "reading_order_proven": False}
    image = source_page.render(scale=dpi/72).to_pil().convert("L")
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    rendered_image_sha256 = hashlib.sha256(image_buffer.getvalue()).hexdigest()
    pixels = np.asarray(image)
    ink = pixels < policy["source_dark_pixel_threshold"]
    # PDF renderers and source scans can draw a one-pixel page-boundary line.
    # It cannot contain legible body text. Do not let that edge artifact mask
    # an actual unrecognized line elsewhere on the page.
    edge = policy["source_edge_exclusion_px"]
    if edge:
        ink[:edge, :] = False
        ink[-edge:, :] = False
        ink[:, :edge] = False
        ink[:, -edge:] = False
    covered = np.zeros(ink.shape, dtype=bool)
    boxes = []
    clipped_boxes = 0
    for line in lines:
        box = line.get("bbox")
        tolerance = policy["ocr_box_max_normalized_overhang"]
        if (not isinstance(box, list) or len(box) != 4 or
                any(not isinstance(value, (float, int)) or value < -tolerance or value > 1+tolerance for value in box) or
                not (box[0] < box[2] and box[1] < box[3])):
            return {"source_pdf_sha256": source_pdf_sha256, "page": page["page"],
                    "page_text_sha256": page["text_sha256"], "ready": False,
                    "reason": "ocr_line_box_invalid", "reading_order_proven": False}
        bounded = [min(1.0, max(0.0, float(value))) for value in box]
        if not (bounded[0] < bounded[2] and bounded[1] < bounded[3]):
            return {"source_pdf_sha256": source_pdf_sha256, "page": page["page"],
                    "page_text_sha256": page["text_sha256"], "ready": False,
                    "reason": "ocr_line_box_outside_source_canvas", "reading_order_proven": False}
        clipped_boxes += int(bounded != box)
        box = bounded
        pad = policy["ocr_box_padding_px"]
        x0 = max(0, int(box[0]*image.width)-pad)
        y0 = max(0, int(box[1]*image.height)-pad)
        x1 = min(image.width, int(box[2]*image.width)+pad+1)
        y1 = min(image.height, int(box[3]*image.height)+pad+1)
        covered[y0:y1, x0:x1] = True
        boxes.append((box, (x0, y0, x1, y1)))
    outside = (ink & ~covered).astype("uint8")
    nlabels, _, stats, _ = cv2.connectedComponentsWithStats(outside, 8)
    body_lines = [box for box, _ in boxes if box[1] < .85]
    body_bottom = max((box[3] for box in body_lines), default=1.0)
    body_limit = min(image.height, int((body_bottom + .04) * image.height))
    from .source_rules import dotted_horizontal_rules, continuous_footer_rule_fragment
    dotted_indices, decorative = dotted_horizontal_rules(ink, stats, body_limit=body_limit,
        table_count=table_count, policy=policy)
    unresolved = []
    body_unaccounted = 0
    for index in range(1, nlabels):
        if index in dotted_indices:
            continue
        x, y, width, height, area = [int(v) for v in stats[index]]
        # Isolated anti-aliased flecks are not a word or number. Any connected
        # region large enough for a glyph is retained with exact coordinates.
        if area <= policy["source_speck_max_pixels"]:
            continue
        region = {"bbox_pixels": [x, y, x + width, y + height],
                  "bbox_normalized": [round(x/image.width, 6), round(y/image.height, 6),
                                      round((x+width)/image.width, 6), round((y+height)/image.height, 6)],
                  "ink_pixels": area, "location": "body" if y < body_limit else "footer_visual"}
        horizontal_rule = (width >= policy["footer_rule_min_width_fraction"] * image.width and
                height <= policy["footer_rule_max_height_fraction"] * image.height and
                width / max(height, 1) >= policy["footer_rule_min_aspect"])
        footer_rule = ((horizontal_rule and y >= body_limit) or
                       continuous_footer_rule_fragment(
                           ink, x=x, y=y, width=width, height=height, area=area,
                           body_limit=body_limit, policy=policy))
        header_rule = (horizontal_rule and table_count == 0 and
                       y + height <= policy["header_rule_max_bottom_fraction"] * image.height)
        if footer_rule or header_rule:
            region["classification"] = "footer_horizontal_rule" if footer_rule else "header_horizontal_rule"
            decorative.append(region)
            continue
        unresolved.append(region)
        if y < body_limit:
            body_unaccounted += area
    unaccounted = sum(region["ink_pixels"] for region in unresolved)
    # Two independently populated columns at the same height must have an
    # explicit reading-order proof; simple OCR line order does not provide it.
    parallel = 0
    for i, (a, _) in enumerate(boxes):
        for b, _ in boxes[i+1:]:
            aligned = min(a[3], b[3]) - max(a[1], b[1])
            if (aligned > .4 * min(a[3]-a[1], b[3]-b[1]) and
                    (a[2]+.06 < b[0] or b[2]+.06 < a[0]) and
                    a[2]-a[0] < .5 and b[2]-b[0] < .5):
                parallel += 1
    strike = 0
    if not any("\u0900" <= char <= "\u097f" for char in page.get("text", "")):
        for _, (x0, y0, x1, y1) in boxes:
            height = y1-y0
            width = x1-x0
            if width < 15 or height < 6:
                continue
            center = ink[y0+int(.35*height):y0+int(.65*height), x0:x1]
            if center.size and np.any(center.mean(axis=1) > .8):
                strike += 1
    line_text = "\n".join(str(line.get("text", "")) for line in lines)
    order_exact = WORD.findall(_normalized(line_text)) == WORD.findall(_normalized(page.get("text", "")))
    return {"source_pdf_sha256": source_pdf_sha256, "page": page["page"],
            "page_text_sha256": page["text_sha256"], "method": "source_raster_ocr_box_coverage",
            "render_dpi": dpi, "ink_pixels": int(ink.sum()),
            "rendered_image_sha256": rendered_image_sha256,
            "clipped_line_boxes": clipped_boxes,
            "unaccounted_ink_pixels": unaccounted,
            "body_unaccounted_ink_pixels": body_unaccounted,
            "unresolved_visual_regions": unresolved,
            "decorative_regions": decorative,
            "text_region_bbox_normalized": [min((box[0] for box in body_lines), default=0),
                                            min((box[1] for box in body_lines), default=0),
                                            max((box[2] for box in body_lines), default=1), body_bottom],
            "parallel_column_pairs": parallel, "latin_strike_crossings": strike,
            "recorded_line_order_matches_selected_text": order_exact,
            "reading_order_proven": order_exact and parallel == 0 and strike == 0,
            "ready": body_unaccounted == 0,
            "page_complete": unaccounted == 0,
            "reason": "unrecognized_body_ink" if body_unaccounted else
                      "footer_visual_region_held" if unaccounted else
                      "reading_order_or_decoration_unproven" if not (order_exact and parallel == 0 and strike == 0) else
                      "source_ink_and_reading_order_accounted_for"}


def assess_page(page: dict, *, source_pdf_sha256: str | None,
                package_pdf_sha256: str, independent_text: str | None = None,
                independent_metadata: dict | None = None,
                source_geometry: dict | None = None, layout_proof: dict | None = None,
                table_count: int = 0, policy: dict | None = None,
                source_pdf_path: Path | None = None,
                native_font_evidence: dict | None = None) -> dict:
    """Assess one physical page; every required component must pass separately."""
    policy = policy or load_policy()
    source_ok = (source_pdf_sha256 == package_pdf_sha256 and
                 page.get("text_sha256") == hashlib.sha256(page.get("text", "").encode()).hexdigest() and
                 isinstance(page.get("page"), int) and page["page"] > 0)
    components = {"source_identity": _component("pass" if source_ok else "fail",
                                                 None if source_ok else "source_or_page_hash_mismatch")}
    if not source_ok:
        return {"source_pdf_sha256": package_pdf_sha256, "page": page.get("page"),
                "page_text_sha256": page.get("text_sha256"),
                "outcome": "source_blocked", "components": components,
                "missing_evidence": [], "failed_components": ["source_identity"],
                "acceptance_scope": "none"}

    text = page.get("text", "")
    if not text.strip():
        components["text_content"] = _component("fail", "no_searchable_text")
    elif page.get("selected_quality", {}).get("needs_ocr"):
        components["text_content"] = _component("fail", "unresolved_encoded_glyphs")
    else:
        components["text_content"] = _component("pass", chars=len(text))

    script = "devanagari" if any("\u0900" <= c <= "\u097f" for c in text) else "latin" if any("a" <= c.lower() <= "z" for c in text) else "unknown"
    recognition = (page.get("ocr") or {}).get("recognition_languages") or []
    independent_language_proof = bool(
        independent_text is not None and independent_metadata and
        independent_metadata.get("source_pdf_sha256") == package_pdf_sha256 and
        independent_metadata.get("page") == page["page"] and
        independent_metadata.get("selected_page_text_sha256") == page["text_sha256"] and
        independent_metadata.get("text_sha256") == hashlib.sha256(independent_text.encode()).hexdigest() and
        independent_metadata.get("engine") == "rapidocr_onnx" and
        isinstance(independent_metadata.get("version"), str) and
        bool(independent_metadata.get("version")) and
        independent_metadata.get("recognition_model") == "devanagari_PP-OCRv5_rec_mobile" and
        re.fullmatch(r"[0-9a-f]{64}", independent_metadata.get("recognition_model_sha256") or "") is not None and
        any(code.startswith(("hi", "hin")) for code in independent_metadata.get("recognition_languages", []))
    )
    if script not in policy["supported_scripts"]:
        components["language"] = _component("fail", "unsupported_or_unknown_script", detected_script=script)
    elif page.get("method") == "pdf_text":
        from .text_script import native_script_verification
        required = native_script_verification(text, policy)['required_scripts']
        native_font_evidence = native_font_evidence or (source_geometry or {}).get('native_font_script_evidence')
        font_required = (native_font_evidence or {}).get('required_scripts', [])
        required = sorted(set(required) | set(font_required))
        if required and not independent_language_proof:
            components['language'] = _component('unknown', 'native_complex_script_glyph_mapping_unverified',
                detected_script=script, required_scripts=required,
                source_font_evidence=native_font_evidence,
                native_parser_agreement_is_linguistic_proof=False)
        else:
            components['language'] = _component('pass', detected_script=script, reader='native_pdf_text',
                independent_model_confirmed=independent_language_proof)

    elif script == "devanagari" and not (any(code.startswith(("hi", "hin")) for code in recognition) or
                                             independent_language_proof):
        components["language"] = _component("fail", "devanagari_ocr_model_not_confirmed", detected_script=script,
                                             recognition_languages=recognition)
    elif script == "latin" and not (recognition or page.get("ocr", {}).get("engine") in {"vision", "tesseract"}):
        components["language"] = _component("unknown", "ocr_model_language_unreported", detected_script=script)
    else:
        components["language"] = _component("pass", detected_script=script,
                                             recognition_languages=recognition,
                                             independent_model_confirmed=independent_language_proof)

    from .column_order import validate_column_order_proof
    from .row_layout import validate_label_value_proof
    from .table_grid import validate_table_grid_proof
    external_geometry = source_geometry is not None
    source_geometry = source_geometry if external_geometry else page.get("short_source_evidence")
    row_proof = layout_proof or source_geometry or {}
    row_layout_valid = (row_proof.get("kind") == "source_bound_label_value_rows" and
                        row_proof.get("source_pdf_sha256") == package_pdf_sha256 and
                        validate_label_value_proof(page, row_proof))
    table_grid_valid = (table_count == 1 and
                        validate_table_grid_proof(page, layout_proof or {}, source_pdf_path))
    if not policy["require_source_render_coverage"]:
        components["source_render"] = _component("unknown", "source_render_gate_disabled_by_policy")
    elif external_geometry and (source_geometry.get("source_pdf_sha256") != package_pdf_sha256 or
                              source_geometry.get("page") != page["page"] or
                              source_geometry.get("page_text_sha256") != page["text_sha256"]):
        components["source_render"] = _component("fail", "source_geometry_receipt_provenance_mismatch")
    elif source_geometry is None:
        components["source_render"] = _component("unknown", "no_source_ink_and_glyph_coverage_receipt")
    elif (source_geometry.get("method") == "source_raster_column_order_coverage" and
          not validate_column_order_proof(page, source_geometry)):
        components["source_render"] = _component("fail", "column_line_identity_geometry_or_block_consensus_unproven")
    elif source_geometry.get("method") == "source_raster_label_value_coverage" and not row_layout_valid:
        components["source_render"] = _component("fail", "label_value_row_identity_geometry_or_cell_consensus_unproven")
    elif (source_geometry.get("ready") and
          source_geometry.get("body_unaccounted_ink_pixels", source_geometry.get("unaccounted_ink_pixels")) is not None and
          source_geometry.get("body_unaccounted_ink_pixels", source_geometry.get("unaccounted_ink_pixels")) <= policy["source_ink_unaccounted_max"] and
          (page.get("method") != "pdf_text" or not any(source_geometry.get("nontext_objects", {}).values()) or
           (source_geometry.get("method") == "native_glyph_raster_coverage" and
            source_geometry.get("page_complete") is True and
            source_geometry.get("reading_order_proven") is True and
            source_geometry.get("independent_word_order_agrees") is True and
            source_geometry.get("independent_number_order_agrees") is True and
            source_geometry.get("unaccounted_ink_pixels") == 0 and
            source_geometry.get("unresolved_visual_regions") == [] and
            re.fullmatch(r"[0-9a-f]{64}", source_geometry.get("rendered_image_sha256", "")) is not None))):
        components["source_render"] = _component("pass", ink_pixels=source_geometry.get("ink_pixels"),
                                                   unaccounted_ink_pixels=source_geometry["unaccounted_ink_pixels"],
                                                   receipt_sha256=hashlib.sha256(json.dumps(source_geometry, sort_keys=True,
                                                       ensure_ascii=False).encode()).hexdigest(),
                                                   rendered_image_sha256=source_geometry.get("rendered_image_sha256"),
                                                   page_complete=source_geometry.get("page_complete", True),
                                                   unresolved_visual_regions=source_geometry.get("unresolved_visual_regions", []))
    else:
        components["source_render"] = _component("fail", source_geometry.get("reason", "source_ink_not_accounted_for"),
                                                   unaccounted_ink_pixels=source_geometry.get("unaccounted_ink_pixels"))

    if independent_text is None:
        for name in ("words_and_order", "ordered_numbers", "ordered_operators"):
            components[name] = _component("unknown", "independent_text_unavailable")
    else:
        a, b = _normalized(text), _normalized(independent_text)
        aw, bw = WORD.findall(a), WORD.findall(b)
        overlap = sum((Counter(aw) & Counter(bw)).values())
        recall = overlap / len(aw) if aw else 0.0
        precision = overlap / len(bw) if bw else 0.0
        try:
            from rapidfuzz.distance import Levenshtein
            order = Levenshtein.normalized_similarity(aw, bw)
        except ImportError:
            from difflib import SequenceMatcher
            order = SequenceMatcher(None, aw, bw, autojunk=False).ratio()
        marked_a=[word for word in aw if any(unicodedata.category(c).startswith('M')for c in word)]
        marked_b=[word for word in bw if any(unicodedata.category(c).startswith('M')for c in word)]
        marked_exact=marked_a==marked_b
        word_ok = (recall >= policy["word_recall_min"] and precision >= policy["word_precision_min"]
                   and order >= policy["word_order_min"]and marked_exact)
        components["words_and_order"] = _component("pass" if word_ok else "fail",
            None if word_ok else "independent_combining_mark_word_disagreement"if not marked_exact else "independent_text_word_or_order_disagreement",
            word_recall=round(recall, 5), word_precision=round(precision, 5), word_order=round(order, 5),
            marked_word_tokens_exact=marked_exact)
        an, bn = NUMBER.findall(a), NUMBER.findall(b)
        numeric_ok = an == bn
        components["ordered_numbers"] = _component("pass" if numeric_ok else "fail",
            None if numeric_ok else "ordered_numeric_tokens_disagree", selected=an, independent=bn)
        ao, bo = _operators(text, policy), _operators(independent_text, policy)
        operator_ok = ao == bo
        components["ordered_operators"] = _component("pass" if operator_ok else "fail",
            None if operator_ok else "ordered_logical_operators_disagree", selected=ao, independent=bo)

    if row_layout_valid and not row_proof.get("baseline_independent"):
        metrics = row_proof["row_metrics"]
        components["words_and_order"] = _component("pass" if metrics["word_policy_pass"] else "fail",
            basis="independent_source_cell_words_and_order", word_recall=round(metrics["word_recall"], 5),
            word_precision=round(metrics["word_precision"], 5), word_order=round(metrics["word_order"], 5))
        components["ordered_numbers"] = _component("pass" if metrics["ordered_numbers_exact"] else "fail",
            basis="exact_ordered_numbers_in_every_source_cell", cells=metrics["cell_proofs"])
        components["ordered_operators"] = _component("pass" if metrics["ordered_operators_exact"] else "fail",
            basis="exact_ordered_operators_in_every_source_cell", cells=metrics["cell_proofs"])
    if not policy["require_exact_ordered_numbers"]:
        components["ordered_numbers"] = _component("unknown", "numeric_gate_disabled_by_policy")
    if not policy["require_exact_ordered_operators"]:
        components["ordered_operators"] = _component("unknown", "operator_gate_disabled_by_policy")

    if table_grid_valid:
        components["layout"] = _component("pass", tables=1,
            basis="source_bound_table_grid_and_independent_primary_ocr",
            rows=layout_proof["rows"], cells=layout_proof["populated_cells"],
            table_id=layout_proof["table_id"])
    elif row_layout_valid and table_count in {0, 1}:
        components["layout"] = _component("pass", tables=table_count,
            basis="source_bound_one_to_one_noncrossing_label_value_rows", rows=len(row_proof["geometry"]["rows"]))
    elif table_count == 0:
        if page.get("method") == "pdf_text" and len(text) < 80 and source_geometry and source_geometry.get("ready"):
            components["layout"] = _component("pass", tables=0, basis="short_native_text_only_source_coverage")
        elif source_geometry and source_geometry.get("method") == "source_raster_column_order_coverage" and components["source_render"]["state"] == "pass":
            components["layout"] = _component("pass", tables=0, basis="source_bound_nonoverlapping_column_dag_and_block_consensus")
        elif source_geometry and source_geometry.get("reading_order_proven") is True:
            components["layout"] = _component("pass", tables=0, basis="source_raster_line_order_and_decoration")
        else:
            components["layout"] = _component("unknown", "reading_order_columns_or_decoration_unproven", tables=0)
    elif not policy["require_row_column_proof_for_tables"]:
        components["layout"] = _component("unknown", "table_layout_gate_disabled_by_policy", tables=table_count)
    elif layout_proof is None:
        components["layout"] = _component("unknown", "table_row_column_proof_unavailable", tables=table_count)
    elif (layout_proof.get("source_pdf_sha256") != package_pdf_sha256 or
          layout_proof.get("physical_page") != page["page"] or
          layout_proof.get("page_text_sha256") != page["text_sha256"]):
        components["layout"] = _component("fail", "table_layout_provenance_mismatch")
    elif (layout_proof.get("kind") == "native_empty_form_grid" and
          table_count == 1 and page.get("method") == "pdf_text" and
          source_geometry and source_geometry.get("page_complete") is True and
          source_geometry.get("unaccounted_ink_pixels") == 0 and
          source_geometry.get("reading_order_proven") is True and
          layout_proof.get("source_render_sha256") == source_geometry.get("rendered_image_sha256") and
          layout_proof.get("empty_cells_source_checked") is True and
          layout_proof.get("all_table_glyphs_mapped") is True and
          layout_proof.get("semantic_cells") == 1 and
          isinstance(layout_proof.get("ordinal_placeholders"), int) and
          layout_proof["ordinal_placeholders"] >= 1):
        components["layout"] = _component("pass", tables=1, basis="source_bound_empty_form_grid",
                                           table_id=layout_proof.get("table_id"),
                                           table_cells_sha256=layout_proof.get("table_cells_sha256"))
    elif (layout_proof.get("row_column_proven") is True and
          layout_proof.get("numeric_values_exact") is True and
          layout_proof.get("source_render_coverage") is True):
        components["layout"] = _component("unknown", "caller_claim_is_not_independent_layout_proof", tables=table_count)
    else:
        components["layout"] = _component("fail", "table_cells_or_numeric_values_unproven", tables=table_count)

    required = ("text_content", "language", "source_render", "words_and_order",
                "ordered_numbers", "ordered_operators", "layout")
    missing = [name for name in required if components[name]["state"] == "unknown"]
    failed = [name for name in required if components[name]["state"] == "fail"]
    page_complete = bool(source_geometry and source_geometry.get("page_complete", True))
    if not page_complete:
        missing.append("complete_page_visual_accounting")
    return {"source_pdf_sha256": package_pdf_sha256, "page": page["page"],
            "page_text_sha256": page["text_sha256"],
            "page_complete": page_complete,
            "text_region_verified": not failed and all(name not in missing for name in required),
            "unresolved_visual_regions": (source_geometry or {}).get("unresolved_visual_regions", []),
            "source_style_regions": (source_geometry or {}).get("source_style_regions", []),
            "outcome": "repair_required" if missing or failed else "auto_accepted",
            "components": components, "missing_evidence": missing, "failed_components": failed,
            "acceptance_scope": "machine_verified_extraction" if not (missing or failed) else "none"}


def assess_package(package: Path, source_pdf: Path, *, pages: list[int] | None = None,
                   independent_by_page: dict[int, str] | None = None,
                   independent_metadata_by_page: dict[int, dict] | None = None,
                   source_geometry_by_page: dict[int, dict] | None = None,
                   layout_proof_by_page: dict[int, dict] | None = None,
                   visual_region_by_page: dict[int, dict] | None = None,
                   visual_layout_python: Path | None = None,
                   policy_path: Path | None = None,
                   derive_independent_native: bool = True,
                   derive_ocr_source_raster: bool = False) -> dict:
    """Verify one package/source once, then emit a bounded page-level receipt.

    The legacy ``derive_ocr_source_raster`` flag derives original-source raster
    evidence for both native and OCR pages. Explicit supplied proofs take priority.
    """
    package, source_pdf = Path(package), Path(source_pdf)
    policy = load_policy(policy_path)
    try:
        verify_package(package, source_pdf=source_pdf)
    except Exception as error:
        return {"schema_version": 1, "package_outcome": "source_blocked", "source_error": str(error)[:300],
                "pages_assessed": 0, "counts": {"auto_accepted": 0, "repair_required": 0, "source_blocked": 0},
                "packages_blocked": 1, "results": [], "human_review_receipts_created": 0}
    doc = json.loads((package / "evidence-package.json").read_text(encoding="utf-8"))["document"]
    raw_pages = [json.loads(line) for line in jsonl_physical_lines((package / "pages.jsonl").read_text(encoding="utf-8"))]
    selected = set(pages) if pages is not None else {page["page"] for page in raw_pages}
    if selected - {page["page"] for page in raw_pages}:
        raise ValueError("requested physical page is absent")
    tables = json.loads((package / "tables.json").read_text(encoding="utf-8"))
    table_counts = Counter(table["page"] for table in tables)
    independent_by_page = dict(independent_by_page or {})
    independent_metadata_by_page = dict(independent_metadata_by_page or {})
    layout_proof_by_page = dict(layout_proof_by_page or {})
    if derive_independent_native:
        from pypdf import PdfReader
        reader = PdfReader(source_pdf)
        import pdfplumber
        from .native_consensus import read_source_lines, choose_witness
        # PDF stream order can put a footer ahead of body text. Use the same
        # fresh, source-geometry-bound independent consensus as native ingestion;
        # never relax exact numbers/operators or source-raster completeness.
        with pdfplumber.open(source_pdf) as native:
            for page in raw_pages:
                if page["page"] in selected and page.get("method") == "pdf_text" and page["page"] not in independent_by_page:
                    physical=native.pages[page['page']-1]
                    try:
                        secondary=reader.pages[page['page']-1]
                        layout_text=secondary.extract_text(extraction_mode='layout',layout_mode_space_vertically=False)or ''
                        source_lines=read_source_lines(source_pdf,physical,page['page'])
                        witness,basis=choose_witness(page['text'],layout_text,source_lines)
                        independent_by_page[page['page']]=witness or ''
                        independent_metadata_by_page[page['page']]={
                            'engine':basis,'source_pdf_sha256':doc['pdf_sha256'],'page':page['page'],
                            'selected_page_text_sha256':page['text_sha256'],
                            'text_sha256':hashlib.sha256((witness or '').encode()).hexdigest(),
                            'basis':'fresh_native_reader_and_source_geometry_consensus'}
                    except Exception:
                        # A failed geometry read cannot authorize agreement.
                        try:independent_by_page[page['page']]=reader.pages[page['page']-1].extract_text()or ''
                        except Exception:pass
                    finally:physical.close()
    source_geometry_by_page = source_geometry_by_page or {}
    source_geometry_by_page = dict(source_geometry_by_page)
    # Inspect original fonts even when callers supply geometry or disable raster
    # derivation. Neither cached geometry nor two agreeing readers can bypass a
    # known legacy-encoding language route.
    native_font_by_page = {}
    if policy.get('native_font_families_requiring_raster_proof'):
        import pdfplumber
        from .text_script import native_font_script_evidence
        native_targets = [p for p in raw_pages if p['page'] in selected and p.get('method') == 'pdf_text']
        if native_targets:
            with pdfplumber.open(source_pdf) as original_fonts:
                for target in native_targets:
                    source_page = original_fonts.pages[target['page']-1]
                    native_font_by_page[target['page']] = native_font_script_evidence(source_page.chars, policy)
                    source_page.close()
    if derive_ocr_source_raster:
        from contextlib import ExitStack
        import pdfplumber
        import pypdfium2 as pdfium
        pending = [page for page in raw_pages if page["page"] in selected and
                   page["page"] not in source_geometry_by_page]
        with ExitStack() as stack:
            native = stack.enter_context(pdfplumber.open(source_pdf)) if any(
                page.get("method") == "pdf_text" for page in pending) else None
            original = stack.enter_context(pdfium.PdfDocument(source_pdf)) if any(
                page.get("method") != "pdf_text" for page in pending) else None
            for page in pending:
                if page.get("method") == "pdf_text":
                    native_page = native.pages[page["page"]-1]
                    source_geometry_by_page[page["page"]] = native_source_raster_evidence(
                        page, native_page, doc["pdf_sha256"], independent_by_page.get(page["page"]),
                        policy=policy, table_count=table_counts[page["page"]])
                    native_page.close()
                else:
                    source_page = original[page["page"]-1]
                    try:
                        prior_rows = page.get("label_value_proof")
                        prior_column = page.get("column_order_proof")
                        prior_table = page.get("table_grid_proof")
                        if prior_table:
                            from .table_grid import validate_table_grid_proof
                            if validate_table_grid_proof(page, prior_table, source_pdf):
                                source_geometry_by_page[page["page"]] = ocr_source_raster_evidence(
                                    page, source_page, doc["pdf_sha256"], policy=policy,
                                    table_count=table_counts[page["page"]])
                                layout_proof_by_page[page["page"]] = prior_table
                            else:
                                source_geometry_by_page[page["page"]] = {"ready": False,
                                    "page_text_sha256": page["text_sha256"],
                                    "reason": "stored_table_grid_failed_fresh_source_reproof"}
                        elif prior_rows:
                            from .row_layout import derive_label_value_layout
                            regenerated = derive_label_value_layout(page, prior_rows["layout_regions"], source_pdf,
                                cached_crops=prior_rows["independent_cells"],
                                recognition_dpi=prior_rows["recognition_dpi"],
                                baseline_independent=prior_rows.get("baseline_independent"))
                            if regenerated.get("ready"):
                                source_geometry_by_page[page["page"]] = regenerated
                                layout_proof_by_page[page["page"]] = regenerated
                                independent_by_page[page["page"]] = regenerated["independent_text"]
                                independent_metadata_by_page[page["page"]] = regenerated["independent_metadata"]
                            else:
                                source_geometry_by_page[page["page"]] = {**regenerated, "ready": False,
                                    "reading_order_proven": False, "reason": "stored_row_layout_failed_fresh_source_reproof"}
                        elif prior_column:
                            from .column_order import derive_column_order_repair
                            original_page = {**page, "text": prior_column["original_primary_text"],
                                "text_sha256": prior_column["original_page_text_sha256"],
                                "ocr": {**page["ocr"], "lines": prior_column["original_primary_lines"]}}
                            regenerated = derive_column_order_repair(original_page,
                                prior_column["original_independent_lines"], prior_column["layout_regions"],
                                source_pdf, independent_metadata=prior_column["original_independent_metadata"],
                                table_count=table_counts[page["page"]])
                            if regenerated.get("ready") and regenerated.get("page_text_sha256") == page["text_sha256"]:
                                source_geometry_by_page[page["page"]] = regenerated["proposed_page"]["column_order_proof"]
                                independent_by_page[page["page"]] = regenerated["independent_text"]
                                independent_metadata_by_page[page["page"]] = regenerated["independent_metadata"]
                            else:
                                source_geometry_by_page[page["page"]] = {**regenerated, "ready": False,
                                    "page_text_sha256": page["text_sha256"], "reading_order_proven": False,
                                    "reason": "stored_column_repair_failed_fresh_source_reproof"}
                        else:
                            source_geometry_by_page[page["page"]] = ocr_source_raster_evidence(
                                page, source_page, doc["pdf_sha256"], policy=policy,
                                table_count=table_counts[page["page"]])
                    finally:
                        source_page.close()
    if visual_region_by_page:
        from .visual_region_proof import fresh_docling_picture, reprove_footer_picture
        for page in raw_pages:
            if page["page"] not in selected or page["page"] not in visual_region_by_page:
                continue
            geometry = source_geometry_by_page.get(page["page"])
            if page.get("method") == "pdf_text" or not geometry:
                continue
            try:
                if visual_layout_python is None:
                    raise ValueError("fresh independent layout runtime required")
                fresh_region = fresh_docling_picture(package, source_pdf, page["page"],
                    visual_layout_python, visual_region_by_page[page["page"]],
                    candidate_text_sha256=page["text_sha256"])
                resolved, _ = reprove_footer_picture(page, source_pdf, geometry,
                    fresh_region)
                source_geometry_by_page[page["page"]] = resolved
            except Exception as error:
                source_geometry_by_page[page["page"]] = {
                    **geometry, "visual_region_reproof_error": str(error)[:200]}
    policy_sha256 = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    results = []
    for page in raw_pages:
        if page["page"] not in selected:
            continue
        result = assess_page(page, source_pdf_sha256=doc["pdf_sha256"], package_pdf_sha256=doc["pdf_sha256"],
                 independent_text=independent_by_page.get(page["page"]),
                 independent_metadata=independent_metadata_by_page.get(page["page"]),
                 source_geometry=source_geometry_by_page.get(page["page"]),
                 layout_proof=layout_proof_by_page.get(page["page"]),
                 table_count=table_counts[page["page"]], policy=policy,
                 source_pdf_path=source_pdf,
                 native_font_evidence=native_font_by_page.get(page['page']))
        result["policy_sha256"] = policy_sha256
        result["source_url"] = doc.get("source_url")
        result["independent_text_sha256"] = (hashlib.sha256(independent_by_page[page["page"]].encode()).hexdigest()
                                              if page["page"] in independent_by_page else None)
        result['independent_reader_basis']=independent_metadata_by_page.get(page['page'],{}).get('basis','caller_supplied_or_plain_native_fallback')
        results.append(result)
    outcomes = Counter(row["outcome"] for row in results)
    return {"schema_version": 1, "package_outcome": "assessed",
            "source_pdf_sha256": doc["pdf_sha256"], "source_url": doc.get("source_url"),
            "policy_sha256": policy_sha256,
            "pages_assessed": len(results),
            "counts": {key: outcomes[key] for key in ("auto_accepted", "repair_required", "source_blocked")},
            "results": results, "policy_schema_version": policy["schema_version"],
            "source_verified": True, "packages_blocked": 0, "human_review_receipts_created": 0}


def write_quality_report(report: dict, output: Path) -> Path:
    """Atomically persist the machine gate receipt, separate from review sidecars."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if report.get("schema_version") != 1 or report.get("human_review_receipts_created") != 0:
        raise ValueError("invalid machine quality report")
    temporary = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def write_quality_summary(reports: list[dict], output: Path, *, started_at: str | None = None) -> Path:
    """Aggregate exact machine outcomes without merging human review counts."""
    counts = Counter()
    reasons = Counter()
    for report in reports:
        counts.update(report.get("counts", {}))
        for row in report.get("results", []):
            if row.get("outcome") == "repair_required":
                reasons.update(row.get("failed_components", []) + row.get("missing_evidence", []))
    from .quality_routes import repair_patterns
    routing = repair_patterns([row for report in reports for row in report.get("results", [])])
    now = datetime.now(timezone.utc)
    summary = {"schema_version": 1, "updated_at": now.isoformat(),
               "started_at": started_at, "pages_assessed": sum(r.get("pages_assessed", 0) for r in reports),
               "auto_accepted": counts["auto_accepted"], "repair_required": counts["repair_required"],
               "source_blocked": counts["source_blocked"], "repair_reasons": dict(reasons),
               "packages_blocked": sum(r.get("packages_blocked", 0) for r in reports),
               "human_review_receipts_created": 0, "repair_patterns": routing}
    if started_at:
        seconds = (now - datetime.fromisoformat(started_at)).total_seconds()
        summary["processing_rate_pages_per_second"] = round(summary["pages_assessed"] / seconds, 3) if seconds > 0 else None
    return write_quality_report(summary, output)


def apply_machine_quality_decisions(pages: list[dict], pdf_sha: str, receipt_path: Path,
                                    source_url: str | None = None) -> tuple[list[dict], int]:
    """Apply only complete, source-bound machine accepts to verified page records.

    The caller must also verify the original evidence package and source PDF.
    This never creates a human review decision and does not rewrite OCR text.
    """
    receipt_path = Path(receipt_path)
    from .text_script import native_script_verification
    policy = load_policy()
    output = [dict(page) for page in pages]
    for page in output:
        gate = native_script_verification(page.get('text', ''), policy)
        if (page.get('method') == 'pdf_text' and gate['required_scripts'] and
                page.get('status') == 'ready' and page.get('review_decision') is None):
            page['status'] = 'review_required'
            page['script_verification'] = gate
    if not receipt_path.exists():
        return output, 0
    report = json.loads(receipt_path.read_text(encoding="utf-8"))
    policy_sha = hashlib.sha256(json.dumps(load_policy(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if (report.get("schema_version") != 1 or report.get("source_verified") is not True or
            report.get("source_pdf_sha256") != pdf_sha or report.get("policy_sha256") != policy_sha or
            report.get("human_review_receipts_created") != 0 or
            (source_url is not None and report.get("source_url") != source_url)):
        raise ValueError("machine quality receipt source or policy mismatch")
    by_page = {row["page"]: row for row in report.get("results", [])}
    if len(by_page) != len(report.get("results", [])):
        raise ValueError("duplicate physical page in machine quality receipt")
    known = {page.get("page") for page in output}
    if set(by_page) - known:
        raise ValueError("machine quality receipt references absent physical page")
    accepted = 0
    required = {"source_identity", "text_content", "language", "source_render",
                "words_and_order", "ordered_numbers", "ordered_operators", "layout"}
    for page in output:
        row = by_page.get(page.get("page"))
        if row is None or row.get("outcome") != "auto_accepted":
            continue
        # A source-reviewed decision has precedence, including an explicit
        # hold. Machine evidence must never overwrite reviewed text or blanks.
        if (page.get("status") not in {"review_required", "ready"} or
                page.get("review_decision") is not None or
                not page.get("text", "").strip()):
            continue
        components = row.get("components") or {}
        render = components.get("source_render") or {}
        if (row.get("source_pdf_sha256") != pdf_sha or row.get("policy_sha256") != policy_sha or
                row.get("page_text_sha256") != page.get("text_sha256") or
                page.get("text_sha256") != hashlib.sha256(page.get("text", "").encode()).hexdigest() or
                row.get("page_complete") is not True or row.get("unresolved_visual_regions") != [] or
                row.get("missing_evidence") != [] or row.get("failed_components") != [] or
                not required.issubset(components) or
                (page.get('method') == 'pdf_text' and
                 native_script_verification(page.get('text', ''), policy)['required_scripts'] and
                 components.get('language', {}).get('independent_model_confirmed') is not True) or
                any(components[name].get("state") != "pass" for name in required) or
                not row.get("independent_text_sha256") or not render.get("receipt_sha256") or
                not render.get("rendered_image_sha256")):
            raise ValueError(f"incomplete machine quality proof for physical page {page.get('page')}")
        page["status"] = "ready"
        page["machine_quality_accepted"] = True
        if page.get('method') == 'pdf_text' and native_script_verification(page.get('text', ''), policy)['required_scripts']:
            page['script_verification'] = {**native_script_verification(page['text'], policy),
                'state': 'machine_raster_text_verified', 'legal_review_approved': False}
        page["quality_policy_sha256"] = policy_sha
        page["quality_receipt_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        accepted += 1
    return output, accepted
