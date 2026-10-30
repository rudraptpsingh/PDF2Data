from copy import deepcopy
from hashlib import sha256
import pytest
from local_vector_batch.legal_l1 import structure_document
from local_vector_batch.readable_export import full_document,html_document,export_readable

def fixture():
    texts=['Heading\n2. Respondents must\nRoP in Petition No. 1\nPage 1 of 2','file replies; rejoinder, if any, within two weeks thereafter.\n3. Next.\nPage 2 of 2']
    pages=[{'id':f'd:p{i}','page':i,'text':t,'text_sha256':sha256(t.encode()).hexdigest(),'method':'pdf_text'} for i,t in enumerate(texts,1)]
    structure=structure_document(pages,'a'*64,'record_of_proceedings')
    return {'source_id':'d','title':'Test <script>','source_pdf_sha256':'a'*64,'pages':pages,'structure':structure,'tables':[],
      'quality':{'results':[{'page':i,'outcome':'repair_required'} for i in (1,2)]},'flagged_pages':2,'fact_candidates':[]}

def test_continuous_paragraph_retains_cross_page_condition_and_graph():
    data=fixture();md,html,graph=full_document(data)
    assert 'Respondents must\nfile replies; rejoinder, if any, within two weeks thereafter.' in md
    assert 'RoP in Petition' not in md
    unit=next(u for u in data['structure']['units'] if u['label']=='2')
    assert len([e for e in graph['edges'] if e['from']==unit['id'] and e['type']=='anchored_in'])==2
    assert graph['publication_eligible'] is False
    assert 'Source page 1' in html and 'Source page 2' in html
    assert 'source.pdf#page=2' in md

def test_changed_source_span_cannot_be_rendered_as_valid():
    data=fixture();data['pages'][1]['text']='changed'
    with pytest.raises(ValueError,match='anchor'):full_document(data)

def test_table_cells_and_observations_retain_ownership_without_inventing_legal_edges():
    data=fixture();data['tables']=[{'id':'t','page':1,'cells':[['GNA','<script>'],['date','Not effective']]}]
    data['fact_candidates']=[{'kind':'word','value_text':'Next','page':2,'char_start':data['pages'][1]['text'].index('Next'),'char_length':4,'status':'unverified_candidate'}]
    md,html,g=full_document(data)
    assert '&lt;script&gt;' in md and 'Not effective' in html
    assert len([e for e in g['edges'] if e['type']=='contains_cell'])==4
    assert any(e['type']=='observed_within_unit' for e in g['edges'])
    assert not any(e['type']=='legal_obligation' for e in g['edges'])
    rendered=html_document(data,False,html)
    assert '<h1>Test &lt;script&gt;</h1>' in rendered
    assert 'id="full"' in rendered and 'id="comparison" hidden' in rendered

def test_export_never_overwrites_source_or_existing_output(tmp_path):
    package=tmp_path/'package';package.mkdir()
    with pytest.raises(ValueError,match='new directory'):export_readable(package,package)
    with pytest.raises(ValueError,match='outside'):export_readable(package,package/'readable')
