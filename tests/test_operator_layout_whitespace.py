"""Token comparison controls, separate from source/language/raster acceptance."""
from hashlib import sha256
import pytest
from local_vector_batch.quality_gate import _operators,operator_source_spans,load_policy,assess_page


@pytest.mark.parametrize('raw',['shall not','shall  not','shall\tnot','shall\nnot','shall\u00a0not','shall\u202fnot'])
def test_existing_multiword_operator_survives_layout_whitespace_without_changing_raw_spans(raw):
    text='😀 prefix '+raw+' 12 units';start=text.index('shall')
    assert _operators(text,load_policy())==['shall not']
    rows=operator_source_spans(text,load_policy())
    assert rows==[dict(canonical_operator='shall not',exact_quote=raw,char_start=start,char_length=len(raw),offset_encoding='codepoint')]
    assert text[rows[0]['char_start']:rows[0]['char_start']+rows[0]['char_length']]==raw


@pytest.mark.parametrize('separator',['\u0085','\u2028','\u2029','\v','\f'])
def test_nonlayout_separators_do_not_join_an_operative_phrase(separator):
    assert _operators('shall'+separator+'not',load_policy())!=['shall not']


@pytest.mark.parametrize('left,right',[
    ('shall not','shall'),('shall not','shall now'),('shall not','not shall'),
    ('shall not','shallnot'),('shall not','shall. not'),('shall not','shall\u200bnot'),
    ('at least','at last'),('at least','atleast'),('and or','or and'),
    ('whichever is higher','whichever higher'),
])
def test_missing_changed_reordered_negated_or_corrupt_words_never_gain_equivalence(left,right):
    assert _operators(left,load_policy())!=_operators(right,load_policy())


def test_word_boundaries_and_unicode_offsets_are_not_guessed():
    assert _operators('notable anderson orchard',load_policy())==[]
    assert _operators('ＡＮＤ',load_policy())==['and']  # existing NFKC behavior
    assert operator_source_spans('ＡＮＤ',load_policy()) is None


def test_numeric_disagreement_remains_a_separate_failure_and_page_remains_held():
    text='shall not exceed 12 units and 30 days'
    page=dict(page=1,text=text,text_sha256=sha256(text.encode()).hexdigest(),method='pdf_text',selected_quality={})
    result=assess_page(page,source_pdf_sha256='a'*64,package_pdf_sha256='a'*64,
        independent_text='shall  not exceed 21 units and 30 days')
    assert result['components']['ordered_operators']['state']=='pass'
    assert result['components']['ordered_numbers']['state']=='fail'
    assert result['outcome']=='repair_required'


def test_cached_mode_is_an_explicit_cli_comparison_without_reader_reruns():
    from local_vector_batch.cli import parser
    args=parser().parse_args(['pdf-quality','--package','p','--source-pdf','p/original.pdf',
        '--output','candidate.json','--cached-native-operators','--no-source-raster','--json'])
    assert args.cached_native_operators and args.source_raster is False


def test_actual_cached_cli_preserves_every_original_component_and_refuses_existing_output(tmp_path,capsys):
    import json
    from test_legal_l1 import source_fixture
    from local_vector_batch.legal_l1 import extract_document
    from local_vector_batch.cli import main
    package=tmp_path/'package';extract_document(source_fixture(tmp_path),package)
    original=json.loads((package/'quality.json').read_text());output=tmp_path/'comparison.json'
    args=['pdf-quality','--package',str(package),'--source-pdf',str(package/'original.pdf'),
        '--output',str(output),'--cached-native-operators','--no-source-raster','--json']
    assert main(args)==0
    result=json.loads(output.read_text())
    assert result['results']==original['results'] and result['counts']==original['counts']
    assert result['operator_comparison']['original_ledger_preserved'] is True
    assert result['operator_comparison']['source_quality_approval_granted'] is False
    raw=output.read_bytes();assert main(args)==2 and output.read_bytes()==raw
