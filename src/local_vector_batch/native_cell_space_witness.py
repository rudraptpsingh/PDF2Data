"""Restore a reader's omitted explicit source-space glyph, never inferred gaps."""
import hashlib,json,re
from pathlib import Path

def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def load_rules():
 p=Path(__file__).with_name('native_cell_space_rules.json');rule=json.loads(p.read_text())
 if rule.get('schema_version')!='pdf2data.native-cell-space-rules.v1':raise ValueError('Unknown source-space proof policy')
 return rule,hashlib.sha256(p.read_bytes()).hexdigest()
def compact(text):return ''.join(c for c in text if not c.isspace())
def boundaries(text):
 result={};count=0
 for match in re.finditer(r'\s+|\S',text):
  raw=match.group()
  if raw.isspace():result[count]=raw
  else:count+=1
 return result

def restore_explicit_source_spaces(selected,raw,glyphs,third):
 """All literal characters/order must agree; add only source-witnessed spaces.

    Native explicit U+0020/U+00A0 objects and unique pypdf context must both
    retain the same separators that the PDFium reader omitted. Width alone,
    dictionary words, model votes and neighboring cells cannot authorize this.
    """
 rule,rule_sha=load_rules();plain=compact(selected)
 if not plain or plain!=compact(raw)or plain!=compact(''.join(g['text']for g in glyphs)):return None
 wanted=boundaries(selected);present=boundaries(raw);missing=sorted(k for k in wanted if 0<k<len(plain)and k not in present)
 if not missing or any(k not in wanted for k in present if 0<k<len(plain)):return None
 flat=compact(third);positions=[i for i,c in enumerate(third)if not c.isspace()];hits=[m.start()for m in re.finditer(re.escape(plain),flat)]
 if len(hits)!=1 or len(plain)>rule['maximum_context_codepoints']:return None
 anchor=hits[0];source=[];nonwhite=[];before=0
 for index,g in enumerate(glyphs):
  for character in g['text']:
   if character.isspace():source.append((before,index,character,g))
   else:nonwhite.append((index,character,g));before+=1
 receipts=[];allowed=set(rule['allowed_source_space_codepoints']);tol=rule['coordinate_tolerance_points']
 for k in missing:
  # Cross-line joins, explicit source line endings and guessed layout gaps stay held.
  if any(c in '\r\n'for c in wanted[k]):return None
  left,right=nonwhite[k-1][2],nonwhite[k][2];height=min(left['bottom']-left['top'],right['bottom']-right['top']);overlap=min(left['bottom'],right['bottom'])-max(left['top'],right['top'])
  if height<=0 or overlap/height<rule['minimum_vertical_overlap_fraction']or right['x0']<left['x1']-tol:return None
  matches=[(i,c,g)for boundary,i,c,g in source if boundary==k and ord(c)in allowed]
  if not matches:return None
  for _,_,g in matches:
   center=(g['x0']+g['x1'])/2;em=max(g.get('size',g['bottom']-g['top']),1)
   if not(0<g['x1']-g['x0']<=rule['maximum_space_advance_width_em']*em and left['x1']-tol<=center<=right['x0']+tol):return None
  lo=positions[anchor+k-1]+1;hi=positions[anchor+k];independent=third[lo:hi]
  if not independent or not independent.isspace()or not any(ord(c)in allowed for c in independent):return None
  receipts.append({'nonwhitespace_boundary':k,'native_space_glyphs':[{'glyph_index':i,'codepoint':ord(c),'bbox':[g[v]for v in('x0','top','x1','bottom')]}for i,c,g in matches],'left_native_glyph_index':nonwhite[k-1][0],'right_native_glyph_index':nonwhite[k][0],'third_reader_span':[lo,hi],'third_reader_exact_separator':independent})
 raw_positions=[i for i,c in enumerate(raw)if not c.isspace()];corrected=raw
 for k in reversed(missing):
  index=raw_positions[k];corrected=corrected[:index]+rule['inserted_reader_separator']+corrected[index:]
 return {'kind':'explicit_source_space_glyph_reader_restoration','raw_text':raw,'corrected_text':corrected,'source_space_boundaries':receipts,'rule_configuration_sha256':rule_sha,'third_reader_sha256':sha(third),'selected_text_modified':False,'source_text_modified':False,'basis':'literal_native_space_glyph_and_unique_independent_layout_separator; no inferred word gap'}
