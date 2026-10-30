"""Source-bound OCR candidates for ink missed outside recorded text lines.

This only proposes repairs. The caller must run the whole-page quality gate
and compare-and-swap publisher before making any text searchable.
"""
from __future__ import annotations
from .jsonl import jsonl_physical_lines

from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from pathlib import Path
import json
import re
import unicodedata
import difflib

import pypdfium2 as pdfium
from PIL import Image

from .evidence_package import verify_package
from .ocr import _rapidocr_lines, _rapidocr_model_sha256, vision_crop_lines
from .quality_gate import ocr_source_raster_evidence


MODEL = "mlx-community/Qwen3-VL-8B-Instruct-4bit"
MODEL_REVISION = "defcdea7cc7a4b0858fea563cbbce171d328e457"
_MODEL_CACHE = None
PADDLE_MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PADDLE_REVISION = "3404726b40ac30b6e4e3d37c504e3f96c9c6c984"
_PADDLE_CACHE = None


def _canonical_quotes(value: str) -> str:
    """Compare OCR typography without changing source text or other punctuation."""
    return value.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))


def load_region_policy() -> tuple[dict, str]:
    raw = files("local_vector_batch").joinpath("region_ocr_policy.json").read_bytes()
    policy = json.loads(raw)
    if (policy.get("schema_version") != 1 or
            not 0 < policy.get("three_reader_min_rapid_confidence", 0) < policy.get("two_reader_min_rapid_confidence", 0) <= 1 or
            not 0 < policy.get("three_reader_min_vision_confidence", 0) <= 1 or
            not isinstance(policy.get("short_label_pattern"), str) or
            policy.get("require_literal_casefold_agreement") is not True or
            policy.get("forbid_digits_in_short_label") is not True or
            type(policy.get("composite_requires_exact_words")) is not bool):
        raise ValueError("invalid source-region OCR policy")
    re.compile(policy["short_label_pattern"])
    return policy, sha256(raw).hexdigest()


def require_composite_word_agreement(selected: str, independent: str) -> None:
    """Do not approve a composed repair with an unresolved OCR word change."""
    from .quality_gate import WORD, _normalized
    policy, _ = load_region_policy()
    if (policy["composite_requires_exact_words"] and
            WORD.findall(_normalized(selected)) != WORD.findall(_normalized(independent))):
        raise ValueError("composite_source_ocr_words_disagree")


def short_label_crop(image, line):
    """Keep a structural label at source resolution, excluding adjacent prose."""
    x0, _, x1, _ = line["bbox"]
    left = max(0, int(x0 * image.width) - 10)
    right = min(image.width, int(x1 * image.width) + 10)
    box = [left, 0, right, image.height]
    return image.crop(tuple(box)), box


def agreed_label_literal(rapid: str, vision: str, vlm: str) -> str | None:
    """Preserve the case printed by two exact readers, never infer a value."""
    labels = [rapid.strip(), vision.strip()]
    labels.extend(re.findall(r"(?<!\w)[A-Za-z]{1,3}[.)](?!\w)", vlm))
    if len(labels) < 3 or labels[0].casefold() != labels[1].casefold():
        return None
    for label in (labels[0], labels[1]):
        if labels.count(label) >= 2:
            return label
    return None


def linked_footnote_marker(body_vlm: str, note_vlm: str, note_rapid: str,
                           independent_page: str) -> str | None:
    """Require the same literal marker at the body anchor and footnote.

    This is a candidate link only. It cannot promote an OCR page or infer a
    handwritten number; the complete page still needs its quality gate.
    """
    normalized = [unicodedata.normalize("NFKC", value.strip())
                  for value in (body_vlm, note_vlm, note_rapid, independent_page)]
    anchor = re.search(r"[\].)][\s]*([0-9]{1,2})\s*[:;.]?\s*$", normalized[0])
    note_a = re.match(r"^([0-9]{1,2})\b", normalized[1])
    note_b = re.match(r"^([0-9]{1,2})\b", normalized[2])
    if not all((anchor, note_a, note_b)):
        return None
    marker = anchor.group(1)
    if marker != note_a.group(1) or marker != note_b.group(1):
        return None
    if not re.search(r"[\].)]\s*" + re.escape(marker) + r"\b", normalized[3]):
        return None
    return marker


def _missing_terminal_marker(selected: str, independent: str) -> str | None:
    """Find one independently read superscript without changing body prose.

    This nominates a crop only. The source readers and whole-page gate still
    decide whether the marker and its lower-page note are actually present.
    """
    selected, independent = selected.strip(), independent.strip()
    match = re.search(r"(?<=[\].)])([0-9]{1,2})(?=[:;.]?$)", independent)
    if match and independent[:match.start()] + independent[match.end():] == selected:
        return match.group(1)
    # A closing bracket and its superscript can both disappear after a period.
    match = re.search(r"\]([0-9]{1,2})$", independent)
    if match and independent[:match.start()] == selected:
        return match.group(1)
    return None


