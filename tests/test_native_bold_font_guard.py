"""Observed Bold legacy font requests review; it does not decode Latin codes."""
import hashlib
import pytest
from local_vector_batch.quality_gate import assess_page,load_policy
from local_vector_batch.text_script import native_font_script_evidence


@pytest.mark.parametrize('font',['Devnagari-ChanakyaBoldA','ABCDEF+Devnagari-ChanakyaBoldA'])
def test_exact_bold_family_cannot_pass_on_matching_latin_reader_codes(font):
    text='Hkkjr 2026'
    glyphs=[{'fontname':font,'text':text}]
    route=native_font_script_evidence(glyphs,load_policy())
    assert route['required_scripts']==['devanagari']
    assert route['decoded_text_modified']is False and glyphs[0]['text']==text
    page={'page':1,'text':text,'method':'pdf_text','text_sha256':hashlib.sha256(text.encode()).hexdigest(),
          'selected_quality':{'needs_ocr':False}}
    geometry={'source_pdf_sha256':'a'*64,'page':1,'page_text_sha256':page['text_sha256'],'ready':True,
              'ink_pixels':300,'unaccounted_ink_pixels':0,'nontext_objects':{'images':0,'curves':0,'rects':0,'lines':0}}
    q=assess_page(page,source_pdf_sha256='a'*64,package_pdf_sha256='a'*64,independent_text=text,
                  source_geometry=geometry,native_font_evidence=route)
    assert q['components']['language']['state']=='unknown'and q['outcome']=='repair_required'


@pytest.mark.parametrize('font',['NotDevnagari-ChanakyaBoldA','Devnagari-ChanakyaBoldA-copy','Times-Bold'])
def test_font_substrings_do_not_invent_a_decode_or_route(font):
    assert not native_font_script_evidence([{'fontname':font,'text':'CERC'}],load_policy())['required_scripts']
