from local_vector_batch.l1_review import native_focus_boxes


def test_native_crop_uses_equal_source_alignment_and_never_guesses_missing_text():
    raw={'alignment':[{'kind':'equal','glyph_start':0,'glyph_end':2,'selected_start':0,'selected_end':2}],
        'glyphs':[{'text':'A','bbox':[10,10,20,20]},{'text':'B','bbox':[21,10,31,20]}],'width':100,'height':100}
    assert native_focus_boxes(raw,0,1)
    assert native_focus_boxes(raw,5,7)==[]
