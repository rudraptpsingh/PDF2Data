from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

from local_vector_batch.visual_region_proof import (footer_picture_receipt,
    fresh_docling_picture, reprove_footer_picture, verify_footer_picture_receipt)


def fixture(tmp_path):
    source = tmp_path / "source.pdf"; source.write_bytes(b"%PDF-test")
    image = tmp_path / "page.png"
    Image.new("RGB", (1000, 1000), "white").save(image)
    source_sha = sha256(source.read_bytes()).hexdigest()
    render_sha = sha256(image.read_bytes()).hexdigest()
    page = {"page": 2, "text_sha256": "a" * 64}
    region = {"id": "picture-1", "kind": "picture", "source_pdf_sha256": source_sha,
              "physical_page": 2, "page_text_sha256": page["text_sha256"],
              "rendered_image_sha256": render_sha,
              "bbox_normalized": [.45, .91, .6, .98]}
    geometry = {"source_pdf_sha256": source_sha, "page": 2,
                "page_text_sha256": page["text_sha256"],
                "unresolved_visual_regions": [{"location": "footer_visual",
                                               "bbox_normalized": [.47, .93, .55, .96],
                                               "ink_pixels": 50}]}
    return page, source, image, geometry, region


def test_picture_proof_is_hash_bound_and_never_publishes(tmp_path):
    page, source, image, geometry, region = fixture(tmp_path)
    receipt = footer_picture_receipt(page, source, image, geometry, region,
        qwen_label="handwritten_signature", rapid_text="", vision_text="")
    assert receipt["status"] == "candidate_nontext_signature"
    assert receipt["literal_signature_text"] is None
    assert receipt["publication_allowed"] is False
    assert verify_footer_picture_receipt(page, source, image, receipt)
    assert not verify_footer_picture_receipt({**page, "text_sha256": "b" * 64}, source, image, receipt)
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="raster changed"):
        footer_picture_receipt(page, source, image, geometry, region,
            qwen_label="handwritten_signature", rapid_text="", vision_text="")


def test_picture_proof_holds_text_body_and_unmatched_regions(tmp_path):
    page, source, image, geometry, region = fixture(tmp_path)
    assert footer_picture_receipt(page, source, image, geometry, region,
        qwen_label="handwritten_signature", rapid_text="12", vision_text="")['status'] == "held_unclassified"
    geometry['unresolved_visual_regions'][0]['location'] = 'body'
    with pytest.raises(ValueError, match="non-footer"):
        footer_picture_receipt(page, source, image, geometry, region,
            qwen_label="handwritten_signature", rapid_text="", vision_text="")
    geometry['unresolved_visual_regions'][0]['location'] = 'footer_visual'
    region['bbox_normalized'] = [.7, .91, .8, .98]
    with pytest.raises(ValueError, match="account"):
        footer_picture_receipt(page, source, image, geometry, region,
            qwen_label="handwritten_signature", rapid_text="", vision_text="")


def test_reproof_rejects_stale_or_unverified_layout_before_loading_models(tmp_path):
    page, source, _, geometry, region = fixture(tmp_path)
    region.update(candidate_engine="docling", route="visual_region_review",
                  candidate_text="", content_verified=False,
                  reading_order_verified=False)
    page["text_sha256"] = "changed"
    with pytest.raises(ValueError, match="stale"):
        reprove_footer_picture(page, source, geometry, region)
    page["text_sha256"] = "a" * 64
    region["candidate_text"] = "unread legal text"
    with pytest.raises(ValueError, match="provenance"):
        reprove_footer_picture(page, source, geometry, region)


def test_fresh_layout_rejects_changed_picture_geometry(tmp_path, monkeypatch):
    page, source, _, _, region = fixture(tmp_path)
    region.update(id="picture-1")
    package = tmp_path / "package"; package.mkdir()

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(args, **kwargs):
        output = Path(args[5]); output.mkdir(exist_ok=True)
        changed = {**region, "bbox_normalized": [.1, .91, .2, .98]}
        (output / "page-regions.jsonl").write_text(json.dumps(changed) + "\n")
        return Result()

    monkeypatch.setattr("local_vector_batch.visual_region_proof.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="changed"):
        fresh_docling_picture(package, source, 2, Path(sys.executable), region)
