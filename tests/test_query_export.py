import json
from hashlib import sha256
import pytest
from local_vector_batch import query_export
from local_vector_batch.legal_l1 import extract_document,rows
from local_vector_batch.cli import parser
from test_legal_l1 import source_fixture

def setup(tmp_path):
    root=tmp_path/'packages';package=root/'example';extract_document(source_fixture(tmp_path),package)
    return root,package

def test_complete_cached_records_sidecars_and_one_parent_verification(tmp_path,monkeypatch):
    root,p=setup(tmp_path);calls=[];original=query_export.verify_document
    def verify(path):calls.append(path);return original(path)
    monkeypatch.setattr(query_export,'verify_document',verify)
    out=tmp_path/'query.jsonl';receipt=query_export.export_query_data(root,out);records=rows(out)
    assert len(calls)==1 and receipt['parent_verifications']==1 and receipt['withheld_pages']==0
    pages=[r for r in records if r['kind']=='page'];units=[r for r in records if r['kind']=='unit'];tables=[r for r in records if r['kind']=='table']
    assert [r['payload']for r in pages]==rows(p/'extraction/pages.jsonl')
    assert [r['payload']for r in units]==json.loads((p/'structure.json').read_text())['units']
    assert [r['payload']for r in tables]==json.loads((p/'extraction/tables.json').read_text())
    document=next(r for r in records if r['kind']=='document')['payload']
    assert document['quality']==json.loads((p/'quality.json').read_text())
    assert document['raw_sidecars']['layout.jsonl']['availability']=='local_not_uploaded'
    assert document['raw_sidecars']['layout.jsonl']['artifact_sha256']==sha256((p/'layout.jsonl').read_bytes()).hexdigest()
    assert all(not r['source_quality_approval_granted']and not r['legal_answer_eligible']and not r['publication_eligible']for r in records)
    assert all(r['payload_sha256']==sha256(query_export.canonical(r['payload']).encode()).hexdigest()for r in records)
    assert len({r['id']for r in records})==len(records)
    assert all('glyphs'not in r['payload']for r in records)

def test_no_layout_json_load_beyond_existing_parent_verifier(tmp_path,monkeypatch):
    root,p=setup(tmp_path);original=query_export.json.loads
    def loads(value,*args,**kwargs):
        # Existing verifier receives its layout through json.loads too; count
        # calls whose JSON object is a layout page and ensure only parent reads.
        result=original(value,*args,**kwargs)
        if isinstance(result,dict)and'pdfium_source_lines'in result:seen.append(result)
        return result
    seen=[];monkeypatch.setattr(query_export.json,'loads',loads)
    query_export.export_query_data(root,tmp_path/'query.jsonl')
    assert len(seen)==2

@pytest.mark.parametrize('target',['existing','inside','corrupt'])
def test_unsafe_output_or_corrupt_package_never_publishes(tmp_path,target):
    root,p=setup(tmp_path);out=tmp_path/'query.jsonl'
    if target=='existing':out.write_text('keep')
    elif target=='inside':out=p/'query.jsonl'
    else:(p/'layout.jsonl').write_text('{}\n')
    with pytest.raises(ValueError):query_export.export_query_data(root,out)
    assert out.read_text()=='keep'if target=='existing'else not out.exists()

def test_changed_source_during_export_never_publishes(tmp_path,monkeypatch):
    root,p=setup(tmp_path);original=query_export.verify_document
    def mutate(path):
        result=original(path);(path/'quality.json').write_text('{}');return result
    monkeypatch.setattr(query_export,'verify_document',mutate)
    out=tmp_path/'query.jsonl'
    with pytest.raises(ValueError,match='changed'):query_export.export_query_data(root,out)
    assert not out.exists()

def test_new_cli_is_optional_and_full_export_unchanged():
    args=parser().parse_args(['export-query-data','--packages','p','--output','out','--json'])
    assert args.command=='export-query-data'
    assert parser().parse_args(['export-l1-data','--packages','p','--output','out']).command=='export-l1-data'

def test_broken_symlink_is_not_an_output_path(tmp_path):
    root,p=setup(tmp_path);out=tmp_path/'linked.jsonl';target=tmp_path/'absent.jsonl';out.symlink_to(target)
    with pytest.raises(ValueError,match='new file'):query_export.export_query_data(root,out)
    assert out.is_symlink()and not target.exists()

def test_nonfinite_cached_payload_refuses_even_if_parent_verifier_permits_it(tmp_path,monkeypatch):
    root,p=setup(tmp_path);checked=query_export.verify_document(p)
    # Deliberately permissive parent stub: query JSON must still be standards
    # compliant, not silently emit Python's NaN/Infinity extensions.
    quality=json.loads((p/'quality.json').read_text());quality['injected_nonfinite_control']=float('nan')
    raw=json.dumps(quality).encode();(p/'quality.json').write_bytes(raw)
    manifest=json.loads((p/'l1-package.json').read_text());manifest['artifacts']['quality.json']=sha256(raw).hexdigest()
    (p/'l1-package.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(query_export,'verify_document',lambda path:checked)
    out=tmp_path/'query.jsonl'
    with pytest.raises(ValueError,match='Out of range float values'):query_export.export_query_data(root,out)
    assert not out.exists()and(p/'quality.json').read_bytes()==raw