def candidate_footnote_pairs(page: dict, independent: str | None = None) -> list[tuple[int, int]]:
    """Nominate body anchors and lower-page notes for bounded source proof.

    An optional, separately verified OCR reading can nominate markers missing
    entirely from the selected reading. It cannot nominate changed body words.
    """
    lines = page.get("ocr", {}).get("lines", [])
    bodies = [i for i, line in enumerate(lines)
              if line["bbox"][1] < .93 and
              ((len(line["text"].strip()) > 10 and
                (re.search(r"[\].)][A-Za-z0-9°'$]{1,3}$", line["text"]) or
                 re.search(r"\]$", line["text"]))) or
               re.fullmatch(r"\s*\[\s*\][\w°$]{1,3}\s*", line["text"]))]
    notes = [i for i, line in enumerate(lines)
             if line["bbox"][1] >= .8 and
             re.match(r"^\s*(?:[0-9]{1,3}|[$*†]|[sSoO]{1,2}[*°]?)\s+"
                      r"(?:Added|Replaced|Modified|Substituted|Deleted|First|Second|Third)\b", line["text"], re.I) and
             len(line["text"].strip()) > 10]
    if independent is not None:
        independent_lines = independent.splitlines()
        for i, line in enumerate(lines):
            if (i not in bodies and line["bbox"][1] < .8 and
                    len(line["text"].strip()) > 10 and
                    any(_missing_terminal_marker(line["text"], other)
                        for other in independent_lines)):
                bodies.append(i)
    return [(body, note) for body in sorted(bodies) for note in notes if body < note]


def corrected_footnote_text(page: dict, independent: str,
                            links: list[dict]) -> tuple[list[dict], str]:
    """Compose literal linked markers without changing unrelated OCR lines."""
    lines = [dict(line) for line in page["ocr"]["lines"]]
    if len({(link["body_line_index"], link["note_line_index"]) for link in links}) != len(links):
        raise ValueError("duplicate footnote pair")
    touched: set[int] = set()
    independent_lines = independent.splitlines()
    for link in links:
        body_i, note_i, marker = (link["body_line_index"],
                                  link["note_line_index"], link["marker"])
        if ((body_i, note_i) not in candidate_footnote_pairs(page, independent) or
                body_i in touched or note_i in touched or
                not isinstance(marker, str) or not re.fullmatch(r"[0-9]{1,2}", marker)):
            raise ValueError("footnote roles overlap or are invalid")
        touched.update((body_i, note_i))
        body_old, note_old = lines[body_i]["text"], lines[note_i]["text"]
        if (body_old != link["body_selected_text"] or
                note_old != link["note_selected_text"]):
            raise ValueError("selected OCR anchor text changed")
        standalone = bool(re.fullmatch(r"\s*\[\s*\][\w°$]{1,3}\s*", body_old))
        if standalone:
            body_matches = [line for line in independent_lines
                            if re.fullmatch(r"\s*\[\s*\]" + re.escape(marker) + r"\s*", line)]
        elif body_old.endswith("]"):
            # A tiny printed superscript can be absent from selected OCR.
            # The source-crop pair and independent full page must supply the
            # same exact marker immediately after this complete body line.
            body_matches = [line for line in independent_lines
                            if line == body_old + marker]
        elif any(_missing_terminal_marker(body_old, line) == marker
                 for line in independent_lines):
            body_matches = [line for line in independent_lines
                            if _missing_terminal_marker(body_old, line) == marker]
        else:
            prefix = re.sub(r"[\].)][A-Za-z0-9°'$]{1,3}$", "", body_old).strip()
            if not prefix or prefix == body_old:
                raise ValueError("footnote body lacks a terminal marker anchor")
            body_matches = [line for line in independent_lines
                            if line.startswith(prefix) and
                            re.search(r"[\].)]\s*" + re.escape(marker) + r"\s*$", line)]
        if len(body_matches) != 1:
            raise ValueError("independent body anchor absent or ambiguous")
        body_new = body_matches[0]
        note_tail = re.sub(r"^\s*(?:[0-9]{1,3}|[$*†]|[sSoO]{1,2}[*°]?)\s*",
                           "", note_old, count=1).strip()
        if not note_tail:
            raise ValueError("footnote text absent after marker")
        exact_note = [i for i, line in enumerate(independent_lines)
                      if line.strip() == f"{marker} {note_tail}"]
        # Repeated amendment text can occur under several numbered notes;
        # prefer the exact matching marker before considering a split label.
        split_note = [i for i, line in enumerate(independent_lines)
                      if line.strip() == note_tail and i > 0 and
                      independent_lines[i - 1].strip() == marker]
        bare_note = [i for i, line in enumerate(independent_lines)
                     if line.strip() == note_tail]
        note_matches = exact_note or split_note or bare_note
        if len(note_matches) != 1:
            raise ValueError("independent footnote text absent or ambiguous")
        note_new = f"{marker} {note_tail}"
        lines[body_i]["text"] = body_new
        lines[note_i]["text"] = note_new
        note_at = note_matches[0]
        # OCR may split the printed note marker into its own row. The source
        # crop links one marker to one note, so merge that exact adjacent row
        # instead of manufacturing a second numeric marker.
        if (independent_lines[note_at].strip() == note_tail and note_at > 0 and
                independent_lines[note_at - 1].strip() == marker):
            independent_lines.pop(note_at - 1)
            note_at -= 1
        independent_lines[note_at] = note_new
    return lines, "\n".join(independent_lines)


