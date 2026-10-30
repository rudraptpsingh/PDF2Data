import copy,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from local_vector_batch.lossless_text import auxiliary_text_receipt,validate_auxiliary_text_receipts,verify_invalid_auxiliary_readers,lossless_json_dumps
from local_vector_batch.legal_l1 import text_hash,write_rows


def raw_fixture(text='bad\udc65literal'):
 pdf='a'*64;selected='ordinary legal text';raw={'page':7,'page_text_sha256':text_hash(selected),'native_text_raw':selected,'secondary_text_raw':text,'secondary_layout_text_raw':'normal layout'}
 raw['auxiliary_reader_receipts']={key:auxiliary_text_receipt(raw[key],source_pdf_sha256=pdf,page=7,page_text_sha256=raw['page_text_sha256'])for key in('native_text_raw','secondary_text_raw','secondary_layout_text_raw')}
 return raw,pdf


def test_valid_unicode_hash_and_serialized_bytes_do_not_change():
 value={'text':'भारत ∑ 25% 😀','number':42.52};assert lossless_json_dumps(value,indent=2)==json.dumps(value,ensure_ascii=False,indent=2)
 assert auxiliary_text_receipt(value['text'],source_pdf_sha256='a'*64,page=1,page_text_sha256='b'*64)['utf8_text_sha256']==hashlib.sha256(value['text'].encode()).hexdigest()


def test_lone_surrogates_are_lossless_escaped_witness_not_ordinary_utf8():
 raw,pdf=raw_fixture();receipt=raw['auxiliary_reader_receipts']['secondary_text_raw'];assert receipt['invalid_scalar_positions']==[{'offset':3,'codepoint':0xdc65}];assert receipt['utf8_text_sha256']is None and not receipt['ordinary_utf8_text_available'];validate_auxiliary_text_receipts(raw,pdf)
 encoded=lossless_json_dumps(raw);assert '\\udc65'in encoded and json.loads(encoded)==raw
 with pytest.raises(UnicodeEncodeError):text_hash(raw['secondary_text_raw'])


def test_paired_python_surrogates_are_not_silently_merged():
 with pytest.raises(ValueError,match='codepoint-array'):lossless_json_dumps({'raw':'\ud83d\ude00'})


def test_literal_backslash_is_not_same_as_bad_codepoint():
 actual,_=raw_fixture();literal,_=raw_fixture('bad\\udc65literal');assert actual!=literal
 assert actual['auxiliary_reader_receipts']['secondary_text_raw']['escaped_json_sha256']!=literal['auxiliary_reader_receipts']['secondary_text_raw']['escaped_json_sha256']


@pytest.mark.parametrize('change',['absent','pdf','page','text_hash','escaped_hash','scalar','pretend_utf8'])
def test_forged_auxiliary_receipts_fail_closed(change):
 raw,pdf=raw_fixture();receipt=raw['auxiliary_reader_receipts']['secondary_text_raw']
 if change=='absent':raw.pop('auxiliary_reader_receipts')
 elif change=='pdf':receipt['source_pdf_sha256']='c'*64
 elif change=='page':receipt['page']=8
 elif change=='text_hash':receipt['page_text_sha256']='c'*64
 elif change=='escaped_hash':receipt['escaped_json_sha256']='c'*64
 elif change=='scalar':raw['secondary_text_raw']='bad\udc66literal'
 else:receipt.update(ordinary_utf8_text_available=True,utf8_text_sha256='c'*64)
 with pytest.raises(ValueError):validate_auxiliary_text_receipts(raw,pdf)


def test_resealed_invalid_witness_must_match_actual_original_reread(monkeypatch):
 import pypdf
 raw,pdf=raw_fixture();monkeypatch.setattr(pypdf,'PdfReader',lambda path:SimpleNamespace(pages=[None]*6+[SimpleNamespace(extract_text=lambda **kwargs:'bad\udc65literal')]))
 verify_invalid_auxiliary_readers(Path('original.pdf'),raw)
 altered,_=raw_fixture('bad\udc66literal');validate_auxiliary_text_receipts(altered,pdf)
 with pytest.raises(ValueError,match='original re-read'):verify_invalid_auxiliary_readers(Path('original.pdf'),altered)


def test_json_rows_keep_invalid_provenance_without_changing_valid_unicode(tmp_path):
 raw,_=raw_fixture();path=tmp_path/'layout.jsonl';write_rows(path,[raw]);assert json.loads(path.read_text())==raw;assert b'\\udc65'in path.read_bytes()
 normal={'raw':'हिन्दी'};write_rows(path,[normal]);assert path.read_bytes()==(json.dumps(normal,ensure_ascii=False)+'\n').encode()


def test_legacy_valid_raw_readers_need_no_new_claim():
 validate_auxiliary_text_receipts({'native_text_raw':'भारत','secondary_text_raw':'plain','secondary_layout_text_raw':None},'a'*64)
