import numpy as np
from local_vector_batch.region_recovery import split_pixels,text_rows


def test_partition_conserves_all_pixels_with_whitespace_context():
    ink=np.zeros((200,200),dtype=bool)
    for y in range(10,190,20):ink[y:y+8,10:180]=True
    parts=split_pixels(ink,max_rows=3)
    assert len(parts)==3
    counts=np.zeros(ink.shape,dtype=int)
    for x0,y0,x1,y1 in parts:counts[y0:y1,x0:x1]+=1
    assert np.all(counts==1)
    for _,y,_,_ in parts[1:]:assert not ink[y-2:y+2].any()


def test_two_printed_columns_are_read_column_first_without_cutting_glyphs():
    ink=np.zeros((200,600),dtype=bool)
    for y in range(10,190,20):ink[y:y+8,10:285]=True;ink[y:y+8,315:590]=True
    parts=split_pixels(ink,max_rows=4)
    assert len(parts)==6 and parts[0][2]==parts[3][0]==300
    assert parts[2][3]==200 and parts[3][1]==0


def test_dense_unseparated_region_is_not_arbitrarily_cut():
    assert split_pixels(np.ones((200,600),dtype=bool))==[[0,0,600,200]]


def test_skewed_column_profiles_need_not_have_full_width_row_gaps():
    ink=np.zeros((300,1000),dtype=bool)
    for x0,x1 in ((0,480),(520,1000)):
        for x in range(x0,x1):
            for y in range(0,280,25):
                top=y+(x-x0)//20
                ink[top:min(300,top+7),x]=True
    assert len(text_rows(ink))<8
    parts=split_pixels(ink)
    assert parts==[[0,0,500,300],[500,0,1000,300]]


def test_short_failed_handwriting_splits_at_whitespace_without_column_guess():
    ink=np.zeros((600,1600),dtype=bool)
    for y in (20,150,280,410):ink[y:y+80,20:1580]=True
    assert split_pixels(ink)==[[0,0,1600,255],[0,255,1600,600]]


def test_part_limit_and_table_column_guard_preserve_original_area():
    ink=np.zeros((200,600),dtype=bool)
    for y in range(10,190,20):ink[y:y+8,10:285]=True;ink[y:y+8,315:590]=True
    assert split_pixels(ink,max_rows=1,max_parts=2)==[[0,0,600,200]]
    parts=split_pixels(ink,max_rows=4,allow_columns=False)
    assert all(box[0]==0 and box[2]==600 for box in parts)