def source_bound_line_words(page: dict, source_pdf: Path, line_index: int,
                            independent: str, *, dpi: int = 300,
                            horizontal_padding: float = .005) -> dict:
    """Prove changed OCR words on one printed source line; never approve a page.

    Keep the selected punctuation/layout and replace only nonnumeric words that
    both a full-page independent reader and two source-crop readers recover.
    One crop reader may split words, so it must contain each changed word but
    the VLM must preserve the complete proposed word order.
    """
    from .quality_gate import WORD, _normalized
    if (not 120 <= dpi <= 360 or not 0 <= horizontal_padding <= .02 or
            not 0 <= line_index < len(page["ocr"]["lines"])):
        raise ValueError("invalid source line")
    old = page["ocr"]["lines"][line_index]["text"]
    old_words = list(WORD.finditer(old))
    independent_lines = independent.splitlines()
    spans = [(line, (line,)) for line in independent_lines]
    # Full-page OCR sometimes puts a clause marker on its own line while the
    # source and selected reader keep it with the following body line.
    for first, second in zip(independent_lines, independent_lines[1:]):
        if (re.fullmatch(r"\(?\d{1,3}\)?[.)]?", first.strip()) and
                old.startswith(first.strip() + " ")):
            spans.append((first.strip() + " " + second, (first, second)))
    candidates = [(line, parts) for line, parts in spans
                  if len(WORD.findall(_normalized(line))) == len(old_words)
                  and len(WORD.findall(_normalized(old))) == len(old_words)
                  and sum(a.casefold() != b.casefold() for a, b in zip(
                      WORD.findall(_normalized(old)), WORD.findall(_normalized(line)))) >= 1]
    # A long source line must have enough matching context to locate it uniquely.
    candidates = [(line, parts) for line, parts in candidates if difflib.SequenceMatcher(
        a=[m.group().casefold() for m in old_words],
        b=[m.group().casefold() for m in WORD.finditer(line)], autojunk=False).ratio() >= .7]
    if len(candidates) != 1:
        raise ValueError("independent source line is absent or ambiguous")
    target, independent_span = candidates[0]
    target_words = list(WORD.finditer(target))
    if len(target_words) != len(old_words):
        raise ValueError("source line tokenization changed")
    changed = [(a.group(), b.group()) for a, b in zip(old_words, target_words)
               if a.group().casefold() != b.group().casefold()]
    operators = {"and", "or", "not", "shall", "may", "must", "if", "except", "unless"}
    if not changed or any(not a.isalpha() or not b.isalpha() or
                          a.casefold() in operators or b.casefold() in operators
                          for a, b in changed):
        raise ValueError("numeric or logical correction needs separate proof")
    revised = old
    for a, b in reversed(list(zip(old_words, target_words))):
        if a.group().casefold() != b.group().casefold():
            revised = revised[:a.start()] + b.group() + revised[a.end():]
    import pypdfium2 as pdfium
    source_pdf = Path(source_pdf)
    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        source_page = pdf[page["page"] - 1]
        try:
            rendered = source_page.render(scale=dpi / 72).to_pil().copy()
        finally:
            source_page.close()
    b = page["ocr"]["lines"][line_index]["bbox"]
    box = [max(0, int((b[0]-horizontal_padding)*rendered.width)),
           max(0, int((b[1]-.01)*rendered.height)),
           min(rendered.width, int((b[2]+horizontal_padding)*rendered.width)),
           min(rendered.height, int((b[3]+.01)*rendered.height))]
    crop = rendered.crop(tuple(box))
    buffer = BytesIO()
    crop.save(buffer, format="PNG")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="source-line-proof-") as folder:
        crop_path = Path(folder) / "crop.png"
        crop_path.write_bytes(buffer.getvalue())
        global _MODEL_CACHE
        if _MODEL_CACHE is None:
            from mlx_vlm import load
            _MODEL_CACHE = load(MODEL, revision=MODEL_REVISION)
        vlm = _vlm_transcribe(*_MODEL_CACHE, crop_path)
    rapid = _rapidocr_lines(crop, "en")
    vision = " ".join(row["text"] for row in vision_crop_lines(crop))
    rapid_words = [w.casefold() for row in (rapid[0] if rapid else [])
                   for w in WORD.findall(_normalized(row["text"]))]
    vlm_words = [w.casefold() for w in WORD.findall(_normalized(vlm))]
    expected = [w.casefold() for w in WORD.findall(_normalized(target))]
    if (vlm_words != expected or vision != revised or
            any(word.casefold() not in rapid_words for _, word in changed)):
        raise ValueError("source crop readers do not corroborate changed words")
    proof = {"kind": "source_bound_line_words", "source_pdf_sha256": sha256(source_pdf.read_bytes()).hexdigest(),
            "page": page["page"], "original_page_text_sha256": page["text_sha256"],
            "line_index": line_index, "old_line": old, "proposed_line": revised,
            "independent_line": target,
            "crop_bbox_pixels": box,
            "crop_sha256": sha256(buffer.getvalue()).hexdigest(), "crop_dpi": dpi,
            "crop_horizontal_padding": horizontal_padding,
            "rapid_words": rapid_words, "vision_text": vision, "vlm_text": vlm,
            "vlm_model": MODEL, "vlm_revision": MODEL_REVISION,
            "publication_allowed": False}
    if len(independent_span) > 1:
        proof["independent_span"] = list(independent_span)
    return proof


