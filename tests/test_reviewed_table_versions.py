from copy import deepcopy
import pytest
from local_vector_batch.reviewed_table_versions import _model_literal,_partition,_html,sha


def candidate():
    return {'interpretation':{'literal_text':'Hydro generating station including pumped storage hydro\ngenerating stations',
             'associations':[{'source_literal':'40 years'}]},
             'original_cells':[{'row':5,'column':1}]}


def model(text,kind='text'):
    return {'status':'processed_candidate','possible_token_cap':False,'source_quality_clearance':False,
        'review_origin':'local_vision_model','kind':kind,'text':text,'source_pdf_sha256':'a'*64,
        'page':7,'image_sha256':'b'*64}


def test_hydro_model_row_association_and_source_identifier_join_are_exact():
    c=candidate();t={'cell_spans':[{'row':5,'column':0,'text':'(e)'}]}
    m=model('<table><tr><td>(e) Hydro generating station including pumped storage hydro generating stations</td><td>40 years</td></tr></table>','table')
    assert _model_literal(c,m,t)['literal_agreement']=='exact_nonwhitespace_codepoints'
    m['text']=m['text'].replace('40 years','35 years')
    with pytest.raises(ValueError):_model_literal(c,m,t)


@pytest.mark.parametrize('change',['missing_word','wrong_number','missing_negation','capped','promoted','wrong_origin'])
def test_model_output_cannot_replace_source_reading_without_exact_full_literal(change):
    c={'interpretation':{'literal_text':'No exemption after 30 days'}}
    m=model(c['interpretation']['literal_text'])
    if change=='missing_word':m['text']='No exemption 30 days'
    elif change=='wrong_number':m['text']='No exemption after 80 days'
    elif change=='missing_negation':m['text']='exemption after 30 days'
    elif change=='capped':m['possible_token_cap']=True
    elif change=='promoted':m['source_quality_clearance']=True
    else:m['review_origin']='human'
    with pytest.raises(ValueError):_model_literal(c,m)


def test_hindi_codepoints_are_not_ascii_folded_or_typo_normalized():
    c={'interpretation':{'literal_text':'टांडा टीपीएस (यूनिट 1)'}}
    assert _model_literal(c,model('टांडा टीपीएस (यूनिट 1)'))['model_quality_approval']is False
    with pytest.raises(ValueError):_model_literal(c,model('टांडा टीपीएस (यूजिट 1)'))


def test_complete_pay_matrix_literal_must_not_be_abbreviated():
    c={'interpretation':{'literal_text':'(4)\nLevel –II\nAs per 7th CPC\npay matrix\n(PB-I, GP-\n₹ 1900/- pre-\nrevised scale)'}}
    assert _model_literal(c,model(c['interpretation']['literal_text']))
    with pytest.raises(ValueError):_model_literal(c,model('(4) Level -II'))


def test_exact_table_partition_keeps_merged_slots_and_source_blanks_separate():
    t={'table_version_id':'v','source_pdf_sha256':'a'*64,'page':1,'row_count':2,'column_count':2,
       'cell_spans':[{'id':'c0','row':0,'column':0,'row_span':2,'column_span':1,'text':'Long\nlabel'},
                     {'id':'c1','row':0,'column':1,'row_span':1,'column_span':1,'text':'40 years'},
                     {'id':'c2','row':1,'column':1,'row_span':1,'column_span':1,'text':''}]}
    r=_partition(t);cursor=0
    for span in r['spans']:
        assert span['char_start']==cursor and r['text'][cursor:cursor+span['char_length']]==span['exact_quote']
        cursor+=span['char_length']
    assert cursor==len(r['text'])and r['text']=='Long\nlabel\t40 years\n\t'
    assert t['cell_spans'][2]['table_text_span']['char_length']==0


def test_superscript_html_uses_exact_unicode_offsets_and_escapes_source_markup():
    t={'table_version_id':'v','source_pdf_sha256':'a'*64,'row_count':1,'cell_spans':[
       {'row':0,'column':0,'row_span':1,'column_span':1,'text':'7th & CPC',
        'typography':[{'start':1,'end':3,'literal':'th','kind':'superscript'}]}]}
    html=_html(t);assert '7<sup>th</sup> &amp; CPC'in html
    t['cell_spans'][0]['typography'][0]['end']=4
    with pytest.raises(ValueError):_html(t)
