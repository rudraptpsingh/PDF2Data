"""Fast private derivation retains the public fresh source decoder boundary."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_native_minimal_grids import grid_pdf


def test_job_render_cache_is_bound_and_never_shared_between_jobs(tmp_path,monkeypatch):
    from PIL import Image
    from local_vector_batch import proof_identity
    from local_vector_batch.native_table_regions import _VerifiedSourceRenderCache
    monkeypatch.setattr(proof_identity,'inspect_pdf2data_profile',lambda p:{'profile_sha256':'epoch'})
    source=tmp_path/'source';source.write_bytes(b'actual immutable source bytes')
    snapshot={'source_pdf_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'page':1,'page_text_sha256':'text'}
    calls=[]
    def render(**options):calls.append(options);return SimpleNamespace(original=Image.new('RGB',(4,4),'white'))
    cache=_VerifiedSourceRenderCache(source,snapshot,{'policy':1})
    assert cache.image(render,resolution=120)is cache.image(render,resolution=120)
    assert len(calls)==1
    _VerifiedSourceRenderCache(source,snapshot,{'policy':1}).image(render,resolution=120)
    assert len(calls)==2


@pytest.mark.parametrize('mutation',['source','page','text','policy','epoch','pixels'])
def test_job_render_cache_rejects_every_bound_input_or_pixel_change(tmp_path,monkeypatch,mutation):
    from PIL import Image
    from local_vector_batch import proof_identity
    from local_vector_batch.native_table_regions import _VerifiedSourceRenderCache
    state={'profile_sha256':'epoch'};monkeypatch.setattr(proof_identity,'inspect_pdf2data_profile',lambda p:state)
    source=tmp_path/'source';source.write_bytes(b'original');snapshot={'source_pdf_sha256':hashlib.sha256(b'original').hexdigest(),'page':1,'page_text_sha256':'text'};policy={'policy':1}
    image=SimpleNamespace(original=Image.new('RGB',(4,4),'white'));cache=_VerifiedSourceRenderCache(source,snapshot,policy)
    cache.image(lambda **kw:image,resolution=120)
    if mutation=='source':source.write_bytes(b'changed')
    elif mutation=='page':snapshot['page']=2
    elif mutation=='text':snapshot['page_text_sha256']='changed'
    elif mutation=='policy':policy['policy']=2
    elif mutation=='epoch':state['profile_sha256']='different'
    else:image.original.putpixel((0,0),(0,0,0))
    with pytest.raises(ValueError):cache.image(lambda **kw:image,resolution=120)


def test_public_assessor_still_reconstructs_untrusted_proof(monkeypatch,tmp_path):
    from local_vector_batch import native_table_regions as engine
    actual={'kind':engine.KIND,'value':'actual-original-source'};calls=[]
    monkeypatch.setitem(engine.PROOF_REGISTRY,engine.KIND,lambda *args:(calls.append(args)or actual))
    with pytest.raises(ValueError,match='original-source'):engine.assess_registered_regions(tmp_path,{}, {},{**actual,'value':'forged'})
    assert len(calls)==1
    with pytest.raises(ValueError,match='Unknown'):engine.assess_registered_regions(tmp_path,{}, {},{'kind':'arbitrary_json'})


def _source(tmp_path):
    from local_vector_batch.legal_l1 import extract_document
    source=tmp_path/'source.pdf';digest=grid_pdf(source,2,1);parent=tmp_path/'parent'
    extract_document({'id':'synthetic-grid','path':str(source),'sha256':digest,'family':'draft','expected_pages':1,
                      'url':'https://example.org/synthetic.pdf'},parent)
    feedback=tmp_path/'feedback.jsonl';feedback.write_text('');dossier=tmp_path/'dossier.json';dossier.write_text('{"decisions":[]}')
    return parent,feedback,dossier


@pytest.mark.parametrize('external',[False,True])
def test_builder_derives_once_then_requires_exactly_one_fresh_decoder(tmp_path,monkeypatch,external):
    from local_vector_batch import native_table_regions as engine
    parent,feedback,dossier=_source(tmp_path);actual=engine.derive_regions;calls=[]
    def derive(*args,**kwargs):calls.append(1);return actual(*args,**kwargs)
    monkeypatch.setattr(engine,'derive_regions',derive);monkeypatch.setitem(engine.PROOF_REGISTRY,engine.KIND,derive)
    output=tmp_path/'proof';result=engine.build_version(parent,Path(__file__).resolve().parents[1],feedback,dossier,1,output,external_decoder=external)
    if external:
        assert len(calls)==1 and result['status'].startswith('blocked_')
        assert not(output/'adapter-records.jsonl').exists()and result['registered_source_decoder']is None
        verified=engine.verify_region_version(Path(result['new_l1_package_path']),Path(__file__).resolve().parents[1])
        assert len(calls)==2 and verified['fresh_registered_source_decoder']is True
    else:
        assert len(calls)==2 and result['status']=='passed'
        assert result['registered_source_decoder']['fresh_registered_source_decoder']is True
        assert(output/'adapter-records.jsonl').exists()


def test_resealed_forged_stored_proof_is_rejected_by_actual_decoder(tmp_path):
    from local_vector_batch import native_table_regions as engine
    from local_vector_batch.legal_l1 import rows,write_rows,verify_document
    parent,feedback,dossier=_source(tmp_path);runtime=Path(__file__).resolve().parents[1]
    result=engine.build_version(parent,runtime,feedback,dossier,1,tmp_path/'proof',external_decoder=True)
    package=Path(result['new_l1_package_path']);layout=rows(package/'layout.jsonl')
    layout[0]['native_region_proof']['table_glyph_count']+=1;write_rows(package/'layout.jsonl',layout)
    manifest=json.loads((package/'l1-package.json').read_text());manifest['artifacts']['layout.jsonl']=engine.sha((package/'layout.jsonl').read_bytes());engine.save(package/'l1-package.json',manifest)
    verify_document(package) # Hash-valid packaging cannot establish source correctness.
    with pytest.raises(ValueError,match='original source'):engine.verify_region_version(package,runtime)
