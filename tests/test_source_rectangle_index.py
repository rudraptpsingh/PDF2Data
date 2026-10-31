"""Spatial pruning must equal the complete exact paint registry."""
import copy,random
import pytest
from local_vector_batch.source_rectangle_index import SourceRectangleIndex
from local_vector_batch.native_paint_visibility import classify_glyphs,rules


def test_index_keeps_boundary_overlap_original_order_and_large_fallback():
 boxes=[[0,0,10,10],[64,0,128,64],[-20,-20,-1,-1],[-1e9,-1e9,1e9,1e9]];index=SourceRectangleIndex(boxes)
 assert index.candidates([63,1,65,2])==[0,1,3]
 assert index.candidates([-10,-10,-5,-5])==[2,3]
 assert index.candidates([-1e9,-1e9,1e9,1e9])==[0,1,2,3]


def test_invalid_rectangles_are_never_ignored_by_spatial_pruning():
 with pytest.raises(ValueError):SourceRectangleIndex([[0,0,float('nan'),10]])
 with pytest.raises(ValueError):SourceRectangleIndex([[10,0,0,10]])


def test_visibility_matches_full_registry_on_overlap_partial_gaps_order_and_whitespace():
 rng=random.Random(9271);fills=[]
 for n in range(300):
  x,y=rng.randrange(-100,600),rng.randrange(-100,900);fills.append({'seqno':n,'effective_bbox':[x,y,x+rng.randrange(1,130),y+rng.randrange(1,130)]})
 glyphs=[]
 for n in range(100):
  x,y=rng.randrange(-50,500),rng.randrange(-50,800);glyphs.append({'seqno':rng.randrange(300),'bbox':[x,y,x+12,y+18],'text':' 'if n%11==0 else'A','type':0,'opacity':1.,'layer':None,'direction':[1.,0.],'wmode':0,'ink_bounds_basis':'single_character_paint_bbox'})
 policy=rules()['rules']['later_opaque_rectangular_fill']
 assert classify_glyphs(glyphs,fills,policy)==classify_glyphs(glyphs,fills,policy,use_spatial_index=False)
