"""A ruled table on a scan tilted by a degree or two is still found: the grid is straightened by the page slope
the OCR baselines give before its rulings are looked for."""
import math

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
from PIL import Image

from local_vector_batch.ocr_tables import ruled_table_candidates


def _skewed_table(degrees):
    w, h = 1600, 1200
    img = np.full((h, w), 255, np.uint8)
    xs, ys = [200, 700, 1000], [300, 360, 420, 480]
    for y in ys:
        cv2.line(img, (xs[0], y), (xs[-1], y), 0, 2)
    for x in xs:
        cv2.line(img, (x, ys[0]), (x, ys[-1]), 0, 2)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -degrees, 1)
    img = cv2.warpAffine(img, m, (w, h), borderValue=255)
    cells = [["Item", "Rate"], ["Support", "120.00"], ["Training", "80.00"]]
    lines = []
    for r, row in enumerate(cells):
        for c, value in enumerate(row):
            cx, cy = (xs[c] + xs[c + 1]) / 2, (ys[r] + ys[r + 1]) / 2
            px, py = m @ np.array([cx, cy, 1.0])
            box = [(px - 40) / w, (py - 12) / h, (px + 40) / w, (py + 12) / h]
            lines.append({"text": value, "bbox": box, "words": [{"text": value, "bbox": box}]})
    slope = math.tan(math.radians(degrees)) * w / h      # normalised dy/dx, as the OCR lane reports it
    return Image.fromarray(img), lines, cells, slope


@pytest.mark.parametrize("degrees", [1.5, -1.5])
def test_skewed_ruled_table_is_found_with_its_cells(degrees):
    image, lines, cells, slope = _skewed_table(degrees)
    kw = dict(page=1, page_id="d:p1", pdf_width=576, pdf_height=432)
    assert ruled_table_candidates(image, lines, **kw) == [], "unstraightened, the tilted rulings are lost"
    found = ruled_table_candidates(image, lines, slope=slope, **kw)
    assert [t["cells"] for t in found] == [cells]
