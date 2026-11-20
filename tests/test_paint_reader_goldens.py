"""L2/L3: the paint-visibility reader keeps the PyMuPDF-era outcomes without PyMuPDF.

L2 pins the goldens recorded through the PyMuPDF code path before it was
replaced. L3 runs the same fixture pages through the shipped reader and
requires equality with those goldens, or a reviewed difference listed in
``goldens/paint_visibility.differences.json``.
"""
import builtins
import hashlib
import json
import re
from pathlib import Path

import pytest

from paint_fixtures import CASES, REAL_PAGES, build_all, load_goldens, project

HERE = Path(__file__).parent
GOLDENS = HERE / 'goldens' / 'paint_visibility.pymupdf.json.gz'
GOLDENS_SHA256 = '46d4f1f6da17cc5c8ee920c3df060175c7d4d445a3cfb4791f713309bd7ea5b7'
DIFFERENCES = HERE / 'goldens' / 'paint_visibility.differences.json'
BBOX_TOLERANCE = 1.0   # points; glyph boxes are font-metric boxes in both readers
FILL_TOLERANCE = 0.01  # points; fills are exact path geometry


def test_l2_goldens_recorded_before_the_change():
    assert hashlib.sha256(GOLDENS.read_bytes()).hexdigest() == GOLDENS_SHA256
    goldens = load_goldens(GOLDENS)
    assert goldens['reader'] == 'PyMuPDF'
    pages = goldens['pages']
    assert len(pages) >= 30 and set(pages) == set(CASES) | {f'cerc_545_td_2026_p{p}' for p in REAL_PAGES}
    states = {g['state'] for page in pages.values() for g in page.get('glyphs', [])}
    assert states == {'fully_occluded', 'held_partial_occlusion', 'held_unproved_individual_ink_bounds',
                      'held_unsupported_text_paint', 'retained_raw_whitespace', 'retained_visible_or_unproven'}


def compare(golden, actual):
    """Kinds of difference between a golden page and a fresh one (empty = equal)."""
    if 'error' in golden or 'error' in actual:
        return [] if golden == actual else ['error']
    kinds = []
    g, a = golden['glyphs'], actual['glyphs']
    if [x['text'] for x in g] != [x['text'] for x in a]:
        kinds.append('glyph_text_or_order')
    else:
        if any(x['state'] != y['state'] or x['excluded'] != y['excluded'] for x, y in zip(g, a)):
            kinds.append('visibility_state')
        if any(x['ink_basis'] != y['ink_basis'] for x, y in zip(g, a)):
            kinds.append('ink_basis')
        if any(abs(p - q) > BBOX_TOLERANCE for x, y in zip(g, a) for p, q in zip(x['bbox'], y['bbox'])):
            kinds.append('glyph_bbox')
    if golden['retained_text'] != actual['retained_text'] or golden['excluded_text'] != actual['excluded_text']:
        kinds.append('visible_text')
    gf, af = golden['accepted_fill_bboxes'], actual['accepted_fill_bboxes']
    if len(gf) != len(af) or any(abs(p - q) > FILL_TOLERANCE for x, y in zip(gf, af) for p, q in zip(x, y)):
        kinds.append('accepted_fills')
    if golden['unsupported_reasons'] != actual['unsupported_reasons']:
        kinds.append('unsupported_paints')
    if any(abs(p - q) > FILL_TOLERANCE for p, q in zip(golden['page_bbox'], actual['page_bbox'])):
        kinds.append('page_bbox')
    return kinds


@pytest.fixture(scope='module')
def fresh(tmp_path_factory):
    from local_vector_batch import native_paint_visibility as module
    folder = tmp_path_factory.mktemp('paint')
    return module, {case: project(module.capture, module.analyze, path, page)
                    for case, path, page in build_all(folder)}


def test_l3_reader_is_not_agpl(fresh, monkeypatch, tmp_path):
    module, _ = fresh
    assert module.READER == 'pdfium'
    real_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in ('pymupdf', 'fitz'):
            raise ImportError(f'{name} is AGPL and must not load at runtime')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    case = next(iter(build_all(tmp_path)))
    module.analyze(case[1], case[2], [])


def test_l3_no_runtime_source_imports_pymupdf():
    root = HERE.parent / 'src'
    offenders = [str(p.relative_to(root)) for p in root.rglob('*.py')
                 if re.search(r'^\s*(import|from)\s+(pymupdf|fitz)\b', p.read_text(), re.M)]
    assert offenders == []


def test_l3_replacement_equals_goldens_or_reviewed_difference(fresh):
    _, pages = fresh
    goldens = load_goldens(GOLDENS)['pages']
    reviewed = json.loads(DIFFERENCES.read_text())['cases'] if DIFFERENCES.exists() else {}
    observed = {case: compare(goldens[case], pages[case]) for case in goldens}
    observed = {case: kinds for case, kinds in observed.items() if kinds}
    unreviewed = {case: [k for k in kinds if k not in reviewed.get(case, {})] for case, kinds in observed.items()}
    assert {c: k for c, k in unreviewed.items() if k} == {}
    stale = {case: [k for k in kinds if k not in observed.get(case, [])] for case, kinds in reviewed.items()}
    assert {c: k for c, k in stale.items() if k} == {}


def test_l3_safety_never_weaker_than_goldens(fresh):
    """Whatever the reviewed differences, no glyph the goldens kept is removed."""
    _, pages = fresh
    for case, golden in load_goldens(GOLDENS)['pages'].items():
        actual = pages[case]
        if 'error' in golden:
            assert actual == golden, case
            continue
        kept = [g['text'] for g in golden['glyphs'] if not g['excluded']]
        removed_now = [g['text'] for g in actual['glyphs'] if g['excluded']]
        assert [g['text'] for g in actual['glyphs']] == [g['text'] for g in golden['glyphs']], case
        assert all(not a['excluded'] for g, a in zip(golden['glyphs'], actual['glyphs']) if not g['excluded']), \
            (case, removed_now, kept[:20])
