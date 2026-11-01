"""Custody bindings are capability grants, never inferred filesystem redirects."""
import hashlib
import json
from pathlib import Path
import pytest
from local_vector_batch.proof_relocation import Bindings,SCHEMA,hash_value,read,inventory,captured_path
from local_vector_batch.proof_relocation import supported_decoder_epoch,RC12_CODE,RC13_CODE


@pytest.mark.parametrize('epoch',[RC12_CODE,RC13_CODE,'a'*64])
@pytest.mark.parametrize('operation',['native_whitefill_prose','native_table_regions'])
def test_exact_released_and_current_epochs_are_supported_only_for_registered_operations(epoch,operation):
    assert supported_decoder_epoch(epoch,'a'*64,operation)
    assert not supported_decoder_epoch(epoch,'a'*64,'unregistered_operation')


@pytest.mark.parametrize('epoch',['b'*64,RC12_CODE[:-1]+'0',RC13_CODE[:-1]+'0','v0.2.0-rc.13',''])
def test_unreleased_or_modified_decoder_epochs_remain_refused(epoch):
    assert not supported_decoder_epoch(epoch,'a'*64,'native_table_regions')


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def custody(tmp_path):
    root=tmp_path/'custody';parent=root/'parent';parent.mkdir(parents=True)
    (parent/'original.pdf').write_bytes(b'original source')
    pdf=hashlib.sha256(b'original source').hexdigest();configuration={'source':'unchanged'}
    save(parent/'l1-package.json',{'identity':'captured parent'})
    manifest=hashlib.sha256((parent/'l1-package.json').read_bytes()).hexdigest()
    save(parent/'source-receipt.json',dict(configuration=configuration,actual_sha256=pdf,declared={'sha256':pdf}))
    config=dict(parent_l1_package_path=str(root.parent/'unavailable/original/parent'),parent_manifest_sha256=manifest,source_extraction_configuration=configuration)
    entry=dict(captured_path=config['parent_l1_package_path'],resolved_path=str(parent),manifest_sha256=manifest,source_pdf_sha256=pdf,source_configuration_sha256=hash_value(configuration))
    data=dict(schema_version=SCHEMA,custody_root=str(root),packages=[entry],files=[])
    mapping=save(root/'bindings.json',data)
    return root,parent,config,{'actual_sha256':pdf},data,mapping


def test_parent_requires_all_three_captured_identity_pins_and_full_verification(custody):
    root,parent,config,source,data,mapping=custody;calls=[];bound=Bindings(mapping)
    assert bound.path(config['parent_l1_package_path'])==Path(config['parent_l1_package_path'])
    assert bound.package(config,source,lambda p:calls.append(p))==parent
    assert calls==[parent]
    assert bound.path(config['parent_l1_package_path'])==parent
    assert bound.path('/unmapped/original')==Path('/unmapped/original')
    bound.unchanged()


@pytest.mark.parametrize('field',['manifest_sha256','source_pdf_sha256','source_configuration_sha256'])
def test_wrong_declared_parent_pin_refused(custody,field):
    root,parent,config,source,data,mapping=custody;data['packages'][0][field]='a'*64;save(mapping,data)
    with pytest.raises(ValueError,match='pins differ'):Bindings(mapping).package(config,source,lambda p:None)


def test_resealed_substitution_cannot_replace_captured_parent(custody):
    root,parent,config,source,data,mapping=custody
    save(parent/'l1-package.json',{'identity':'different but independently resealed'})
    with pytest.raises(ValueError,match='manifest differs'):Bindings(mapping).package(config,source,lambda p:None)


@pytest.mark.parametrize('changed',['original','configuration','declared'])
def test_original_and_source_configuration_must_match(custody,changed):
    root,parent,config,source,data,mapping=custody
    if changed=='original':(parent/'original.pdf').write_bytes(b'other')
    else:
        receipt=read(parent/'source-receipt.json');receipt['configuration'if changed=='configuration'else'declared']={};save(parent/'source-receipt.json',receipt)
    with pytest.raises(ValueError,match='original/source configuration'):Bindings(mapping).package(config,source,lambda p:None)


def test_missing_parent_fails_closed(custody):
    root,parent,config,source,data,mapping=custody;data['packages']=[];save(mapping,data)
    with pytest.raises(ValueError,match='Missing explicit parent'):Bindings(mapping).package(config,source,lambda p:None)


def test_binding_cannot_escape_custody(custody,tmp_path):
    root,parent,config,source,data,mapping=custody;escape=tmp_path/'outside';escape.mkdir();data['packages'][0]['resolved_path']=str(escape);save(mapping,data)
    with pytest.raises(ValueError,match='escapes custody'):Bindings(mapping)


def test_parent_mutation_and_symlink_rejected(custody):
    root,parent,config,source,data,mapping=custody
    def mutate(p):(p/'new').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed during'):Bindings(mapping).package(config,source,mutate)
    (parent/'alias').symlink_to(parent/'original.pdf')
    with pytest.raises(ValueError,match='symbolic links'):inventory(parent)


def test_review_files_require_exact_binding_and_remain_sealed(custody):
    root,parent,config,source,data,mapping=custody;image=root/'crop.png';image.write_bytes(b'pixel bytes');expected=hashlib.sha256(image.read_bytes()).hexdigest()
    old=str(root.parent/'old/crop.png');other=str(root.parent/'other/crop.png')
    data['files']=[dict(captured_path=old,resolved_path=str(image),sha256=expected)];save(mapping,data)
    bound=Bindings(mapping)
    assert bound.file(old,expected,'review_raster')==image
    with pytest.raises(ValueError,match='Missing explicit file'):bound.file(other,expected,'review_raster')
    image.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed during'):bound.unchanged()


def test_unknown_duplicate_or_nonlexical_mapping_refused(custody):
    root,parent,config,source,data,mapping=custody
    data['schema_version']='unknown';save(mapping,data)
    with pytest.raises(ValueError,match='Unknown or malformed'):Bindings(mapping)
    mapping.write_text('{"key":1,"key":2}')
    with pytest.raises(ValueError,match='Duplicate'):read(mapping)
    for value in('relative/path','/old/../parent'):
        with pytest.raises(ValueError,match='absolute and lexical'):captured_path(value)
