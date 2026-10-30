import math

import pytest

from local_vector_batch.ocr import OCR_BASE_DPI, OCR_MAX_PIXELS, bounded_ocr_dpi


def test_normal_page_keeps_full_ocr_resolution():
    assert bounded_ocr_dpi(612, 792) == OCR_BASE_DPI


def test_large_drawing_is_bounded_before_rendering():
    width, height = 12_000, 8_000
    dpi = bounded_ocr_dpi(width, height)
    assert dpi < OCR_BASE_DPI
    assert math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72) <= OCR_MAX_PIXELS
    assert math.ceil(width * (dpi + 1) / 72) * math.ceil(height * (dpi + 1) / 72) > OCR_MAX_PIXELS


def test_impossible_render_is_explicit_error():
    with pytest.raises(ValueError, match="exceeds render budget"):
        bounded_ocr_dpi(500_000, 500_000)
