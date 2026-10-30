import copy
from local_vector_batch.native_cell_space_witness import restore_explicit_source_spaces

def glyphs(text):
 result=[];x=10.
 for c in text:
  result.append({'text':c,'x0':x,'x1':x+3,'top':10.,'bottom':20.});x+=3
 return result

def test_explicit_nbsp_is_restored_only_in_reader():
 result=restore_explicit_source_spaces('If so','Ifso',glyphs('If\xa0so'),'unique If\xa0so context')
 assert result and result['corrected_text']=='If so'and result['raw_text']=='Ifso';assert result['source_space_boundaries'][0]['native_space_glyphs'][0]['codepoint']==160;assert not result['source_text_modified']

def test_multiple_separators_restore_all_or_none():
 r=restore_explicit_source_spaces('the Appropriate Commission','theAppropriateCommission',glyphs('the\xa0Appropriate\xa0Commission'),'the\xa0Appropriate\xa0Commission');assert r['corrected_text']=='the Appropriate Commission';assert len(r['source_space_boundaries'])==2

def test_geometric_gap_without_actual_space_never_invents_separator():
 g=glyphs('Ifso')
 for c in g[2:]:c['x0']+=3;c['x1']+=3
 assert restore_explicit_source_spaces('If so','Ifso',g,'If so')is None

def test_third_reader_must_independently_have_unique_same_separator():
 for third in('Ifso','If so and If so','If\nso'):
  assert restore_explicit_source_spaces('If so','Ifso',glyphs('If\xa0so'),third)is None

def test_true_content_change_and_negative_operator_not_erased():
 assert restore_explicit_source_spaces('shall not','shallnot',glyphs('shall\xa0not'),'shall\xa0not')['corrected_text']=='shall not'
 for raw in('shallnow','shall','shall never'):
  assert restore_explicit_source_spaces('shall not',raw,glyphs('shall\xa0not'),'shall\xa0not')is None

def test_crossline_and_inconsistent_space_geometry_stay_held():
 g=glyphs('If\xa0so');g[3].update(top=30.,bottom=40.);assert restore_explicit_source_spaces('If so','Ifso',g,'If so')is None
 g=glyphs('If\xa0so');g[2].update(x0=100.,x1=103.);assert restore_explicit_source_spaces('If so','Ifso',g,'If so')is None
 assert restore_explicit_source_spaces('If\nso','Ifso',glyphs('If\xa0so'),'If so')is None

def test_extra_reader_whitespace_cannot_be_deleted_by_this_rule():assert restore_explicit_source_spaces('If so','I fso',glyphs('If\xa0so'),'If so')is None


def test_source_space_font_advance_can_overhang_ink_bounds_but_center_must_be_in_gap():
 g=glyphs('If\xa0so');g[2].update(x0=15.6,x1=19.2);g[3].update(x0=18.9,x1=21.9);g[4].update(x0=21.9,x1=24.9)
 r=restore_explicit_source_spaces('If so','Ifso',g,'If\xa0so');assert r and r['corrected_text']=='If so'
 # An arbitrarily wide nominal space cannot substitute for bounded source geometry.
 g[2].update(x0=-100,x1=135);assert restore_explicit_source_spaces('If so','Ifso',g,'If\xa0so')is None
