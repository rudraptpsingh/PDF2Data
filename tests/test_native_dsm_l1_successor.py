from copy import deepcopy
import json
import shutil
import pytest
from local_vector_batch import native_dsm_l1_successor as m


def test_selected_span_update_keeps_unrelated_literal_and_hash():
    old='a'*64;new='b'*64
    row={'evidence':[{'page':13,'page_text_sha256':old,'quote':'भारत का रािपत्र'}],
         'other':'unaltered'}
    assert m._replace(row,old,new)=={'evidence':[{'page':13,'page_text_sha256':new,'quote':'भारत का राजपत्र'}],
                                      'other':'unaltered'}
    assert row['evidence'][0]['page_text_sha256']==old


def test_registered_rebuild_rejects_artifact_or_profile_tamper(tmp_path,monkeypatch):
    from local_vector_batch import legal_l1,proof_identity
    folder=tmp_path/'candidate';folder.mkdir();profile={'source_configuration_expected':{'proof':'frozen'}}
    cfg={'operation':m.OPERATION,'implementation_sha256':m._sha(m.Path(m.__file__).read_bytes()),
         'proof_profile':profile,'parent_l1_package_path':'parent','target_extraction_path':'target',
         'parent_manifest_sha256':'a'*64,'target_extraction_manifest_sha256':'b'*64,
         'source_pdf_sha256':'c'*64,'candidate_page_sha256':'d'*64}
    (folder/'source-receipt.json').write_text(json.dumps({'quality_repair_configuration':cfg}))
    (folder/'proof.txt').write_text('exact')
    snapshot=tmp_path/'snapshot';shutil.copytree(folder,snapshot)
    monkeypatch.setattr(legal_l1,'verify_document',lambda _: {'integrity':'passed'})
    monkeypatch.setattr(proof_identity,'inspect_pdf2data_profile',lambda _:deepcopy(profile))
    monkeypatch.setattr(m,'build',lambda parent,target,out,**kw:shutil.copytree(snapshot,out))
    assert m.verify(folder)['source_quality']=='repair_required'
    (folder/'proof.txt').write_text('tampered')
    with pytest.raises(ValueError,match='differ'):m.verify(folder)
    (folder/'proof.txt').write_text('exact');cfg['proof_profile']={'changed':True}
    (folder/'source-receipt.json').write_text(json.dumps({'quality_repair_configuration':cfg}))
    with pytest.raises(ValueError,match='runtime changed'):m.verify(folder)
