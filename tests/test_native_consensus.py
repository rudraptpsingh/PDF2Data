from local_vector_batch.native_consensus import choose_witness


def source(text,**kw):return {'text':text,'status':'captured','glyph_coverage':True,**kw}


def test_exact_source_line_consensus_resolves_artificial_date_spacing():
    selected='The letter is dated 23.12.2014.'
    text,basis=choose_witness(selected,'The letter is dated 23.1    2.2014.',source(selected))
    assert text==selected and basis=='pdfium_source_lines_exact_native_consensus'


def test_missing_digit_or_negation_cannot_be_repaired_by_consensus():
    for selected,actual in [('Pay 91,750,000.','Pay 91,750.'),('Shall not qualify.','Shall qualify.'),('Nowhere','Now here')]:
        text,basis=choose_witness(selected,actual,source(actual))
        assert basis=='pypdf_layout_disagreement_requires_repair'


def test_incomplete_geometry_cannot_authorize_a_matching_witness():
    text,basis=choose_witness('Two weeks.','Three weeks.',source('Two weeks.',glyph_coverage=False))
    assert basis=='pypdf_layout_disagreement_requires_repair'


def test_good_primary_is_retained_and_third_reader_disagreement_is_not_hidden():
    selected='No final finding.'
    text,basis=choose_witness(selected,selected,source('Final finding.'))
    assert text==selected and basis=='pypdf_layout'


def test_source_supported_subscript_order_is_repaired_without_new_characters(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from collections import Counter
    import local_vector_batch.native_consensus as module
    old='power under GNA, GNA and T-GNA\nRE RE'
    new='power under GNA, GNARE and T-GNARE'
    lines=[{'text':'power under GNA, GNA and T-GNA','top':10,'bottom':20,'x0':0,'x1':100,'chars':[]},
           {'text':'RE RE','top':17,'bottom':24,'x0':20,'x1':90,'chars':[]}]
    page=SimpleNamespace(extract_text_lines=lambda **kw:lines)
    monkeypatch.setattr(module,'read_source_lines',lambda *args:{'status':'captured','lines':[{'bbox':[0,10,100,24],'text':new}]})
    text,repairs=module.repair_source_lines(Path('unused.pdf'),page,1,old,new)
    assert text==new and len(repairs)==1
    assert Counter(c for c in text if not c.isspace())==Counter(c for c in old if not c.isspace())


def test_line_repair_rejects_new_digit_or_a_dropped_condition(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    import local_vector_batch.native_consensus as module
    page=SimpleNamespace(extract_text_lines=lambda **kw:[{'text':'Pay 91,750,000; not final.','top':10,'bottom':20,'x0':0,'x1':100,'chars':[]}])
    for replacement in ['Pay 91,750; not final.','Pay 91,750,000; final.']:
        monkeypatch.setattr(module,'read_source_lines',lambda *args:{'status':'captured','lines':[{'bbox':[0,10,100,20],'text':replacement}]})
        text,repairs=module.repair_source_lines(Path('unused.pdf'),page,1,'Pay 91,750,000; not final.',replacement)
        assert not repairs and text=='Pay 91,750,000; not final.'