def compose_source_bound_word_line(independent: str, proof: dict) -> str:
    """Replace one uniquely aligned OCR line, including a split clause label."""
    span = proof.get("independent_span", [proof["independent_line"]])
    if not isinstance(span, list) or not 1 <= len(span) <= 2 or any(
            not isinstance(line, str) or not line for line in span):
        raise ValueError("invalid independent line span")
    lines = independent.splitlines()
    matches = [i for i in range(len(lines) - len(span) + 1)
               if lines[i:i + len(span)] == span]
    if len(matches) != 1:
        raise ValueError("independent source line changed or is ambiguous")
    i = matches[0]
    lines[i:i + len(span)] = [proof["proposed_line"]]
    return "\n".join(lines)


def source_bound_complete_line(page: dict, source_pdf: Path, line_index: int,
                               independent: str, *, dpi: int = 300,
                               vertical_padding: float = .01,
                               horizontal_padding: float = .01) -> dict:
    """Prove one complete printed line, including an omitted independent line.

    All three crop readers must transcribe the *whole* line. The independent
    full-page location must be unique, with exact neighboring selected lines
    when the line was omitted. This is a region proof, never a page approval.
    """
    if (not 120 <= dpi <= 360 or not 0 <= vertical_padding <= .01 or
            not 0 <= horizontal_padding <= .01 or
            not 0 < line_index < len(page["ocr"]["lines"])):
        raise ValueError("invalid complete source line")
    lines = page["ocr"]["lines"]
    old = lines[line_index]["text"]
    before = lines[line_index - 1]["text"]
    after = lines[line_index + 1]["text"] if line_index + 1 < len(lines) else ""
    source_pdf = Path(source_pdf)
    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        source_page = pdf[page["page"] - 1]
        try:
            rendered = source_page.render(scale=dpi / 72).to_pil().copy()
        finally:
            source_page.close()
    b = lines[line_index]["bbox"]
    box = [max(0, int((b[0] - horizontal_padding) * rendered.width)),
           max(0, int((b[1] - vertical_padding) * rendered.height)),
           min(rendered.width, int((b[2] + horizontal_padding) * rendered.width)),
           min(rendered.height, int((b[3] + vertical_padding) * rendered.height))]
    crop = rendered.crop(tuple(box))
    buffer = BytesIO()
    crop.save(buffer, format="PNG")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="complete-line-proof-") as folder:
        crop_path = Path(folder) / "crop.png"
        crop_path.write_bytes(buffer.getvalue())
        global _MODEL_CACHE
        if _MODEL_CACHE is None:
            from mlx_vlm import load
            _MODEL_CACHE = load(MODEL, revision=MODEL_REVISION)
        vlm = _vlm_transcribe(*_MODEL_CACHE, crop_path).strip()
    vision = " ".join(row["text"] for row in vision_crop_lines(crop)).strip()
    rapid_rows = _rapidocr_lines(crop, "en")
    rapid = " ".join(row["text"] for row in sorted(
        rapid_rows[0] if rapid_rows else [], key=lambda row: row["bbox"][0])).strip()
    enumerator_basis = None
    target = vision
    # Preserve Vision's literal transcription; quote equivalence is proof-only.
    if (not vision or _canonical_quotes(vision) != _canonical_quotes(vlm) or
            _canonical_quotes(vision).casefold() != _canonical_quotes(rapid).casefold() or
            min((row["confidence"] for row in rapid_rows[0]), default=0) < .8):
        # Tiny clause labels are often confused with letters or digits by
        # one reader. Require two distinct VLMs and the independent full-page
        # reading with the *following source line*, never sequence inference.
        global _PADDLE_CACHE
        if _PADDLE_CACHE is None:
            from mlx_vlm import load
            _PADDLE_CACHE = load(PADDLE_MODEL, revision=PADDLE_REVISION)
        with tempfile.TemporaryDirectory(prefix="roman-label-proof-") as folder:
            paddle_path = Path(folder) / "crop.png"
            paddle_path.write_bytes(buffer.getvalue())
            paddle = _vlm_transcribe(*_PADDLE_CACHE, paddle_path).strip()
        independent_lines = independent.splitlines()
        # A cropped OCR reader may lose only the tiny first marker. The
        # remaining complete line must still agree exactly, while two VLMs,
        # Vision and the full-page independent reading agree on that marker.
        if (rapid == vlm and " ".join(paddle.split()) == vlm and
                independent_lines.count(vlm) == 1 and
                len(vlm.split()) >= 8 and
                min((row["confidence"] for row in rapid_rows[0]), default=0) >= .8):
            enumerator_basis = paddle
            target = vlm
        elif (vision == vlm and " ".join(paddle.split()) == vlm and
                independent_lines.count(vlm) == 1 and
                rapid.split()[1:] == vlm.split()[1:] and
                len(vlm.split()) >= 8):
            enumerator_basis = paddle
            target = vlm
        else:
            if not re.fullmatch(r"(?:[ivxlcdm]{1,5}|\(?\d{1,3})[.)]", vlm):
                raise ValueError("complete source line lacks three-reader agreement")
            standalone = [i for i, line in enumerate(independent_lines[:-1])
                          if line == vlm and independent_lines[i + 1] == after]
            prefixed = independent_lines.count(vlm + " " + after) == 1
            if (paddle != vlm or not after or
                    not (len(standalone) == 1 or prefixed) or
                    (vlm[0].isdigit() or vlm[0] == "(") and rapid != vlm):
                raise ValueError("source label lacks independent full-span proof")
            enumerator_basis = paddle
            target = vlm
    independent_lines = independent.splitlines()
    matches = [i for i, value in enumerate(independent_lines) if value == target]
    if len(matches) == 1:
        mode, anchor = "replace", matches[0]
    elif (enumerator_basis is not None and after and
          independent_lines.count(target + " " + after) == 1):
        mode, anchor = "prefix", independent_lines.index(target + " " + after)
    elif not matches:
        # Printed folios can be corrupted in the full-page independent OCR.
        # A tiny, isolated last line in the bottom margin can be corrected
        # only when all three source-crop readers transcribe the same digits.
        if (line_index == len(lines) - 1 and b[1] >= .9 and
                re.fullmatch(r"\d{1,4}", old) and target == old and
                independent_lines and len(independent_lines[-1].split()) == 1 and
                independent_lines[-1] != target and
                rapid == target and vlm == target and vision == target and
                min((row["confidence"] for row in rapid_rows[0]), default=0) >= .8):
            mode, anchor = "footer_replace", len(independent_lines) - 1
        else:
            pairs = _missing_line_anchors(independent_lines, before, after)
            if len(pairs) != 1 or old != target:
                raise ValueError("omitted source line lacks exact unique neighbors")
            mode, anchor = "insert", pairs[0]
    else:
        raise ValueError("complete source line is ambiguous")
    if old == target and mode not in {"insert", "footer_replace"}:
        raise ValueError("complete source line makes no correction")
    proof = {"kind": "source_bound_complete_line", "source_pdf_sha256": sha256(source_pdf.read_bytes()).hexdigest(),
            "page": page["page"], "original_page_text_sha256": page["text_sha256"],
            "line_index": line_index, "old_line": old, "proposed_line": target,
            "neighbor_before": before, "neighbor_after": after,
            "independent_mode": mode, "independent_anchor": anchor,
            "crop_bbox_pixels": box, "crop_sha256": sha256(buffer.getvalue()).hexdigest(),
            "crop_dpi": dpi, "rapid_text": rapid, "vision_text": vision,
            "vlm_text": vlm, "vlm_model": MODEL, "vlm_revision": MODEL_REVISION,
            "publication_allowed": False}
    if vertical_padding != .01:
        proof["crop_vertical_padding"] = vertical_padding
    if horizontal_padding != .01:
        proof["crop_horizontal_padding"] = horizontal_padding
    if enumerator_basis is not None:
        proof.update({"reader_basis": "dual_vlm_full_independent_enumerator",
                      "paddle_text": enumerator_basis, "paddle_model": PADDLE_MODEL,
                      "paddle_revision": PADDLE_REVISION})
    return proof


