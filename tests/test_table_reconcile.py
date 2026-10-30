import copy
from local_vector_batch.table_reconcile import native_table_proof


def example():
    table={'extraction_basis':'native_pdf_geometry','bbox':[0,0,20,20],'cells':[['A','B'],['1','2']], 'cell_spans':[]}
    glyphs=[]
    for r,row in enumerate(table['cells']):
        for c,text in enumerate(row):
            box=[c*10,r*10,c*10+10,r*10+10]
            table['cell_spans'].append({'row':r,'column':c,'row_span':1,'column_span':1,'text':text,'bbox':box})
            glyphs.append({'text':text,'bbox':[c*10+2,r*10+2,c*10+8,r*10+8]})
    return table,{'bbox':[0,0,20,20]},{'glyphs':glyphs,'native_text_raw':'A B 1 2','secondary_text_raw':'AB12'},{'method':'pdf_text','independent_coverage':{'char_recall':1,'digit_recall':1}}


def test_source_native_cells_require_original_geometry_and_every_glyph():
    t,r,l,p=example();assert native_table_proof(t,r,l,p)[0]
    # Same total text, wrong cell locations: global inventory alone is unsafe.
    l['glyphs'][2]['bbox']=[12,12,18,18]
    assert native_table_proof(t,r,l,p)==(False,'native_cell_glyph_inventory_differs')


def test_wrong_roi_corrupt_glyph_missing_decoder_or_ocr_projection_never_replaces():
    t,r,l,p=example();r['bbox']=[0,0,100,100];assert not native_table_proof(t,r,l,p)[0]
    t,r,l,p=example();l['secondary_text_raw']='A B 1';assert native_table_proof(t,r,l,p)[1]=='fresh_native_decoders_disagree'
    t,r,l,p=example();t['cells'][1][0]='(cid:123)';t['cell_spans'][2]['text']='(cid:123)';assert native_table_proof(t,r,l,p)[1]=='unresolved_native_glyphs'
    t,r,l,p=example();p['method']='vision';assert not native_table_proof(t,r,l,p)[0]


def test_unmapped_glyph_inside_visual_region_prevents_native_selection():
    t,r,l,p=example();l['glyphs'].append({'text':'missing','bbox':[1,1,3,3]})
    assert native_table_proof(t,r,l,p)==(False,'visual_region_glyph_inventory_differs')


def test_same_digits_in_different_order_cannot_pass_source_cell_proof():
    t,r,l,p=example();t['cells'][1]=['21','X'];t['cell_spans'][2]['text']='21';t['cell_spans'][3]['text']='X'
    l['native_text_raw']=l['secondary_text_raw']='AB12X'
    l['glyphs']=l['glyphs'][:2]+[{'text':'1','bbox':[1,12,4,18]},{'text':'2','bbox':[5,12,8,18]},{'text':'X','bbox':[12,12,18,18]}]
    assert native_table_proof(t,r,l,p)==(False,'native_cell_glyph_order_differs')


def test_visual_reading_prevents_common_font_decode_errors():
    from local_vector_batch.table_reconcile import visual_content_corroborates
    t,r,l,p=example();t['cells'][1]=['_','1Z'];t['cell_spans'][2]['text']='_';t['cell_spans'][3]['text']='1Z'
    model={'cells':[['A','B'],['-','27']]}
    assert not visual_content_corroborates(t,[model])[0]
    t,r,l,p=example();t['cell_spans'][0]['text']='~';t['cells'][0][0]='~'
    assert not visual_content_corroborates(t,[{'cells':[['→','B'],['1','2']]}])[0]


def test_geometry_can_repair_split_headers_only_with_visual_value_support():
    from local_vector_batch.table_reconcile import visual_content_corroborates
    t,r,l,p=example();t['cell_spans'][0]['role_candidate']='header';t['cell_spans'][0]['text']='RE Generator';t['cells'][0][0]='RE Generator'
    t['cell_spans'][1]['role_candidate']='header'
    assert visual_content_corroborates(t,[{'cells':[['Generator','RE','B'],['1','2','2']]}])[0]
    assert not visual_content_corroborates(t,[{'cells':[['Generator','RE','B'],['1','3','3']]}])[0]


def test_case_sensitive_units_are_not_normalized_into_different_values():
    from local_vector_batch.table_reconcile import visual_content_corroborates
    t,r,l,p=example();t['cells'][1]=['10 MW','20 MW'];t['cell_spans'][2]['text']='10 MW';t['cell_spans'][3]['text']='20 MW'
    assert not visual_content_corroborates(t,[{'cells':[['A','B'],['10 mW','20 mW']]}])[0]
    t,r,l,p=example();t['cells'][0][0]='MW';t['cell_spans'][0]['text']='MW';t['cell_spans'][0]['role_candidate']='header'
    assert not visual_content_corroborates(t,[{'cells':[['mW','B'],['1','2']]}])[0]
