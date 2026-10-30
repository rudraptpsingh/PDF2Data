"""Observed script in extracted page text, without inferring OCR correctness."""


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