def _missing_line_anchors(independent_lines: list[str], before: str,
                          after: str) -> list[int]:
    """Locate a complete omitted line using two long, exact surrounding spans."""
    from .quality_gate import WORD
    if len(WORD.findall(before)) < 7 or len(WORD.findall(after)) < 3:
        return []
    return [i for i in range(len(independent_lines) - 1)
            if independent_lines[i + 1] == after and
            any(" ".join(independent_lines[i - width + 1:i + 1]).split() == before.split()
                for width in (1, 2) if i - width + 1 >= 0)]


def compose_complete_line_proof(lines: list[dict], independent: str,
                                proof: dict) -> tuple[list[dict], str]:
    """Apply only an already revalidated complete-line proof to a composition."""
    index = proof["line_index"]
    if lines[index]["text"] != proof["old_line"]:
        raise ValueError("complete source line changed")
    independent_lines = independent.splitlines()
    if proof["independent_mode"] == "replace":
        matches = [i for i, value in enumerate(independent_lines)
                   if value == proof["proposed_line"]]
        if len(matches) != 1:
            raise ValueError("independent complete line changed")
    elif proof["independent_mode"] == "prefix":
        matches = [i for i, value in enumerate(independent_lines)
                   if value == proof["proposed_line"] + " " + proof["neighbor_after"]]
        if len(matches) != 1:
            raise ValueError("independent clause prefix changed")
    elif proof["independent_mode"] == "insert":
        matches = _missing_line_anchors(independent_lines,
            proof["neighbor_before"], proof["neighbor_after"])
        if len(matches) != 1:
            raise ValueError("omission neighbors changed")
        independent_lines.insert(matches[0] + 1, proof["proposed_line"])
    elif proof["independent_mode"] == "footer_replace":
        if (index != len(lines) - 1 or not independent_lines or
                len(independent_lines) - 1 != proof["independent_anchor"] or
                lines[index]["bbox"][1] < .9 or
                not re.fullmatch(r"\d{1,4}", proof["proposed_line"]) or
                independent_lines[-1] == proof["proposed_line"] or
                len(independent_lines[-1].split()) != 1):
            raise ValueError("footer source anchor changed")
        independent_lines[-1] = proof["proposed_line"]
    else:
        raise ValueError("invalid complete-line operation")
    lines[index]["text"] = proof["proposed_line"]
    return lines, "\n".join(independent_lines)


