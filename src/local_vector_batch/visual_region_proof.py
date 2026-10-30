"""Source-bound, review-only proof for non-text visual regions.

This module never makes a page searchable. It can explain whether a footer
region is independently classified as a picture without inventing text.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import subprocess
import tempfile

from PIL import Image


def fresh_docling_picture(package: Path, source_pdf: Path, page: int,
                          python_executable: Path, expected: dict,
                          candidate_text_sha256: str | None = None) -> dict:
    """Rerun independent layout on the current package and exact source PDF."""
    python_executable = Path(python_executable)
    if not python_executable.is_file() or not python_executable.name.startswith("python"):
        raise ValueError("Docling Python executable unavailable")
    script = ("from local_vector_batch.pdf_compare import compare_pdf; "
              "from pathlib import Path; import sys; "
              "compare_pdf(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), "
              "pages=[int(sys.argv[4])], limit=1, layout_only=True, device='auto')")
    with tempfile.TemporaryDirectory(prefix="fresh-docling-picture-") as folder:
        process = subprocess.run([str(python_executable), "-c", script,
                                  str(package), str(source_pdf), folder, str(page)],
                                 capture_output=True, text=True, timeout=90)
        if process.returncode:
            raise ValueError("fresh independent layout failed: " + process.stderr[-200:])
        rows = [json.loads(line) for line in
                jsonl_physical_lines((Path(folder) / "page-regions.jsonl").read_text())]
        pictures = [row for row in rows if row.get("kind") == "picture" and
                    row.get("id") == expected.get("id")]
        if len(pictures) != 1:
            raise ValueError("picture region absent after fresh layout")
        fresh = pictures[0]
        for key in ("bbox_normalized", "rendered_image_sha256", "source_pdf_sha256",
                    "physical_page"):
            if fresh.get(key) != expected.get(key):
                raise ValueError("picture region changed after fresh layout")
        if fresh.get("page_text_sha256") != (candidate_text_sha256 or expected.get("page_text_sha256")):
            raise ValueError("picture region current page hash changed")
        return fresh


def footer_picture_receipt(page: dict, source_pdf: Path, source_raster: Path,
                           geometry: dict, picture_region: dict,
                           *, qwen_label: str, rapid_text: str,
                           vision_text: str) -> dict:
    """Bind a Docling picture to every unresolved footer ink component.

    Caller supplies independently measured reader outputs. This receipt is a
    candidate only; the full page remains held until the gate reruns readers.
    """
    source_pdf, source_raster = Path(source_pdf), Path(source_raster)
    source_sha = sha256(source_pdf.read_bytes()).hexdigest()
    if (geometry.get("source_pdf_sha256") != source_sha or
            geometry.get("page") != page.get("page") or
            geometry.get("page_text_sha256") != page.get("text_sha256") or
            picture_region.get("source_pdf_sha256") != source_sha or
            picture_region.get("physical_page") != page.get("page") or
            picture_region.get("page_text_sha256") != page.get("text_sha256") or
            picture_region.get("kind") != "picture"):
        raise ValueError("stale source or picture identity")
    unresolved = geometry.get("unresolved_visual_regions") or []
    if not unresolved or any(r.get("location") != "footer_visual" for r in unresolved):
        raise ValueError("non-footer or absent visual region")
    image_bytes = source_raster.read_bytes()
    if picture_region.get("rendered_image_sha256") != sha256(image_bytes).hexdigest():
        raise ValueError("source raster changed")
    with Image.open(source_raster) as opened:
        width, height = opened.size
        image = opened.copy()
    box = picture_region.get("bbox_normalized")
    if (not isinstance(box, list) or len(box) != 4 or
            not all(isinstance(v, (int, float)) for v in box) or
            not (0 <= box[0] < box[2] <= 1 and .85 <= box[1] < box[3] <= 1)):
        raise ValueError("invalid footer picture geometry")
    for region in unresolved:
        x0, y0, x1, y1 = region["bbox_normalized"]
        if not (box[0] - .015 <= x0 <= x1 <= box[2] + .015 and
                box[1] - .015 <= y0 <= y1 <= box[3] + .015):
            raise ValueError("picture does not account for all footer ink")
    crop_box = [max(0, int(box[0] * width) - 3), max(0, int(box[1] * height) - 3),
                min(width, int(box[2] * width) + 4), min(height, int(box[3] * height) + 4)]
    crop = image.crop(tuple(crop_box))
    buffer = BytesIO(); crop.save(buffer, format="PNG")
    # OCR silence supports a non-text candidate; it does not prove that a
    # signature's identity or date is known.
    status = ("candidate_nontext_signature" if qwen_label == "handwritten_signature"
              and not rapid_text.strip() and not vision_text.strip()
              else "held_unclassified")
    return {"kind": "source_bound_footer_picture", "status": status,
            "source_pdf_sha256": source_sha, "page": page["page"],
            "page_text_sha256": page["text_sha256"],
            "source_render_sha256": sha256(image_bytes).hexdigest(),
            "picture_region_id": picture_region["id"],
            "picture_bbox_normalized": box, "crop_bbox_pixels": crop_box,
            "crop_sha256": sha256(buffer.getvalue()).hexdigest(),
            "unresolved_regions": unresolved, "qwen_label": qwen_label,
            "rapid_text": rapid_text, "vision_text": vision_text,
            "classification_confidence": "corroborated_nontext_candidate" if status == "candidate_nontext_signature" else "insufficient",
            "independent_checks": {"docling_picture": True,
                                   "rapidocr_no_text": not rapid_text.strip(),
                                   "apple_vision_no_text": not vision_text.strip(),
                                   "qwen_signature": qwen_label == "handwritten_signature"},
            "literal_signature_text": None, "publication_allowed": False}


def verify_footer_picture_receipt(page: dict, source_pdf: Path,
                                  source_raster: Path, receipt: dict) -> bool:
    """Recheck immutable source/crop identity, without approving text or ink."""
    if (receipt.get("kind") != "source_bound_footer_picture" or
            receipt.get("publication_allowed") is not False or
            receipt.get("source_pdf_sha256") != sha256(Path(source_pdf).read_bytes()).hexdigest() or
            receipt.get("page") != page.get("page") or
            receipt.get("page_text_sha256") != page.get("text_sha256") or
            receipt.get("literal_signature_text") is not None):
        return False
    raster_bytes = Path(source_raster).read_bytes()
    if receipt.get("source_render_sha256") != sha256(raster_bytes).hexdigest():
        return False
    with Image.open(source_raster) as opened:
        image = opened.copy()
    box = receipt.get("crop_bbox_pixels")
    if (not isinstance(box, list) or len(box) != 4 or
            not all(isinstance(v, int) for v in box) or
            not (0 <= box[0] < box[2] <= image.width and
                 0 <= box[1] < box[3] <= image.height)):
        return False
    buffer = BytesIO(); image.crop(tuple(box)).save(buffer, format="PNG")
    return receipt.get("crop_sha256") == sha256(buffer.getvalue()).hexdigest()


def reprove_footer_picture(page: dict, source_pdf: Path, geometry: dict,
                           picture_region: dict) -> tuple[dict, dict]:
    """Rerender source and rerun two OCR readers plus a pinned visual model.

    The Docling layout region must be independently generated for this exact
    page version. Only an isolated footer picture can account for ink; no OCR
    text is silently discarded. A failed check leaves the page held.
    """
    import tempfile
    import pypdfium2 as pdfium
    from .ocr import _rapidocr_lines, vision_crop_lines
    from .region_ocr import MODEL, MODEL_REVISION

    if (picture_region.get("candidate_engine") != "docling" or
            picture_region.get("route") != "visual_region_review" or
            picture_region.get("candidate_text") != "" or
            picture_region.get("content_verified") is not False or
            picture_region.get("reading_order_verified") is not False):
        raise ValueError("independent layout candidate provenance missing")
    source_sha = sha256(Path(source_pdf).read_bytes()).hexdigest()
    if (picture_region.get("source_pdf_sha256") != source_sha or
            picture_region.get("physical_page") != page.get("page") or
            picture_region.get("page_text_sha256") != page.get("text_sha256") or
            geometry.get("source_pdf_sha256") != source_sha or
            geometry.get("page_text_sha256") != page.get("text_sha256")):
        raise ValueError("stale source or visual region proof")
    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        source_page = pdf[page["page"] - 1]
        try:
            raster = source_page.render(scale=120 / 72).to_pil().copy()
        finally:
            source_page.close()
    with tempfile.TemporaryDirectory(prefix="footer-picture-reproof-") as folder:
        raster_path = Path(folder) / "page.png"
        raster.save(raster_path)
        box = picture_region["bbox_normalized"]
        crop_box = [max(0, int(box[0] * raster.width) - 3),
                    max(0, int(box[1] * raster.height) - 3),
                    min(raster.width, int(box[2] * raster.width) + 4),
                    min(raster.height, int(box[3] * raster.height) + 4)]
        crop = raster.crop(tuple(crop_box))
        crop_path = Path(folder) / "crop.png"; crop.save(crop_path)
        rapid_rows = _rapidocr_lines(crop, "en")
        rapid_text = " ".join(row["text"] for row in rapid_rows[0] if row.get("confidence", 0) >= .5)
        vision_text = " ".join(row["text"] for row in vision_crop_lines(crop))
        from mlx_vlm import load, generate
        model, processor = load(MODEL, revision=MODEL_REVISION)
        prompt = processor.apply_chat_template([{"role": "user", "content": [
            {"type": "image"}, {"type": "text", "text":
                "Classify this crop as exactly one of: handwritten_signature, printed_text, table, diagram, blank_or_noise, uncertain. Return only the category."}
        ]}], tokenize=False, add_generation_prompt=True)
        label = generate(model, processor, prompt, image=str(crop_path),
                         max_tokens=24, verbose=False).text.strip()
        receipt = footer_picture_receipt(page, source_pdf, raster_path,
            geometry, picture_region, qwen_label=label,
            rapid_text=rapid_text, vision_text=vision_text)
    receipt["qwen_model"] = MODEL
    receipt["qwen_revision"] = MODEL_REVISION
    receipt["gate_reproved"] = True
    if receipt["status"] != "candidate_nontext_signature":
        raise ValueError("footer picture is not independently nontext")
    # Do not erase the evidence: retain the exact source region and its crop
    # hash as a classified nontext object in the geometry receipt.
    resolved = dict(geometry)
    resolved["unaccounted_ink_pixels"] = 0
    resolved["unresolved_visual_regions"] = []
    resolved["decorative_regions"] = list(geometry.get("decorative_regions", [])) + [
        {"classification": "source_proven_nontext_signature",
         "picture_region_id": receipt["picture_region_id"],
         "crop_sha256": receipt["crop_sha256"],
         "original_unresolved_regions": receipt["unresolved_regions"]}]
    resolved["page_complete"] = True
    resolved["reason"] = "source_ink_and_nontext_picture_accounted_for"
    resolved["visual_region_reproof"] = receipt
    return resolved, receipt
