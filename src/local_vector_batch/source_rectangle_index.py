"""Exact conservative spatial candidates for immutable source rectangles.

Indexing never establishes coverage. Original predicates and order still decide;
large boxes fall back to the complete registry rather than being clipped.
"""
import math
from collections import defaultdict


def valid_box(box):
 if len(box)!=4 or any(not math.isfinite(v)for v in box)or box[0]>box[2]or box[1]>box[3]:raise ValueError('Invalid source geometry')
 return box


class SourceRectangleIndex:
 def __init__(self,boxes,*,tile_points=64,max_tiles=4096):
  if not math.isfinite(tile_points)or tile_points<=0 or type(max_tiles)is not int or max_tiles<1:raise ValueError('Invalid rectangle index capacity')
  self.boxes=list(boxes);self.tile=tile_points;self.maximum=max_tiles;self.buckets=defaultdict(list);self.large=[]
  for index,box in enumerate(self.boxes):
   valid_box(box);keys=self._keys(box)
   if keys is None:self.large.append(index)
   else:
    for key in keys:self.buckets[key].append(index)
 def _keys(self,box):
  x0,y0,x1,y1=valid_box(box)
  if x0==x1 or y0==y1:return []
  left,right=math.floor(x0/self.tile),math.floor(x1/self.tile);top,bottom=math.floor(y0/self.tile),math.floor(y1/self.tile)
  if(right-left+1)*(bottom-top+1)>self.maximum:return None
  return[(x,y)for x in range(left,right+1)for y in range(top,bottom+1)]
 def candidates(self,box):
  keys=self._keys(box)
  if keys is None:return list(range(len(self.boxes)))
  if not keys:return []
  found=set(self.large)
  for key in keys:found.update(self.buckets.get(key,()))
  return sorted(found) # Exact original paint/cell order, never spatial order.
