"""Bounded source-raster/PDFium tight-box ownership, not estimated-grid repair.

Native/font-metric overhang is separated from actual source ink crossing a cell.
No padding, approximate text matching, source edit or model vote chooses a cell.
"""
import hashlib,json,math
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter,defaultdict

def sha(value):return hashlib.sha256(value).hexdigest()
def compact(value):return ''.join(c for c in value if not c.isspace())
def intersects(a,b):return min(a[2],b[2])>max(a[0],b[0])and min(a[3],b[3])>max(a[1],b[1])
def contained(a,b):return b[0]<=a[0]<=a[2]<=b[2]and b[1]<=a[1]<=a[3]<=b[3]
def intersection(a,b):return[max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])]
def outside_parts(a,b):
 i=intersection(a,b)
 if not intersects(a,b):return[a]
 return[r for r in([a[0],a[1],a[2],i[1]],[a[0],i[3],a[2],a[3]],[a[0],i[1],i[0],i[3]],[i[2],i[1],a[2],i[3]])if r[0]<r[2]and r[1]<r[3]]
def pixels(box,scale,width,height):
 return(max(0,math.floor(box[0]*scale)),max(0,math.floor(box[1]*scale)),min(width,math.ceil(box[2]*scale)),min(height,math.ceil(box[3]*scale)))
def ink_count(ink,box,scale):
 x0,y0,x1,y1=pixels(box,scale,ink.shape[1],ink.shape[0]);return int(ink[y0:y1,x0:x1].sum())if x0<x1 and y0<y1 else 0

def visible_source_color(color):
 if isinstance(color,(int,float)):return 0<=color<1
 return isinstance(color,(list,tuple))and len(color)==3 and all(isinstance(c,(int,float))and math.isfinite(c)and 0<=c<=1 for c in color)and any(c<1 for c in color)

def supported_contexts(drawings,box,policy):
 for drawing in drawings:
  if drawing.get('layer'):return False
  if drawing['type']=='clip':
   if not drawing.get('rectangular_path')or drawing.get('rect')is None or not contained(box,drawing['rect']):return False
  elif drawing['type']=='group':
   if drawing.get('opacity')!=policy['required_group_opacity']or drawing.get('blendmode')not in policy['allowed_group_blend_modes']or drawing.get('knockout')is not False:return False
 return True

_PAGE_SOURCE_CELLS=ContextVar('pdf2data_page_source_cells',default=None)


