"""Shared legacy font mappings must not turn reader agreement into clearance."""
import hashlib
import json

import pytest

from local_vector_batch.quality_gate import assess_page, load_policy
from local_vector_batch.text_script import native_font_script_evidence


def glyph(font, text='Hkkjr'):
    return {'fontname': font, 'text': text}


def test_observed_subset_font_routes_latin_codes_without_rewriting_them():
    source = [glyph('UVLDRG+Devnagari-ChanakyaNormalA')]
    result = native_font_script_evidence(source, load_policy())
    assert result['required_scripts'] == ['devanagari']
    assert result['observed_font_glyph_counts'] == {'devnagari-chanakyanormala': 1}
    assert source[0]['text'] == 'Hkkjr'
    assert result['decoded_text_modified'] is False


@pytest.mark.parametrize('font,text', [
    ('Times-Roman', 'CERC'), ('ABCDEF+Times-Roman', 'भारत'),
    ('Devnagari-ChanakyaNormalA-copy', 'Hkkjr'),
    ('NotDevnagari-ChanakyaNormalA', 'Hkkjr'),
    ('Devnagari-ChanakyaNormalA', ' '),
])
def test_unobserved_families_substrings_and_whitespace_do_not_invent_a_route(font, text):
    assert not native_font_script_evidence([glyph(font, text)], load_policy())['required_scripts']


def test_font_routes_are_configuration_not_word_replacements():
    policy = load_policy()
    policy['native_font_families_requiring_raster_proof'] = {'observed-family': 'devanagari'}
    assert native_font_script_evidence([glyph('ABCDEF+Observed-Family')], policy)['required_scripts'] == ['devanagari']
    assert not native_font_script_evidence([glyph('Devnagari-ChanakyaNormalA')], policy)['required_scripts']


@pytest.mark.parametrize('evidence_route', ['fresh_package', 'fresh_geometry'])
def test_identical_native_readers_and_full_geometry_do_not_bypass_font_route(evidence_route):
    text = 'Hkkx Hkkjr 2026'
    page = {'page': 1, 'method': 'pdf_text', 'text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'selected_quality': {'needs_ocr': False}}
    geometry = {'source_pdf_sha256': 'a'*64, 'page': 1,
                'page_text_sha256': page['text_sha256'], 'ready': True,
                'ink_pixels': 300, 'unaccounted_ink_pixels': 0,
                'nontext_objects': {'images': 0, 'curves': 0, 'rects': 0, 'lines': 0}}
    evidence = native_font_script_evidence([glyph('Devnagari-ChanakyaNormalA', text)], load_policy())
    extra = {'native_font_evidence': evidence}
    if evidence_route == 'fresh_geometry':
        geometry['native_font_script_evidence'] = evidence
        extra = {}
    result = assess_page(page, source_pdf_sha256='a'*64, package_pdf_sha256='a'*64,
                         independent_text=text, source_geometry=geometry,
                         **extra)
    assert result['components']['source_render']['state'] == 'pass'
    assert result['components']['language']['state'] == 'unknown'
    assert result['outcome'] == 'repair_required'
    assert page['text'] == text


@pytest.mark.parametrize('routes', [[], {'UpperCase': 'devanagari'}, {'font': 'invented-script'}])
def test_invalid_font_policy_is_rejected(tmp_path, routes):
    policy = load_policy()
    policy['native_font_families_requiring_raster_proof'] = routes
    path = tmp_path/'policy.json'
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match='native font'):
        load_policy(path)
