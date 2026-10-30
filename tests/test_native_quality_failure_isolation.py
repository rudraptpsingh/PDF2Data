"""Dispatcher regressions for a demonstrated native-source precondition failure."""
import json
from pathlib import Path
import pytest

from local_vector_batch import native_quality_repair as repair


def test_failed_source_gets_terminal_hold_and_sibling_runs_without_losing_rows(tmp_path,monkeypatch):
    manifest=tmp_path/'review.jsonl'
    entries=[{'l1_package':str(tmp_path/'bad'),'page':1,'source_pdf_sha256':'bad'},
             {'l1_package':str(tmp_path/'good'),'page':2,'source_pdf_sha256':'good'}]
    original=''.join(json.dumps(row)+'\n'for row in entries);manifest.write_text(original)
    calls=[]
    def create(parent,runtime,pages,output):
        calls.append(Path(parent).name)
        if Path(parent).name=='bad':raise ValueError('Selected text is not the exact fresh native extraction')
        output.mkdir();repair.save(output/'page-proofs.json',[])
        return {'whole_pages_cleared':[],'parent_l1_package_path':str(parent)}
    monkeypatch.setattr(repair,'create_spacing_reproof',create)
    monkeypatch.setattr(repair,'verify_spacing_reproof',lambda *a,**k:{'status':'verified'})
    result=repair.repair_review_queue(manifest,tmp_path/'runtime',tmp_path/'output')
    assert calls==['bad','good']
    assert result['held_source_groups']==1 and result['source_quality_pages_cleared']==0
    assert result['review_filter']['remaining_count']==2 and result['review_filter']['resolved_count']==0
    terminal=repair.read(tmp_path/'output/source-0001/group-terminal.json')
    assert terminal['status']=='held_source_group_error'and not terminal['source_capture_attested']
    assert repair.read(tmp_path/'output/source-0002/group-terminal.json')['status']=='source_revalidated'
    assert manifest.read_text()==original
    assert repair.rows(tmp_path/'output/review-queue/review-manifest.jsonl')==entries


def test_failing_fresh_source_verifier_never_exports_that_groups_partial_proofs(tmp_path,monkeypatch):
    manifest=tmp_path/'review.jsonl';manifest.write_text(json.dumps({'l1_package':str(tmp_path/'source'),'page':1})+'\n')
    def create(parent,runtime,pages,output):
        output.mkdir();repair.save(output/'page-proofs.json',[{'unverified':'candidate'}])
        return {'whole_pages_cleared':[1]}
    monkeypatch.setattr(repair,'create_spacing_reproof',create)
    monkeypatch.setattr(repair,'verify_spacing_reproof',lambda *a,**k:(_ for _ in ()).throw(ValueError('fresh source proof differs')))
    result=repair.repair_review_queue(manifest,tmp_path/'runtime',tmp_path/'result')
    assert result['held_source_groups']==1 and result['source_quality_pages_cleared']==0
    assert repair.read(tmp_path/'result/page-proofs.json')==[]
    assert result['review_filter']['remaining_count']==1


def test_failed_focus_decoder_preserves_exact_original_question_and_audit(tmp_path,monkeypatch):
    row={'l1_package':str(tmp_path/'source'),'page':1,'source_pdf_sha256':'source','selected_text_sha256':'selected',
         'independent_text_sha256':'independent','focus':{'kind':'number','selected_span':[0,2],'independent_span':[0,3]}}
    manifest=tmp_path/'review.jsonl';manifest.write_text(json.dumps(row)+'\n')
    proof={'source_pdf_sha256':'source','page':1,'page_text_sha256':'selected','independent_raw_sha256':'independent','signature':'candidate'}
    monkeypatch.setattr(repair,'decode_spacing_proof',lambda *a:(_ for _ in ()).throw(ValueError('stale or forged evidence')))
    result=repair.filter_revalidated_focus(manifest,[proof],tmp_path/'filtered')
    assert result['resolved_count']==0 and result['remaining_count']==1 and result['held_proof_errors']==1
    assert repair.rows(tmp_path/'filtered/review-manifest.jsonl')==[row]
    assert repair.rows(tmp_path/'filtered/held-proof-audit.jsonl')[0]['status']=='held'
