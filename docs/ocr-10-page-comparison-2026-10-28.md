# OCR comparison: ten rendered regulatory pages

This is a small diagnostic sample, not a corpus accuracy estimate. The ten
images are E2E's fixed 300-DPI OCR fixtures: scanned letters, Hindi gazette
pages, rotated pages, and ruled tables. E2E's Tesseract result was replayed
from its recorded OCR passes because Tesseract is not installed on this Mac.
Vision and RapidOCR read the same fixture PNGs directly. Timing excludes PDF
rendering and, for E2E, cannot measure actual Tesseract inference.

I visually checked the listed numeric strings on each rendered image. A hit
requires the exact punctuation and digits, with a numeric boundary; spaces
may wrap. These targets test **recall only**. They cannot establish whether
an OCR engine invented other numbers or attached the right number to the
right table row.

| Fixture | Visible numeric targets | Vision | RapidOCR | E2E reader |
|---|---|---:|---:|---:|
| CERC 29891 p26, rotated table | 42, 45, 50 | 3/3 | 3/3 | 3/3 |
| KERC 88018 p46, tariff tables | 1,372.41; 2,932.00; 20,477.54; 1,794.00 | 4/4 | 4/4 | 4/4 |
| MoP 40999 p16, technical table | 765; 67; 56; 400 | 4/4 | 4/4 | 4/4 |
| MoP 40999 p1, letter | 3/4/2016; 06.10.2026; 110 001 | 3/3 | 3/3 | 3/3 |
| Grid India 103002 p1, bilingual letter | 110 066; 011-26732203; 2025 | 3/3 | 3/3 | 3/3 |
| CERC 581 p21, Hindi gazette | 30; 2003; 36; 2009 | 4/4 | 0/4 | 4/4 |
| CERC 2473 p25, allocation table | 8.5; 501.6; 1070.25; 25.08% | 4/4 | 4/4 | 4/4 |
| CERC 595 p67, rotated Hindi table | printed page number 67 | 0/1 | 0/1 | 0/1 |
| CEA 33743 p28, planning table | 27700; 13.7; 27.7; 13.85 | 4/4 | 4/4 | 4/4 |
| CERC 1752 p1, letter | 600 002; 110029; 80%; 2020 | 4/4 | 4/4 | 4/4 |
| **Total** | **34 targets** | **33/34** | **29/34** | **33/34** |

Vision's median recognition time was 0.41 seconds/image and RapidOCR's was
1.6 seconds/image after model warmup on this Mac. RapidOCR read zero
Devanagari characters on the four Hindi/mixed fixtures; Vision and E2E read
them. E2E's cell reconstruction emitted 45, 55, 144, 43 and 36 pipe cell
separators on five table pages. Vision and RapidOCR emitted essentially none.
This is the main quality gap: flat OCR text can contain every selected number
while losing its row and column meaning.

One exact source PDF SHA was also present in our Cloudflare backfill: CERC
581 p21. Its local Vision page held 2,122 characters and 1,659 Devanagari
characters; E2E's reader held 2,122 and 1,615. Word recall was 94.1%, but
their numeric sequences disagreed. A 300-DPI Vision reread also disagreed,
so simply increasing DPI is not a verified fix.

**Decision:** keep native extraction first and Vision as the current Mac OCR
reader. RapidOCR is useful as an independent disagreement signal, not as a
replacement or an automatic certifier. Keep scanned-table numeric facts and
clause links in source review until a table-aware parser is tested against
page images and row/column gold data. E2E's orientation, Hindi, and cell
reconstruction provide concrete patterns to evaluate, but its own recorded
audit also documents lost figures and clause labels; its text is not ground
truth. The generic 12-million-pixel render cap now bounds oversized pages and
records the actual DPI without changing review status.
