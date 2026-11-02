"""Engineering component controls; real original-source trials are separate."""
from copy import deepcopy
import pytest
from PIL import Image
from local_vector_batch import native_scoped_raster as m


def inputs():
    glyph=dict(text='A',bbox=[3.,13.,9.,22.],font='Helvetica',size=10.)
    invalid=dict(text='ु',bbox=[4.,1.,4.,8.],font='Mangal',size=10.)
    region=dict(id='body',page=1,bbox_pdf_points=[0.,10.,24.,34.],render_dpi=120,crop_bbox_pixels=[0,16,40,57])
    paint_glyph=dict(glyph,text='A',seqno=0,type=0,opacity=1.0,layer='',direction=[1.,0.],wmode=0,color=[0.,0.,0.])
    paint=dict(drawings=[],glyphs=[paint_glyph],paint_bbox_log=[dict(kind='fill-text',bbox=[2.,12.,10.,23.])])
    image=Image.new('RGB',(40,41),'white');image.putpixel((8,8),(0,0,0))
    fields=[dict(native_geometry=dict(bbox=glyph['bbox']),native_source_span=dict(page=1),native_field_handle={'json_pointer':'/observations/0'})]
    policy=dict(source_dark_pixel_threshold=180,ocr_box_padding_px=2)
    return region,[glyph,invalid],paint,image,fields,policy


def run(values):
    region,glyphs,paint,image,fields,policy=values
    return m.account(region,glyphs,deepcopy(glyphs),paint,image,fields,[],policy)


def test_disjoint_invalid_header_remains_held_while_crop_ink_component_is_proved():
    values=inputs();before=deepcopy(values[:-1]);result=run(values)
    assert result['source_object_raster_accounting']=='proved'
    assert result['unaccounted_ink_pixels']==0
    assert result['outside_invalid_glyph_regions'][0]['status']=='outside_held_invalid_geometry'
    assert result['native_fields']==values[4]
    assert all(result[k]=='unproved' for k in ('transcription','language','layout'))
    assert all(result[k] is False for k in ('page_quality_clearance','source_quality_approval_granted','publication_eligible','calculation_allowed'))
    assert values[:-1]==before


@pytest.mark.parametrize('mutation,reason',[
    ('invalid_inside','invalid_native_glyph_intersects_scope'),
    ('invalid_touching','invalid_native_glyph_intersects_scope'),
    ('unknown_location','unlocalizable_native_glyph_geometry'),
    ('clipped_glyph','scope_clips_native_glyph'),
    ('wrong_field_box','scope_does_not_bind_native_field_geometry'),
    ('wrong_field_page','scope_does_not_bind_native_field_geometry'),
    ('missing_field_geometry','scope_does_not_bind_native_field_geometry'),
    ('paint_boundary','scope_cuts_independent_text_paint'),
    ('hidden_text','unsupported_source_glyph_paint'),
    ('white_text','white_or_unsupported_source_text_color'),
    ('image','unsupported_source_paint_intersects_scope'),
    ('paint_text_disagreement','independent_source_object_glyph_inventory_differs'),
    ('extra_ink','unaccounted_source_ink_inside_scope'),
])
def test_wrong_or_unproved_region_components_stay_held(mutation,reason):
    v=inputs();region,glyphs,paint,image,fields,policy=v
    if mutation=='invalid_inside':glyphs[1]['bbox']=[4.,15.,4.,22.]
    elif mutation=='invalid_touching':glyphs[1]['bbox']=[4.,1.,4.,10.]
    elif mutation=='unknown_location':glyphs[1]['bbox'][0]=float('nan')
    elif mutation=='clipped_glyph':
        fields[0]['native_geometry']['bbox']=list(glyphs[0]['bbox']);glyphs[0]['bbox'][0]=-1
    elif mutation=='wrong_field_box':fields[0]['native_geometry']['bbox']=[30,12,40,20]
    elif mutation=='wrong_field_page':fields[0]['native_source_span']['page']=2
    elif mutation=='missing_field_geometry':fields[0]['native_geometry']=None
    elif mutation=='paint_boundary':paint['glyphs'][0]['bbox']=[3,9,9,22]
    elif mutation=='hidden_text':paint['glyphs'][0]['type']=3
    elif mutation=='white_text':paint['glyphs'][0]['color']=[1.,1.,1.]
    elif mutation=='image':paint['paint_bbox_log'].append(dict(kind='fill-image',bbox=[1.,11.,10.,20.]))
    elif mutation=='paint_text_disagreement':paint['glyphs'][0]['text']='B'
    elif mutation=='extra_ink':image.putpixel((35,35),(0,0,0))
    result=run(v)
    assert result['source_object_raster_accounting']=='held' and result['reason']==reason
    assert result['page_quality_clearance'] is False


def test_changed_fresh_source_geometry_is_an_identity_refusal():
    region,glyphs,paint,image,fields,policy=inputs();changed=deepcopy(glyphs);changed[0]['bbox'][0]+=1
    with pytest.raises(ValueError,match='fresh_source_glyph_inventory_differs'):
        m.account(region,glyphs,changed,paint,image,fields,[],policy)


