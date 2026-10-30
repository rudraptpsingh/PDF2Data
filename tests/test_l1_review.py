from local_vector_batch.l1_review import native_focus_boxes


def test_native_crop_uses_equal_source_alignment_and_never_guesses_missing_text():
    raw={'alignment':[{'kind':'equal','glyph_start':0,'glyph_end':2,'selected_start':0,'selected_end':2}],
        'glyphs':[{'text':'A','bbox':[10,10,20,20]},{'text':'B','bbox':[21,10,31,20]}],'width':100,'height':100}
    assert native_focus_boxes(raw,0,1)
    assert native_focus_boxes(raw,5,7)==[]


def test_cell_feedback_is_scoped_to_the_exact_illustration_and_readings(tmp_path,monkeypatch):
    import pytest
    import local_vector_batch.review_queue as queue
    from local_vector_batch.l1_review import record_cell_decision
    cell={'id':'t2:r1:c7','row':1,'column':7,'bbox':[1,2,3,4],'text':'Not effective','independent_text':'Not effective','selected_cell_sha256':'s','independent_cell_sha256':'i'}
    item={'source_pdf_sha256':'pdf','page':5,'table_previews':[{'table_version_id':'t2','caption':'Illustration 2','cell_spans':[cell]}]}
    monkeypatch.setattr(queue,'load_item',lambda *args:item)
    request={'index':7,'source_pdf_sha256':'pdf','page':5,'table_version_id':'t2','cell_id':cell['id'],'selected_cell_sha256':'s','independent_cell_sha256':'i','decision':'selected'}
    out=tmp_path/'cell-feedback.jsonl'
    r=record_cell_decision(tmp_path/'unused',out,request)
    assert r['table_caption']=='Illustration 2' and r['selected_text']=='Not effective' and not r['promotes_page']
    with pytest.raises(ValueError,match='illustration'):
        record_cell_decision(tmp_path/'unused',out,{**request,'table_version_id':'t1'})
    with pytest.raises(ValueError,match='readings changed'):
        record_cell_decision(tmp_path/'unused',out,{**request,'selected_cell_sha256':'stale'})
    with pytest.raises(ValueError,match='corrected value'):
        record_cell_decision(tmp_path/'unused',out,{**request,'decision':'corrected'})


def test_native_focus_keeps_complete_line_and_excludes_partial_neighbour():
    raw={'alignment':[{'kind':'equal','glyph_start':0,'glyph_end':3,'selected_start':0,'selected_end':3}],
        'glyphs':[{'text':'A','bbox':[10,10,20,20]},{'text':'B','bbox':[21,10,31,20]},{'text':'C','bbox':[10,30,20,40]}],'width':100,'height':100}
    box=native_focus_boxes(raw,1,2)[0]
    assert box[0]<=.1 and box[2]>=.31
    assert box[1]>.09 and box[3]<.21
