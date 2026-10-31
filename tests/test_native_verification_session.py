"""Parent-once means successful validation reused, never metadata-only trust."""
import json,os
from pathlib import Path
import pytest
from test_legal_l1 import source_fixture


def package(tmp_path):
    from local_vector_batch.legal_l1 import extract_document
    source=source_fixture(tmp_path);out=tmp_path/'source-package';extract_document(source,out);return out


def test_parent_graph_validated_once_then_reused_after_all_bytes_rehashed(tmp_path):
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch.native_verification_session import VerificationSession
    parent=package(tmp_path)
    with VerificationSession()as session:
        expected=verify_document(parent);actual=verify_document(parent)
        assert actual==expected and session.stats['full_verifications']==1 and session.stats['cache_hits']==1
        actual['quality_counts']['auto_accepted']=999
        assert verify_document(parent)['quality_counts']!=actual['quality_counts']


def test_rename_stage_to_final_reuses_same_exact_sealed_graph(tmp_path):
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch.native_verification_session import VerificationSession
    parent=package(tmp_path)
    with VerificationSession()as session:
        before=verify_document(parent);dest=tmp_path/'renamed';parent.rename(dest)
        assert verify_document(dest)==before
        assert session.stats['full_verifications']==1 and session.stats['cache_hits']==1


def test_preserved_mtime_cannot_hide_artifact_corruption(tmp_path):
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch.native_verification_session import VerificationSession
    parent=package(tmp_path)
    with VerificationSession()as session:
        verify_document(parent);path=parent/'quality.json';stat=path.stat();path.write_text(path.read_text().replace('repair_required','source_blocked'))
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with pytest.raises(ValueError):verify_document(parent)
        assert session.stats['cache_hits']==0


def test_proof_or_dependency_epoch_change_invalidates_cached_validation(tmp_path,monkeypatch):
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch import proof_identity
    from local_vector_batch.native_verification_session import VerificationSession
    parent=package(tmp_path);state={'profile_sha256':'old'}
    monkeypatch.setattr(proof_identity,'inspect_pdf2data_profile',lambda p:state)
    with VerificationSession()as session:
        verify_document(parent);state['profile_sha256']='new';verify_document(parent)
        assert session.stats['full_verifications']==2 and session.stats['cache_hits']==0


def test_page_incremental_reader_has_live_hash_and_order_guards(tmp_path):
    from local_vector_batch.native_verification_session import VerificationSession
    path=tmp_path/'pages.jsonl';path.write_text('{"page":1,"text":"A"}\n{"page":2,"text":"B"}\n')
    with VerificationSession()as session:
        assert session.page_row(path,2)['text']=='B'
        path.write_text('{"page":2,"text":"B"}\n{"page":1,"text":"A"}\n')
        with pytest.raises(ValueError,match='Ordered'):session.page_row(path,1)


def test_session_exit_cannot_leak_parent_approval_into_another_job(tmp_path):
    from local_vector_batch.legal_l1 import verify_document
    from local_vector_batch.native_verification_session import VerificationSession,active_session
    parent=package(tmp_path)
    with VerificationSession()as first:verify_document(parent)
    assert active_session()is None
    with VerificationSession()as second:
        verify_document(parent);assert second.stats['full_verifications']==1 and second.stats['cache_hits']==0
