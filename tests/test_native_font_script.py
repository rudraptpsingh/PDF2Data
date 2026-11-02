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


@pytest.mark.parametrize('font', ['KrutiDev010', 'BCDFEE+KrutiDev010', 'BCDFEE+KRUTIDEV010'])
def test_observed_krutidev_family_requires_proof_for_legacy_latin_codes(font):
    source = [glyph(font, 'dsUæh; fo|qr fofu;ked vk;ksx')]
    before = json.dumps(source)
    route = native_font_script_evidence(source, load_policy())
    assert route['required_scripts'] == ['devanagari']
    assert route['observed_font_glyph_counts'] == {'krutidev010': 1}
    assert json.dumps(source) == before
    assert route['decoded_text_modified'] is False


@pytest.mark.parametrize('font,text', [('NotKrutiDev010', 'codes'),
    ('KrutiDev010-copy', 'codes'), ('KrutiDev010', ' '), ('Times-Roman', 'KrutiDev010')])
def test_krutidev_substrings_content_and_blank_glyphs_do_not_route(font, text):
    assert native_font_script_evidence([glyph(font, text)], load_policy())['required_scripts'] == []


def test_page_script_metadata_combines_unicode_and_observed_font_without_decoding():
    from local_vector_batch.text_script import native_script_verification
    policy = load_policy()
    route = native_font_script_evidence([glyph('BCDFEE+KrutiDev010')], policy)
    result = native_script_verification('CERC 2026 Hkkjr', policy, native_font_evidence=route)
    assert result['required_scripts'] == ['devanagari']
    assert result['state'] == 'unverified'
    assert result['source_font_evidence'] == route
    assert result['reason'] == 'native_complex_script_glyph_mapping_unverified'


def _known_font_page():
    from local_vector_batch.text_script import native_script_verification
    text = 'Software control Hkkjr 2026'
    font = native_font_script_evidence([glyph('BCDFEE+KrutiDev010')], load_policy())
    return {'page': 1, 'method': 'pdf_text', 'text': text, 'status': 'ready',
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'selected_quality': {'needs_ocr': False},
            'script_verification': native_script_verification(text, load_policy(), native_font_evidence=font)}


def test_cached_font_requirement_survives_assessment_even_with_empty_fresh_font_route():
    page = _known_font_page()
    geometry = {'source_pdf_sha256': 'a' * 64, 'page': 1,
                'page_text_sha256': page['text_sha256'], 'ready': True,
                'ink_pixels': 300, 'unaccounted_ink_pixels': 0,
                'nontext_objects': {'images': 0, 'curves': 0, 'rects': 0, 'lines': 0}}
    for fresh in (None, native_font_script_evidence([], load_policy())):
        result = assess_page(page, source_pdf_sha256='a' * 64, package_pdf_sha256='a' * 64,
            independent_text=page['text'], source_geometry=geometry, native_font_evidence=fresh)
        assert result['components']['language']['state'] == 'unknown'
        assert result['outcome'] == 'repair_required'


@pytest.mark.parametrize('inventory_failed', [False, True])
def test_machine_application_retains_known_font_or_inventory_hold(tmp_path, inventory_failed):
    from local_vector_batch.quality_gate import apply_machine_quality_decisions
    page = _known_font_page()
    if inventory_failed:
        page['script_verification'] = {'required_scripts': [], 'state': 'unverified',
                                      'reason': 'native_font_inventory_unavailable'}
    held, count = apply_machine_quality_decisions([page], 'a' * 64, tmp_path / 'missing.json')
    assert count == 0 and held[0]['status'] == 'review_required'
    assert page['status'] == 'ready'
    # A software-only otherwise-passing receipt must not erase the known hold.
    ordinary = {k: v for k, v in page.items() if k != 'script_verification'}
    geometry = {'source_pdf_sha256': 'a' * 64, 'page': 1,
                'page_text_sha256': page['text_sha256'], 'ready': True,
                'ink_pixels': 300, 'unaccounted_ink_pixels': 0,
                'nontext_objects': {'images': 0, 'curves': 0, 'rects': 0, 'lines': 0}}
    row = assess_page(ordinary, source_pdf_sha256='a' * 64, package_pdf_sha256='a' * 64,
                     independent_text=page['text'], source_geometry=geometry)
    assert row['outcome'] == 'auto_accepted'
    row.update(policy_sha256=hashlib.sha256(json.dumps(load_policy(), sort_keys=True,
               ensure_ascii=False).encode()).hexdigest(),
               independent_text_sha256=page['text_sha256'], page_complete=True,
               unresolved_visual_regions=[])
    row['components']['source_render'].update(receipt_sha256='b' * 64, rendered_image_sha256='c' * 64)
    receipt = {'schema_version': 1, 'source_verified': True, 'source_pdf_sha256': 'a' * 64,
               'policy_sha256': row['policy_sha256'], 'human_review_receipts_created': 0, 'results': [row]}
    path = tmp_path / 'software-receipt.json'
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='incomplete machine quality proof'):
        apply_machine_quality_decisions([page], 'a' * 64, path)
    if not inventory_failed:
        row['components']['language']['independent_model_confirmed'] = True
        path.write_text(json.dumps(receipt))
        accepted, count = apply_machine_quality_decisions([page], 'a' * 64, path)
        assert count == 1
        assert accepted[0]['script_verification']['state'] == 'machine_raster_text_verified'
        assert accepted[0]['script_verification']['source_font_evidence']['required_scripts'] == ['devanagari']


@pytest.mark.parametrize('evidence_route', ['fresh_package', 'fresh_geometry'])
@pytest.mark.parametrize('font', ['Devnagari-ChanakyaNormalA', 'BCDFEE+KrutiDev010'])
def test_identical_native_readers_and_full_geometry_do_not_bypass_font_route(evidence_route, font):
    text = 'Hkkx Hkkjr 2026'
    page = {'page': 1, 'method': 'pdf_text', 'text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'selected_quality': {'needs_ocr': False}}
    geometry = {'source_pdf_sha256': 'a'*64, 'page': 1,
                'page_text_sha256': page['text_sha256'], 'ready': True,
                'ink_pixels': 300, 'unaccounted_ink_pixels': 0,
                'nontext_objects': {'images': 0, 'curves': 0, 'rects': 0, 'lines': 0}}
    evidence = native_font_script_evidence([glyph(font, text)], load_policy())
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
