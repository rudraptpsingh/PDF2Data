from local_vector_batch.text_script import classify_text_script


def test_text_script_is_observed_not_ocr_quality():
    assert classify_text_script('भारत 2026')['text_script'] == 'devanagari'
    assert classify_text_script('CERC 2026')['text_script'] == 'latin'
    assert classify_text_script('CERC भारत')['text_script'] == 'mixed'
    assert classify_text_script('2026/05')['text_script'] == 'unknown'
