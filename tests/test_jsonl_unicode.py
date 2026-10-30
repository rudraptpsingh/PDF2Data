"""JSONL record separators are physical LF, not Unicode characters in strings."""
from pathlib import Path
import hashlib,json
import pytest
from local_vector_batch.evidence_package import _rows as package_rows
from local_vector_batch.presentation import _rows as presentation_rows
from local_vector_batch.legal_l1 import rows as legal_rows
from local_vector_batch.benchmark import rows as benchmark_rows


@pytest.mark.parametrize('reader',[package_rows,presentation_rows,legal_rows,benchmark_rows])
@pytest.mark.parametrize('separator',['\u0085','\u2028','\u2029'])
def test_valid_unicode_separators_never_split_a_json_record(tmp_path,reader,separator):
    text='Clause A'+separator+'Clause B';row={'page':1,'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    path=tmp_path/'pages.jsonl';path.write_text(json.dumps(row,ensure_ascii=False)+'\n')
    assert reader(path)==[row]


def test_two_physical_records_and_unicode_source_hash_round_trip(tmp_path):
    texts=['Not\u0085effective','Condition\u2028if any\u2029thereafter']
    data=[{'page':i+1,'text':t,'text_sha256':hashlib.sha256(t.encode()).hexdigest()} for i,t in enumerate(texts)]
    path=tmp_path/'pages.jsonl';path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data))
    result=package_rows(path);assert len(result)==2
    assert all(hashlib.sha256(r['text'].encode()).hexdigest()==r['text_sha256'] for r in result)


def test_physical_helper_handles_crlf_and_trailing_blank_records():
    from local_vector_batch.jsonl import jsonl_physical_lines
    text='{"text":"A\u0085B"}\r\n \t\r\n\r\n'
    assert [json.loads(line) for line in jsonl_physical_lines(text)]==[{'text':'A\u0085B'}]


def test_invalid_standalone_unicode_is_not_silently_dropped(tmp_path):
    from local_vector_batch.jsonl import jsonl_physical_lines
    lines=jsonl_physical_lines('\u0085\n')
    assert lines==['\u0085']
    with pytest.raises(json.JSONDecodeError):json.loads(lines[0])

    path=tmp_path/"invalid.jsonl";path.write_text("\u0085\n")
    for reader in (package_rows,presentation_rows,legal_rows,benchmark_rows):
        with pytest.raises(json.JSONDecodeError):reader(path)