def test_fresh_zero_ink_space_paints_never_authorize_advance_box_masks():
    values=inputs();region,glyphs,paint,image,fields,policy=values
    space=dict(paint['glyphs'][0],seqno=1,text=' ',bbox=[15.,13.,18.,22.])
    paint['glyphs'].append(space);paint['paint_bbox_log'].append(dict(kind='fill-text',bbox=[15.,20.,15.,20.]))
    result=run(values);assert result['source_object_raster_accounting']=='proved'
    assert result['zero_ink_source_spaces'][0]['mask_pixels_authorized']==0
    image.putpixel((27,8),(0,0,0))
    assert run(values)['reason']=='unaccounted_source_ink_inside_scope'
    paint['paint_bbox_log'][1]['bbox']=[15.,13.,18.,22.]
    assert run(values)['reason']=='source_paint_has_no_accountable_glyphs'


def test_native_table_scope_is_not_promoted_by_an_ink_mask():
    region,glyphs,paint,image,fields,policy=inputs()
    r=m.account(region,glyphs,deepcopy(glyphs),paint,image,fields,[[1,11,15,25]],policy)
    assert r['reason']=='native_table_intersects_scope' and r['source_object_raster_accounting']=='held'


def test_nonzero_or_cropped_frames_are_not_silently_projected():
    from types import SimpleNamespace
    native=SimpleNamespace(bbox=(0,0,600,800),cropbox=None,mediabox=(0,0,600,800))
    layout=dict(width=600,height=800);paint=dict(page_bbox=[0,0,600,800])
    m.require_frame(native,layout,paint)
    native.cropbox=(20,20,580,780)
    with pytest.raises(ValueError,match='source_frame_requires_separate_proof'):m.require_frame(native,layout,paint)


def test_registered_cli_interface_keeps_model_reading_commands_separate():
    from local_vector_batch.cli import parser
    args=parser().parse_args(['field-witness','native-raster','--request','r','--output','o','--json'])
    assert args.witness_operation=='native-raster'


@pytest.fixture
def source_bundle(tmp_path):
    from test_legal_l1 import source_fixture
    from local_vector_batch.legal_l1 import extract_document
    from local_vector_batch import field_rules as fields,field_witness as witness
    package=tmp_path/'package';extract_document(source_fixture(tmp_path),package)
    rules=dict(schema_version=fields.SCHEMA,fields_schema_version=fields.FIELDS_V2,id='component',version='1',
        fields=[dict(id='reply',type='text',pages=[1],pattern=r'(?P<value>1\. The Respondent may file its reply within two weeks\.)')])
    fields.write(tmp_path/'fields.rules.json',rules);fields.extract(package,tmp_path/'fields.rules.json',tmp_path/'fields')
    geometry=witness.load(tmp_path/'fields/fields.json')['observations'][0]['geometry']['bbox']
    crop=[geometry[0]-5,geometry[1]-5,geometry[2]+5,geometry[3]+5]
    rules=dict(schema_version=witness.RULES,id='component',version='1',dpi=120,
        regions=[dict(id='reply',page=1,bbox_pdf_points=crop)],
        fields=[dict(id='reply',native_field_id='reply',region_id='reply',selector=dict(kind='first_line'))])
    witness.write(tmp_path/'witness.rules.json',rules)
    witness.prepare(package,tmp_path/'fields',tmp_path/'witness.rules.json',tmp_path/'request')
    return package,tmp_path/'request'


def test_actual_cli_replays_raster_component_and_rejects_resealed_proposal(source_bundle,tmp_path,capsys):
    from local_vector_batch.cli import main
    from local_vector_batch import field_witness as witness
    package,request=source_bundle;before={n:m.file_sha256(package/n)for n in witness.SOURCE_FILES}
    output=tmp_path/'accounting'
    assert main(['field-witness','native-raster','--request',str(request),'--output',str(output),'--json'])==0
    result=witness.load(output/'accounting.json');assert result['components_proved']==1
    assert main(['field-witness','verify-native-raster','--bundle',str(output),'--json'])==0
    assert before=={n:m.file_sha256(package/n)for n in witness.SOURCE_FILES}
    result['results'][0]['transcription']='proved';witness.write(output/'accounting.json',result)
    manifest=witness.load(output/'manifest.json');manifest['files']['accounting.json']=m.file_sha256(output/'accounting.json');witness.write(output/'manifest.json',manifest)
    assert main(['field-witness','verify-native-raster','--bundle',str(output),'--json'])==2


def test_changed_original_is_refused_before_a_new_candidate_is_written(source_bundle,tmp_path):
    from local_vector_batch.cli import main
    package,request=source_bundle;source=package/'original.pdf';source.write_bytes(source.read_bytes()+b'\n% changed\n')
    output=tmp_path/'refused'
    assert main(['field-witness','native-raster','--request',str(request),'--output',str(output),'--json'])==2
    assert not output.exists()