def propose_footnote_marker_link(package: Path, source_pdf: Path, page_number: int,
                                 independent_receipt: dict, output: Path,
                                 body_line_index: int, note_line_index: int,
                                 *, dpi: int = 300) -> dict:
    """Make a source-bound, review-only anchor to a printed footnote marker."""
    from .repair_pipeline import _bound_independent_text

    package, source_pdf, output = Path(package), Path(source_pdf), Path(output)
    verify_package(package, source_pdf=source_pdf)
    report = json.loads((package / "report.json").read_text())
    page = next(row for row in (json.loads(line) for line in
                jsonl_physical_lines((package / "pages.jsonl").read_text())) if row["page"] == page_number)
    lines = page.get("ocr", {}).get("lines", [])
    if (page.get("status") != "review_required" or page.get("review_decision") or
            page.get("method") not in {"vision", "rapidocr"} or
            not 120 <= dpi <= 360 or
            body_line_index == note_line_index or
            min(body_line_index, note_line_index) < 0 or
            max(body_line_index, note_line_index) >= len(lines)):
        raise ValueError("footnote proof needs a held OCR page and two distinct source lines")
    body, note = lines[body_line_index], lines[note_line_index]
    independent = _bound_independent_text(
        independent_receipt, source_pdf, source_sha=report["source_pdf_sha256"],
        page=page_number, original_text_sha=page["text_sha256"],
        candidate_engine=page.get("ocr", {}).get("engine", page["method"]))
    if (body_line_index, note_line_index) not in candidate_footnote_pairs(page, independent):
        raise ValueError("selected lines are not a body anchor and lower-page footnote")
    pdf = pdfium.PdfDocument(str(source_pdf))
    try:
        source_page = pdf[page_number - 1]
        try:
            rendered = source_page.render(scale=dpi / 72).to_pil()
        finally:
            source_page.close()
    finally:
        pdf.close()
    output.mkdir(parents=True, exist_ok=True)
    # Keep enough neighboring text to distinguish a superscript from ink or a
    # signature, while isolating the body endpoint and the note's left edge.
    regions = {
        "body": (max(0, body["bbox"][2] - .115), max(0, body["bbox"][1] - .012),
                 min(1, body["bbox"][2] + .055), min(1, body["bbox"][3] + .015)),
        "note": (max(0, note["bbox"][0] - .018), max(0, note["bbox"][1] - .012),
                 min(1, note["bbox"][0] + .18), min(1, note["bbox"][3] + .015)),
    }
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        from mlx_vlm import load
        _MODEL_CACHE = load(MODEL, revision=MODEL_REVISION)
    model, processor = _MODEL_CACHE
    proofs = {}
    for role, bounds in regions.items():
        box = [int(bounds[0] * rendered.width), int(bounds[1] * rendered.height),
               int(bounds[2] * rendered.width), int(bounds[3] * rendered.height)]
        crop = rendered.crop(tuple(box))
        png = BytesIO()
        crop.save(png, format="PNG")
        image_bytes = png.getvalue()
        image_sha = sha256(image_bytes).hexdigest()
        image_path = output / f"p{page_number}-{role}-{image_sha[:12]}.png"
        image_path.write_bytes(image_bytes)
        # Tiny printed note numerals are often lost at the native crop size.
        # Upsampling only the exact source crop helps both OCR readers see the
        # same ink without introducing any inferred character or new source.
        ocr_crop = crop.resize((crop.width * 2, crop.height * 2),
                               Image.Resampling.LANCZOS) if role == "note" else crop
        rapid = _rapidocr_lines(ocr_crop, "en")
        rapid_lines = rapid[0] if rapid else []
        vlm = _vlm_transcribe(model, processor, image_path)
        proofs[role] = {"source_image_path": str(image_path),
                        "source_image_sha256": image_sha, "bbox_pixels": box,
                        "rapidocr_lines": rapid_lines, "rapidocr_scale": 2 if role == "note" else 1,
                        "vlm_text": vlm}
    leftmost_note = min(proofs["note"]["rapidocr_lines"],
                        key=lambda line: line["bbox"][0], default={})
    marker = linked_footnote_marker(proofs["body"]["vlm_text"],
                                    proofs["note"]["vlm_text"],
                                    leftmost_note.get("text", ""), independent)
    receipt = {"schema_version": 1, "kind": "source_bound_footnote_marker_link",
               "status": "candidate" if marker else "held", "source_pdf": str(source_pdf),
               "source_pdf_sha256": report["source_pdf_sha256"], "page": page_number,
               "original_page_text_sha256": page["text_sha256"],
               "body_line_index": body_line_index, "note_line_index": note_line_index,
               "body_selected_text": body["text"], "note_selected_text": note["text"],
               "marker": marker, "reader_model": MODEL, "reader_revision": MODEL_REVISION,
               "independent_receipt_sha256": sha256(json.dumps(independent_receipt, sort_keys=True).encode()).hexdigest(),
               "independent_full_receipt": independent_receipt,
               "regions": proofs, "page_status_preserved": page["status"],
               "publication_allowed": False}
    path = output / f"p{page_number}-b{body_line_index}-n{note_line_index}-footnote-link.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {"status": receipt["status"], "marker": marker, "receipt": str(path)}


def _region_window(groups: list, budget: int, cursor: int) -> tuple[range, int]:
    """Bound work per call without treating the work budget as a quality result."""
    if budget < 1 or cursor < 0 or cursor > len(groups):
        raise ValueError("invalid region budget or resume cursor")
    end = min(len(groups), cursor + budget)
    return range(cursor, end), end


