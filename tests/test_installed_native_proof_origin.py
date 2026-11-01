"""Package-layout and immutable-boundary controls; no source accuracy claims."""
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest


def test_same_installed_package_directory_is_coherent(tmp_path,monkeypatch):
    from local_vector_batch import native_table_feedback as module
    package=tmp_path/'lib/python3.12/site-packages/local_vector_batch'
    monkeypatch.setattr(module,'__file__',str(package/'native_table_feedback.py'))
    modules=[SimpleNamespace(__file__=str(package/name))for name in ('legal_l1.py','l1_review.py','quality_gate.py')]
    module.require_coherent_proof_modules(*modules)
    modules[-1]=SimpleNamespace(__file__=str(tmp_path/'old-origin/src/local_vector_batch/quality_gate.py'))
    with pytest.raises(ValueError,match='Mixed cached proof'):module.require_coherent_proof_modules(*modules)


def test_same_source_checkout_is_coherent_and_nested_foreign_module_is_not(tmp_path,monkeypatch):
    from local_vector_batch import native_table_feedback as module
    package=tmp_path/'src/local_vector_batch';monkeypatch.setattr(module,'__file__',str(package/'native_table_feedback.py'))
    module.require_coherent_proof_modules(SimpleNamespace(__file__=str(package/'legal_l1.py')))
    with pytest.raises(ValueError,match='Mixed cached proof'):
        module.require_coherent_proof_modules(SimpleNamespace(__file__=str(package/'foreign/quality_gate.py')))


def test_origin_is_not_added_to_proof_interpreter_search_path(tmp_path,monkeypatch):
    from local_vector_batch import native_table_regions as engine,legal_l1
    parent=tmp_path/'parent';parent.mkdir();origin=tmp_path/'origin';origin.mkdir()
    feedback=tmp_path/'feedback';feedback.write_text('');dossier=tmp_path/'dossier';dossier.write_text('{}')
    before=list(sys.path)
    def stop(*args):raise RuntimeError('stop before source processing')
    monkeypatch.setattr(legal_l1,'verify_document',stop)
    try:
        with pytest.raises(RuntimeError,match='stop before'):engine.build_version(parent,origin,feedback,dossier,1,tmp_path/'proof')
        assert sys.path==before
    finally:sys.path[:]=before


@pytest.mark.parametrize('command',['cells','regions'])
@pytest.mark.parametrize('overlap',['parent','origin','feedback'])
def test_outputs_cannot_mutate_immutable_parents_origins_or_inputs(tmp_path,command,overlap):
    from local_vector_batch import native_table_regions as regions,native_table_feedback as cells
    parent=tmp_path/'parent';parent.mkdir();origin=tmp_path/'origin';origin.mkdir()
    feedback=tmp_path/'feedback';feedback.write_text('');dossier=tmp_path/'dossier';dossier.write_text('{}')
    output=parent/'new-output'if overlap=='parent'else origin/'new-output'
    if overlap=='feedback':output=tmp_path/'not-created';feedback=output/'input-feedback'
    with pytest.raises(ValueError,match='outside parent'):
        if command=='cells':cells.revalidate(parent,origin,feedback,dossier,output,1)
        else:regions.build_version(parent,origin,feedback,dossier,1,output)
    assert not output.exists()


def test_cell_hold_has_terminal_outer_receipt_without_a_quality_version(tmp_path,monkeypatch):
    from local_vector_batch import native_table_regions as engine,legal_l1,proof_identity
    parent=tmp_path/'parent';parent.mkdir();(parent/'l1-package.json').write_text('{}')
    (parent/'extraction').mkdir();(parent/'extraction/pages.jsonl').write_text('')
    origin=tmp_path/'origin';origin.mkdir();feedback=tmp_path/'feedback';feedback.write_text('');dossier=tmp_path/'dossier';dossier.write_text('{}')
    monkeypatch.setattr(legal_l1,'verify_document',lambda *args:{'integrity':'passed'})
    monkeypatch.setattr(engine,'proof_configuration',lambda *args:{})
    monkeypatch.setattr(engine,'revalidate',lambda *args:{'status':'held'})
    output=tmp_path/'proof';result=engine.build_version(parent,origin,feedback,dossier,1,output)
    assert result['status']=='held'and result['cell_validation_status']=='held'
    assert not result['new_source_quality_version_created']and not(output/'packages').exists()
    assert(output/'validation.json').is_file()
