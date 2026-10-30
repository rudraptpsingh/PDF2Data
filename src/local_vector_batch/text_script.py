"""Observed script in extracted page text, without inferring OCR correctness."""

from collections import Counter
import re


def native_font_script_evidence(glyphs, policy: dict) -> dict:
    """Route observed legacy font encodings independently of decoded Unicode.

    A PDF reader can faithfully reproduce the same wrong Latin character codes
    for a Hindi font. Font names only request review; they never decode glyphs.
    Subset prefixes are ignored, while font-family matching stays exact.
    """
    families = policy.get('native_font_families_requiring_raster_proof', {})
    observed = Counter()
    for glyph in glyphs:
        if not str(glyph.get('text', '')).strip():
            continue
        font = re.sub(r'^[A-Z]{6}\+', '', str(glyph.get('fontname', '')))
        if font.casefold() in families:
            observed[font.casefold()] += 1
    return {'required_scripts': sorted({families[font] for font in observed}),
            'observed_font_glyph_counts': dict(sorted(observed.items())),
            'basis': 'observed_source_font_family_review_route_only',
            'decoded_text_modified': False}


def classify_text_script(text: str) -> dict:
    devanagari = sum('\u0900' <= char <= '\u097f' for char in text)
    latin = sum(('A' <= char <= 'Z') or ('a' <= char <= 'z') for char in text)
    kind = ('mixed' if devanagari and latin else 'devanagari' if devanagari else
            'latin' if latin else 'unknown')
    return {'text_script': kind, 'script_counts': {'devanagari': devanagari, 'latin': latin}}


def native_script_verification(text: str, policy: dict) -> dict:
    """Native-parser agreement cannot validate a shared PDF font glyph map.

    This is a routing gate only. It preserves original Unicode text and never
    corrects words or claims that a script classification verifies language.
    """
    observed = classify_text_script(text)['script_counts']
    minimum = policy['native_complex_script_min_characters']
    required = [name for name in policy['native_scripts_requiring_raster_linguistic_proof']
                if observed.get(name, 0) >= minimum]
    return {'required_scripts': required, 'state': 'unverified' if required else 'not_required',
            'reason': 'native_complex_script_glyph_mapping_unverified' if required else None,
            'proof_required': 'source_bound_independent_raster_recognition_and_linguistic_review' if required else None}