def _clusters(regions: list[dict], *, max_gap: int = 42, max_y_gap: int = 8) -> list[list[int]]:
    """Group adjacent glyph components into candidate words on one source line."""
    boxes = sorted((r["bbox_pixels"] for r in regions if r.get("location") == "body"),
                   key=lambda box: (box[1], box[0]))
    groups: list[list[int]] = []
    for x0, y0, x1, y1 in boxes:
        if groups and y0 <= groups[-1][3] + max_y_gap and y1 >= groups[-1][1] - max_y_gap and x0 <= groups[-1][2] + max_gap:
            group = groups[-1]
            group[0], group[1] = min(group[0], x0), min(group[1], y0)
            group[2], group[3] = max(group[2], x1), max(group[3], y1)
        else:
            groups.append([x0, y0, x1, y1])
    return groups


def _vlm_transcribe(model, processor, image_path: Path) -> str:
    from mlx_vlm import generate
    prompt = processor.apply_chat_template([{"role": "user", "content": [
        {"type": "image"}, {"type": "text", "text":
            "Transcribe exactly the visible text. Preserve Roman numerals, numbers, signs and punctuation. Return only the transcription."}
    ]}], tokenize=False, add_generation_prompt=True)
    return generate(model, processor, prompt, image=str(image_path), max_tokens=120,
                    verbose=False).text.strip()