def _source_stat(path):
 value=Path(path).stat();return(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


@contextmanager
def page_source_cell_context(source,page_number,native_page,source_sha,cells,*,pdfium_page=None,text_page=None,third_layout=None):
 """Trusted decoder-local complete raw grid; never deduplicate variants."""
 from .proof_identity import inspect_pdf2data_profile
 source=Path(source).resolve();before=_source_stat(source)
 if sha(source.read_bytes())!=source_sha:raise ValueError('Page context source identity changed')
 root=Path(__file__).resolve().parents[2];profile=inspect_pdf2data_profile(root)['profile_sha256']
 by_box=defaultdict(list)
 for cell in cells:by_box[tuple(cell['bbox'])].append(cell)
 context={'source':source,'page':page_number,'native_page':native_page,'source_sha256':source_sha,'before':before,
          'cells':list(cells),'by_box':by_box,'profile':profile,'prepared':None,
          'pdfium_page':pdfium_page,'text_page':text_page,'third_layout':third_layout}
 token=_PAGE_SOURCE_CELLS.set(context)
 try:yield context
 finally:
  _PAGE_SOURCE_CELLS.reset(token)
  context['prepared']=None # Release page raster/labels before the next page.
  if _source_stat(source)!=before or sha(source.read_bytes())!=source_sha:raise ValueError('Original source changed during page context')
  if inspect_pdf2data_profile(root)['profile_sha256']!=profile:raise ValueError('Page context proof code or policy changed')


def _complete_source_cells(native_page,source,page_number):
 context=_PAGE_SOURCE_CELLS.get()
 if context is not None and context['source']==Path(source).resolve()and context['page']==page_number and context['native_page']is native_page:
  if _source_stat(source)!=context['before']:raise ValueError('Page context source changed')
  return context['cells'],context['by_box']
 from .native_tables import find_native_tables,table_shape
 cells=[]
 for index,table in enumerate(find_native_tables(native_page)):
  values=table.extract()
  if values:cells.extend(table_shape(table,values,'tight-cell-probe:'+str(index))['cell_spans'])
 by_box=defaultdict(list)
 for cell in cells:by_box[tuple(cell['bbox'])].append(cell)
 return cells,by_box


def raw_annotation_presence(page):
 """Do not recursively resolve annotation Parent/page cycles.

 Only a missing/null entry or a direct empty PDF array is known empty.
 Indirect references and all other values conservatively prevent ownership;
 their evidence remains in the byte-bound original PDF.
 """
 attrs=getattr(getattr(page,'page_obj',None),'attrs',None)
 if not isinstance(attrs,dict):
  return {'has_annotations':True,'raw_type':'unavailable_page_attributes','inspection_basis':'raw_Annots_presence_no_resolution','annotation_evidence_preserved_in_original_PDF':True}
 raw=attrs.get('Annots')
 empty=raw is None or(type(raw)is list and len(raw)==0)
 return {'has_annotations':not empty,'raw_type':type(raw).__name__,
         'direct_array_length':len(raw)if type(raw)is list else None,
         'inspection_basis':'raw_Annots_presence_no_resolution',
         'annotation_evidence_preserved_in_original_PDF':True}


def _prepare_ownership_page(source,page_number,page,other,tp,third):
 import numpy as np,cv2
 from importlib.metadata import version
 source=Path(source);source_sha=sha(source.read_bytes());dpi=240
 from .native_paint_visibility import capture,opaque_fills,classify_glyphs,rules as paint_rules
 paint=capture(source,page_number);policy=paint_rules()['rules']['later_opaque_rectangular_fill'];fills,unsupported=opaque_fills(paint['drawings'],policy);visibility=classify_glyphs(paint['glyphs'],fills,policy)
 if page.rotation or other.get_rotation()or tuple(page.bbox[:2])!=(0,0)or tuple(other.get_bbox()[:2])!=(0,0)or abs(page.width-other.get_width())>.001 or abs(page.height-other.get_height())>.001:raise ValueError('Tight ownership requires proven unrotated original coordinate frame')
 annotation_guard=raw_annotation_presence(page)
 image=page.to_image(resolution=dpi).original.convert('RGB');array=np.asarray(image);ink=(array<255).any(axis=2);scale=dpi/72;png=__import__('io').BytesIO();image.save(png,format='PNG');raster_sha=sha(png.getvalue());characters=[]
 by_text=defaultdict(list)
 for i,g in enumerate(page.chars):by_text[g.get('text')].append((i,g))
 for index in range(tp.count_chars()):
  text=tp.get_text_range(index,1)
  if not text.strip():continue
  try:
   tight=tp.get_charbox(index);loose=tp.get_charbox(index,loose=True)
  except Exception:continue
  tight=[tight[0],page.height-tight[3],tight[2],page.height-tight[1]];loose=[loose[0],page.height-loose[3],loose[2],page.height-loose[1]]
  matches=[(i,g)for i,g in by_text[text]if g['x0']<=(loose[0]+loose[2])/2<=g['x1']and g['top']<=(loose[1]+loose[3])/2<=g['bottom']]
  characters.append({'pdfium_index':index,'text':text,'tight_bbox':tight,'loose_bbox':loose,'native_matches':[i for i,g in matches]})
 # Exact connected source ink, without expanding glyph/cell rectangles.
 # A component is owned only when its ink pixels have one tight-glyph anchor.
 _,labels,component_stats,_=cv2.connectedComponentsWithStats(ink.astype('uint8'),8)
 component_owners=defaultdict(set);character_components={}
 for char in characters:
  t=char['tight_bbox'];x0=max(0,math.ceil(t[0]*scale-.5));y0=max(0,math.ceil(t[1]*scale-.5));x1=min(ink.shape[1],math.ceil(t[2]*scale-.5));y1=min(ink.shape[0],math.ceil(t[3]*scale-.5))
  ids=[int(i)for i in np.unique(labels[y0:y1,x0:x1])if i]if x0<x1 and y0<y1 else[]
  character_components[char['pdfium_index']]=ids
  for component in ids:component_owners[component].add(char['pdfium_index'])
 return {'page':page,'other':other,'tp':tp,'third':third,'source_sha':source_sha,'page_number':page_number,
         'policy':policy,'paint':paint,'fills':fills,'unsupported':unsupported,'visibility':visibility,
         'ink':ink,'scale':scale,'raster_sha':raster_sha,'characters':characters,
         'component_owners':component_owners,'character_components':character_components,
         'labels':labels,'component_stats':component_stats,'annotation_guard':annotation_guard,
         'paint_snapshot_sha256':sha(json.dumps(paint,sort_keys=True,separators=(',',':')).encode()),
         'implementation_sha256':sha(Path(__file__).read_bytes()),
         'policy_sha256':sha(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()),
         'reader_versions':{name:version(name)for name in('pdfplumber','pypdf','pypdfium2','PyMuPDF')}}


def _audit_prepared_cells(prepared,cells):
 import numpy as np,copy
 from .native_consensus import agrees
 from .native_table_cell_spacing import read_source_box
 page,other,tp,third=(prepared[k]for k in('page','other','tp','third'));source_sha=prepared['source_sha'];page_number=prepared['page_number'];dpi=240
 policy,paint,fills,unsupported,visibility=(prepared[k]for k in('policy','paint','fills','unsupported','visibility'))
 ink,scale,raster_sha,characters=(prepared[k]for k in('ink','scale','raster_sha','characters'))
 component_owners,character_components,labels,component_stats,annotation_guard=(prepared[k]for k in('component_owners','character_components','labels','component_stats','annotation_guard'))
 reports=[]
 for cell in cells:
  box=cell['bbox'];owned=[];excluded=[];crossing=[];unmatched=[]
  raw_box=[box[0]+.2,box[1]+.2,box[2]-.2,box[3]-.2]
  fresh_raw,legacy_rectangle=read_source_box(page,other,tp,raw_box)
  raw_reproduced=cell.get('independent_raw')is None or cell.get('independent_raw')==fresh_raw
  for char in characters:
   tight=char['tight_bbox']
   if not intersects(char['loose_bbox'],box)and not intersects(tight,box):continue
   if len(char['native_matches'])!=1:
    if intersects(tight,box):unmatched.append(char)
    continue
   decision='outside'
   if contained(tight,box):decision='inside'
   elif intersects(tight,box):
    inside_pixels=ink_count(ink,intersection(tight,box),scale);outside_pixels=sum(ink_count(ink,r,scale)for r in outside_parts(tight,box))
    char={**char,'inside_intersection_nonwhite_pixels':inside_pixels,'outside_intersection_nonwhite_pixels':outside_pixels}
    if not inside_pixels:decision='outside'
    elif not outside_pixels:decision='inside'
    else:decision='crosses_actual_ink'
   (owned if decision=='inside'else crossing if decision=='crosses_actual_ink'else excluded).append(char)
  # Distinct PDFium characters must not claim one native source object twice.
  native_ids=[c['native_matches'][0]for c in owned];duplicate_native=len(native_ids)!=len(set(native_ids))
  native_owners=[page.chars[i]for i in native_ids];native_text=''.join(g['text']for g in native_owners)
  indices=sorted(c['pdfium_index']for c in owned);runs=[]
  for index in indices:
   if not runs:runs.append([index,index])
   else:
    a=runs[-1][1]+1;between=tp.get_text_range(a,index-a)if index>a else''
    if not between.strip():runs[-1][1]=index
    else:runs.append([index,index])
  readings=[{'char_start':a,'char_count':b-a+1,'exact_quote':tp.get_text_range(a,b-a+1),'offset_basis':'PDFium character array, not page-codepoint offsets'}for a,b in runs];mapped=''.join(r['exact_quote']for r in readings)
  selected=cell.get('selected',cell.get('text',''));plain=compact(selected);flat=compact(third);locations=[]
  if plain:
   start=0
   while True:
    index=flat.find(plain,start)
    if index<0:break
    locations.append(index);start=index+1
  # Coverage is conservative: source paths, image ink or true boundary bleed
  # not covered by uniquely owned tight character envelopes stay unresolved.
  mask=np.zeros(ink.shape,dtype=bool);component_proofs=[];ambiguous_components=set();component_boundary_crossings=set();recorded_components=set();owned_indices={c['pdfium_index']for c in owned}
  for char in owned:
   for component in character_components[char['pdfium_index']]:
    if not component_owners[component]or not component_owners[component]<=owned_indices:ambiguous_components.add(component);continue
    if component in recorded_components:continue
    x,y,w,h,area=map(int,component_stats[component]);component_box=[x/scale,y/scale,(x+w)/scale,(y+h)/scale]
    if not contained(component_box,box):component_boundary_crossings.add(component);continue
    actual=labels[y:y+h,x:x+w]==component;mask[y:y+h,x:x+w]|=actual;recorded_components.add(component)
    component_proofs.append({'component_id':component,'source_pixel_bbox':[x,y,x+w,y+h],'ink_pixels':area,'relative_ink_mask_sha256':sha(np.packbits(actual).tobytes()),'pdfium_character_cluster':sorted(component_owners[component]),'ownership_basis':'whole connected source ink cluster has all glyph anchors inside this cell; no per-character pixel split inferred','source_point_bbox':component_box})
  x0,y0,x1,y1=pixels(box,scale,ink.shape[1],ink.shape[0]);unknown=int((ink[y0:y1,x0:x1]&~mask[y0:y1,x0:x1]).sum());agreement=agrees(selected,mapped,exact_characters=True);native_conserved=Counter(compact(native_text))==Counter(plain)
  paint_holds=[{'glyph_key':g['glyph_key'],'state':g['visibility_state']}for g in visibility if g['text'].strip()and intersects(g['bbox'],box)and(g['visibility_state']!='retained_visible_or_unproven'or not visible_source_color(g.get('color')))]
  unsupported_context=bool(unsupported or not supported_contexts(paint['drawings'],box,policy))
  overlapping_nontext=[]
  for kind in('images','curves'):
   overlapping_nontext.extend({'kind':kind,'bbox':[obj[k]for k in('x0','top','x1','bottom')]}for obj in getattr(page,kind,[])if intersects([obj[k]for k in('x0','top','x1','bottom')],box))
  # Non-text source paint cannot be relabelled as a glyph merely because
  # connected ink touches one glyph box. Preserve these objects as holds.
  overlapping_nontext.extend({'kind':'nonwhite_source_fill','bbox':fill['effective_bbox'],'paint_seqno':fill['seqno']}for fill in fills if fill['fill']!=[1.,1.,1.]and intersects(fill['effective_bbox'],box))
  for kind,objects in [('stroke_line',page.lines),('stroke_rect',[r for r in page.rects if r.get('stroke')is not False])]:
   for obj in objects:
    if not all(key in obj for key in('x0','top','x1','bottom')):continue
    b=[obj[key]for key in('x0','top','x1','bottom')]
    if max(b[0],box[0])<=min(b[2],box[2])and max(b[1],box[1])<=min(b[3],box[3]):overlapping_nontext.append({'kind':kind,'bbox':b})
  if annotation_guard['has_annotations']:overlapping_nontext.append({'kind':'unresolved_raw_annotation_presence','bbox':list(box),'raw_annotation_guard':annotation_guard})
  invisible_owned=[c['pdfium_index']for c in owned if ink_count(ink,c['tight_bbox'],scale)==0]
  strict_geometry=not(crossing or unmatched or duplicate_native or unknown or paint_holds or unsupported_context or overlapping_nontext or invisible_owned or not raw_reproduced or ambiguous_components or component_boundary_crossings)
  status='verified_reader_ownership'if strict_geometry and agreement and len(locations)==1 and native_conserved else'held_geometry_or_reader_order'
  reports.append({'source_pdf_sha256':source_sha,'page':page_number,'cell_id':cell.get('id'),'row':cell.get('row'),'column':cell.get('column'),'bbox':box,'selected_raw':selected,'raw_bounded_reader':fresh_raw,'legacy_bounded_reader_rectangle':legacy_rectangle,'legacy_reader_input_reproduced':raw_reproduced,'mapped_reader_candidate':mapped,'pdfium_source_ranges':readings,'unique_third_compact_contexts':len(locations),'owned_characters':copy.deepcopy(owned),'excluded_metric_overhang_characters':copy.deepcopy(excluded),'actual_ink_crossings':crossing,'unmatched_source_characters':unmatched,'duplicate_native_owner':duplicate_native,'unaccounted_cell_ink_pixels':unknown,'unique_source_ink_components':component_proofs,'ambiguous_ink_components':sorted(ambiguous_components),'ink_components_crossing_cell_boundary':sorted(component_boundary_crossings),'strict_geometry_ownership_verified':strict_geometry,'owned_glyphs_without_visible_ink':invisible_owned,'source_paint_holds':paint_holds,'raw_annotation_guard':annotation_guard,'unsupported_paint_or_clipping_context':unsupported_context,'overlapping_images_or_curves':overlapping_nontext,'native_owned_glyph_indices':native_ids,'source_paint_snapshot_sha256':prepared['paint_snapshot_sha256'],'selected_reader_word_number_operator_agreement':agreement,'native_literal_character_multiset_conserved':native_conserved,'status':status,'source_raster_sha256':raster_sha,'dpi':dpi,'raster_pixel_rule':'any RGB channel below255; 8-connected source RGB ink; unique tight-glyph pixel-center anchor; whole component inside original cell; no mask or read-rectangle padding','implementation_sha256':prepared['implementation_sha256'],'paint_policy_sha256':prepared['policy_sha256'],'reader_versions':prepared['reader_versions'],'source_text_modified':False,'source_quality_page_cleared':False,'legal_publication':False})
 return reports


def audit_cells(source,page_number,cells,*,dpi=240):
 """One live source page context; no source/quality rule is bypassed."""
 if type(dpi)is not int or dpi!=240:raise ValueError('Tight ownership uses the fixed240DPI evidence contract')
 import pdfplumber,pypdfium2 as pdfium
 from pypdf import PdfReader
 source=Path(source).resolve();before=_source_stat(source);context=_PAGE_SOURCE_CELLS.get()
 if context is not None and context['source']==source and context['page']==page_number and context['pdfium_page']is not None and context['text_page']is not None:
  if before!=context['before']:raise ValueError('Page ownership context source changed')
  if context['prepared']is None:context['prepared']=_prepare_ownership_page(source,page_number,context['native_page'],context['pdfium_page'],context['text_page'],context['third_layout'])
  reports=_audit_prepared_cells(context['prepared'],cells)
 else:
  third=PdfReader(source).pages[page_number-1].extract_text(extraction_mode='layout')or''
  with pdfplumber.open(source)as native,pdfium.PdfDocument(source)as independent:
   page=native.pages[page_number-1];other=independent[page_number-1];tp=other.get_textpage()
   try:reports=_audit_prepared_cells(_prepare_ownership_page(source,page_number,page,other,tp,third),cells)
   finally:tp.close();other.close()
 if _source_stat(source)!=before:raise ValueError('Original source changed during tight ownership audit')
 return reports


def try_owned_cell_reader(native_page,pdfium_page,text_page,box,selected,raw,third,original_proof):
    """Restricted recovery for literal-identical reader text +metric ghosts.

    Whole original source glyphs and raw readings remain in the returned proof.
    Any actual crossing, unsupported paint, missing ink, wrong order, changed
    literal or ambiguous independent layout keeps the original hold.
    """
    if not selected or compact(selected)!=compact(raw):return None
    source_name=getattr(getattr(getattr(native_page,'pdf',None),'stream',None),'name',None)
    if not source_name or not Path(source_name).is_file():return None
    # Reject the same ambiguous geometry before any expensive paint/raster.
    source_cells,by_box=_complete_source_cells(native_page,Path(source_name),native_page.page_number)
    matched=by_box.get(tuple(box),[])
    if len(matched)!=1 or any(cell is not matched[0]and intersects(cell['bbox'],box)for cell in source_cells):return None
    report=audit_cells(source_name,native_page.page_number,[{'bbox':list(box),'selected':selected,'independent_raw':raw}])[0]
    if not report['strict_geometry_ownership_verified']or not report['native_literal_character_multiset_conserved']or report['unique_third_compact_contexts']!=1:return None
    report['fresh_nonoverlapping_cell_geometry_verified']=True
    from types import SimpleNamespace
    from .native_table_cell_spacing import map_cell_reader
    from .native_consensus import agrees
    ids=set(report['native_owned_glyph_indices'])
    owned=[glyph for index,glyph in enumerate(native_page.chars)if index in ids or(glyph.get('text','').isspace()and box[0]<=(glyph['x0']+glyph['x1'])/2<=box[2]and box[1]<=(glyph['top']+glyph['bottom'])/2<=box[3])]
    if compact(''.join(glyph['text']for glyph in owned))!=compact(selected):return None
    keys={(g['text'],g['x0'],g['top'],g['x1'],g['bottom'])for g in owned}
    filtered=native_page.filter(lambda obj:obj.get('object_type')!='char'or(obj['text'],obj['x0'],obj['top'],obj['x1'],obj['bottom'])in keys)
    cropped=filtered.crop(tuple(box));cell_view=SimpleNamespace(chars=owned,extract_text_lines=cropped.extract_text_lines)
    proxy=SimpleNamespace(width=native_page.width,height=native_page.height,rotation=native_page.rotation,bbox=native_page.bbox,chars=owned,crop=lambda region:cell_view)
    nested=map_cell_reader(proxy,pdfium_page,text_page,box,selected,report['mapped_reader_candidate'],third,_allow_tight_ownership=False)
    if not agrees(selected,nested['mapped_text'],exact_characters=True):return None
    repair={'kind':'source_tight_glyph_ownership_reader_revalidation','raw_text':raw,'corrected_text':nested['mapped_text'],'ownership_proof':report,'owned_reader_repairs':nested['repairs'],'selected_text_modified':False,'outside_glyphs_preserved_in_original_page':True}
    return {**original_proof,'mapped_text':nested['mapped_text'],'mapped_sha256':sha(nested['mapped_text'].encode()),'owned_native_glyphs_sha256':nested['native_glyphs_sha256'],'repairs':[repair],'status':'source_proved'}


def verify_ownership_report(report,source):
    """Fresh original raster/reader derivation; a sealed JSON edit is insufficient."""
    fresh=audit_cells(source,report['page'],[{'id':report.get('cell_id'),'row':report.get('row'),'column':report.get('column'),'bbox':report['bbox'],'selected':report['selected_raw'],'independent_raw':report['raw_bounded_reader']}],dpi=report['dpi'])[0]
    if fresh!=report:raise ValueError('Tight ownership receipt differs from fresh original source')
    return {'status':'verified_observation','ownership_state':fresh['status'],'source_quality_page_cleared':False,'publication_eligible':False}