def propose_region_repairs(package: Path, source_pdf: Path, pages: list[int], output: Path,
                           independent_receipts: dict[int, dict], *, max_regions_per_page: int = 4,
                           audit_dpi: int = 120, crop_dpi: int = 300) -> dict:
    """Run distinct local OCR readers on bounded source-image crops.

    Every proposed receipt remains review-only until the existing targeted
    repair and eight-component whole-page gate accept it.
    """
    package, source_pdf, output = Path(package), Path(source_pdf), Path(output)
    verify_package(package, source_pdf=source_pdf)
    report = json.loads((package / "report.json").read_text())
    rows = {row["page"]: row for row in (json.loads(line) for line in
            jsonl_physical_lines((package / "pages.jsonl").read_text()) if line.strip(' \t\r'))}
    table_pages = {table["page"] for table in json.loads((package / "tables.json").read_text())}
    output.mkdir(parents=True, exist_ok=True)
    policy, policy_sha = load_region_policy()
    global _MODEL_CACHE
    results = []
    next_offsets = {}
    deferred_total = 0
    pdf = pdfium.PdfDocument(str(source_pdf))
    try:
        for number in pages:
            page = rows[number]
            if page["status"] != "review_required" or page.get("review_decision") or page["method"] not in {"vision", "rapidocr"}:
                results.append({"page": number, "status": "held", "reason": "not a held OCR page"})
                continue
            if number in table_pages:
                results.append({"page": number, "status": "held", "reason": "source table requires separate cell proof"})
                continue
            if any("\u0900" <= character <= "\u097f" for character in page.get("text", "")):
                results.append({"page": number, "status": "held", "reason": "Devanagari crop requires its own recognizer proof"})
                continue
            source_page = pdf[number - 1]
            try:
                evidence = ocr_source_raster_evidence(page, source_page, report["source_pdf_sha256"], dpi=audit_dpi)
                groups = _clusters(evidence.get("unresolved_visual_regions", []))
                if not groups:
                    results.append({"page": number, "status": "held", "reason": "no unresolved source text region"})
                    continue
                cursor_file = output / f"p{number}-cursor.json"
                cursor_binding = {"source_pdf_sha256": report["source_pdf_sha256"],
                                  "page": number, "page_text_sha256": page["text_sha256"],
                                  "model_revision": MODEL_REVISION, "region_policy_sha256": policy_sha,
                                  "groups_sha256": sha256(json.dumps(groups).encode()).hexdigest(),
                                  "audit_dpi": audit_dpi, "crop_dpi": crop_dpi}
                prior_cursor = json.loads(cursor_file.read_text()) if cursor_file.is_file() else {}
                cursor = prior_cursor.get("next_region", 0) if all(
                    prior_cursor.get(key) == value for key, value in cursor_binding.items()) else 0
                region_range, next_cursor = _region_window(groups, max_regions_per_page, cursor)
                rendered = source_page.render(scale=crop_dpi / 72).to_pil()
                for index in region_range:
                    group = groups[index]
                    scale = crop_dpi / audit_dpi
                    gx0, gy0, gx1, gy1 = [round(v * scale) for v in group]
                    # Surround the missed glyphs with source text to keep OCR
                    # word segmentation and reading context stable.
                    x0 = max(0, gx0 - 120)
                    x1 = min(rendered.width, gx1 + 700)
                    y0 = max(0, gy0 - 50)
                    y1 = min(rendered.height, gy1 + 55)
                    box = [x0, y0, x1, y1]
                    crop = rendered.crop(tuple(box))
                    buffer = BytesIO()
                    crop.save(buffer, format="PNG")
                    image_bytes = buffer.getvalue()
                    image_sha = sha256(image_bytes).hexdigest()
                    png = output / f"p{number}-r{index}-{image_sha[:12]}.png"
                    png.write_bytes(image_bytes)
                    rapid = _rapidocr_lines(crop, "en")
                    if rapid is None:
                        results.append({"page": number, "region": index, "status": "held", "reason": "RapidOCR unavailable"})
                        continue
                    lines, _ = rapid
                    selected = []
                    for line in lines:
                        a = line["bbox"]
                        lx0, ly0 = x0 + a[0]*(x1-x0), y0 + a[1]*(y1-y0)
                        lx1, ly1 = x0 + a[2]*(x1-x0), y0 + a[3]*(y1-y0)
                        if lx0 <= gx1+12 and lx1 >= gx0-12 and ly0 <= gy1+12 and ly1 >= gy0-12:
                            selected.append(line)
                    if len(selected) != 1:
                        results.append({"page": number, "region": index, "status": "held",
                                        "reason": "unresolved region has no unique OCR line"})
                        continue
                    # The downstream repair rejects an overlapping patch. Check
                    # that geometry before loading the VLM on this source crop.
                    sx0, sy0, sx1, sy1 = selected[0]["bbox"]
                    candidate_box = [(x0 + sx0*(x1-x0))/rendered.width,
                                     (y0 + sy0*(y1-y0))/rendered.height,
                                     (x0 + sx1*(x1-x0))/rendered.width,
                                     (y0 + sy1*(y1-y0))/rendered.height]
                    area = max(1e-9, (candidate_box[2]-candidate_box[0]) *
                               (candidate_box[3]-candidate_box[1]))
                    if any((max(0, min(line["bbox"][2], candidate_box[2]) - max(line["bbox"][0], candidate_box[0])) *
                            max(0, min(line["bbox"][3], candidate_box[3]) - max(line["bbox"][1], candidate_box[1]))) / area > .1
                           for line in page["ocr"]["lines"]):
                        results.append({"page": number, "region": index, "status": "held",
                                        "reason": "candidate overlaps an existing OCR line"})
                        continue
                    if _MODEL_CACHE is None:
                        from mlx_vlm import load
                        _MODEL_CACHE = load(MODEL, revision=MODEL_REVISION)
                    model, processor = _MODEL_CACHE
                    vlm_text = _vlm_transcribe(model, processor, png)
                    token = selected[0]["text"].strip()
                    third = None
                    inserted_text = token
                    if (policy["three_reader_min_rapid_confidence"] <= selected[0]["confidence"] <
                            policy["two_reader_min_rapid_confidence"] and
                            re.fullmatch(policy["short_label_pattern"], token) and
                            not any(char.isdigit() for char in token)):
                        label_image, label_box = short_label_crop(crop, selected[0])
                        alternatives = vision_crop_lines(label_image)
                        matches = [line for line in alternatives if line["text"].strip().casefold() == token.casefold()
                                   and line["confidence"] >= policy["three_reader_min_vision_confidence"]]
                        if len(matches) == 1:
                            label_png = BytesIO()
                            label_image.save(label_png, format="PNG")
                            third = {"engine": "macos_vision", "line": matches[0],
                                     "source_subcrop_bbox_pixels": label_box,
                                     "source_subcrop_sha256": sha256(label_png.getvalue()).hexdigest(),
                                     "crop_image_sha256": image_sha}
                            inserted_text = agreed_label_literal(token, matches[0]["text"], vlm_text)
                    if ((selected[0]["confidence"] < policy["two_reader_min_rapid_confidence"] and third is None) or len(token) > 80 or
                            (third is not None and inserted_text is None) or
                            not re.search(r"\w", token) or
                            not re.search(r"(?<!\w)" + re.escape(token.casefold()) + r"(?!\w)", vlm_text.casefold())):
                        results.append({"page": number, "region": index, "status": "held",
                                        "reason": "independent crop readers disagree or token is nontext",
                                        "rapid_text": token, "vlm_text": vlm_text})
                        continue
                    receipt = {"source_pdf": str(source_pdf), "source_pdf_sha256": report["source_pdf_sha256"],
                               "page": number, "original_page_text_sha256": page["text_sha256"],
                               "render_dpi": crop_dpi, "crop_bbox_pixels": box,
                               "crop_image_sha256": image_sha, "rapidocr_line": selected[0],
                               "rapidocr_model_sha256": _rapidocr_model_sha256("en"),
                               "vlm_engine": "mlx-vlm", "vlm_model": MODEL, "vlm_revision": MODEL_REVISION,
                               "vlm_crop_image_sha256": image_sha, "vlm_text": vlm_text,
                               "vlm_text_sha256": sha256(vlm_text.encode()).hexdigest(),
                               "region_policy_sha256": policy_sha,
                               "third_reader": third,
                               "inserted_text": inserted_text,
                               "independent_full_receipt": independent_receipts[number],
                               "independent_original_text_sha256": independent_receipts[number]["selected_page_text_sha256"]}
                    path = output / f"p{number}-r{index}-receipt.json"
                    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
                    results.append({"page": number, "region": index, "status": "candidate", "receipt": str(path),
                                    "rapid_text": selected[0]["text"], "vlm_text": vlm_text})
                if next_cursor < len(groups):
                    next_offsets[number] = next_cursor
                    deferred_total += len(groups) - next_cursor
                    temporary = cursor_file.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps({**cursor_binding, "next_region": next_cursor}) + "\n")
                    temporary.replace(cursor_file)
                else:
                    cursor_file.unlink(missing_ok=True)
            finally:
                source_page.close()
    finally:
        pdf.close()
    return {"source_pdf_sha256": report["source_pdf_sha256"], "pages": len(pages),
            "candidate_regions": sum(row["status"] == "candidate" for row in results),
            "deferred_regions": deferred_total, "next_region_offsets": next_offsets,
            "results": results}
