import hashlib
import json
from dataclasses import asdict, replace

import pytest

from local_vector_batch.chunking import exact_passage
from local_vector_batch.pipeline import Config, _fingerprint, run, verify
from local_vector_batch.token_windows import TokenSplitter, nonspace_gaps


class FakeFastTokenizer:
    is_fast = True
    name_or_path = "fake-character"

    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, add_special_tokens=True, **kwargs):
        # Two subword tokens per nonspace Unicode codepoint, including repeated
        # offsets: splitting must never split or omit a source codepoint.
        offsets = [(i, i + 1) for i, c in enumerate(text) if not c.isspace() for _ in range(2)]
        ids = list(range(len(offsets)))
        if add_special_tokens:
            ids = [-1, *ids, -2]
        return {"input_ids": ids, "offset_mapping": offsets}


class SplitEncoder:
    device = "fake:cpu"
    max_tokens = 14
    tokenizer = FakeFastTokenizer()
    tokenizer_info = {"sha256": "fake-tokenizer-v1", "model": "fake", "max_tokens": 14,
                      "revision": "test-1", "truncation_policy": "error"}

    def encode(self, texts):
        assert all(len(self.tokenizer(t)["input_ids"]) <= self.max_tokens for t in texts)
        return [[float(len(t)), 1.0] for t in texts]


@pytest.mark.parametrize("text", ["  12.345 678.9 " * 20, "हिन्दी\n制度 café 🙂e\u0301" * 15,
                                  "verylongsubwordwithoutspaces" * 20, "\t alpha \n beta " * 15])
def test_split_exact_source_coverage_unicode_and_subwords(text):
    passage = exact_passage("doc", text, {"page_number": 8}, "model", 2, 900)
    splitter = TokenSplitter(FakeFastTokenizer(), 14, 64, "model")
    children = splitter.split(passage)
    assert len(children) > 1 and splitter.overlap <= splitter.budget // 4
    spans = []
    for child in children:
        assert text[child.start - 900:child.end - 900] == child.text
        assert len(splitter.tokenizer(child.text)["input_ids"]) <= 14
        assert child.metadata["page_number"] == 8 and child.metadata["chunk_index"] == 2
        assert child.metadata["char_length"] == len(child.text)
        spans.append((child.start - 900, child.end - 900))
    assert nonspace_gaps(text, spans) == 0
    assert children == splitter.split(passage)
    assert len({p.vector_id for p in children}) == len(children)


def test_safe_passage_identity_unchanged():
    passage = exact_passage("doc", " a🙂 ", {}, "model", 0, 12)
    assert TokenSplitter(FakeFastTokenizer(), 14, 2, "model").split(passage) == [passage]


def test_default_fingerprint_compatible(tmp_path):
    config = Config(tmp_path / "input", tmp_path / "out")
    old = asdict(config)
    for key in ["input_path", "output_dir", "resume", "overflow_policy", "token_overlap"]:
        old.pop(key)
    serialized = (json.dumps({"input_sha256": "hash", "options": old}, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert _fingerprint(config, "hash") == hashlib.sha256(serialized).hexdigest()
    assert _fingerprint(replace(config, overflow_policy="split"), "hash") != _fingerprint(config, "hash")


def test_split_pipeline_receipt_and_interrupted_resume(tmp_path):
    source = tmp_path / "in.jsonl"
    source.write_text(json.dumps({"id": "doc", "text": "数值12.34 " * 25,
                                 "chunk_index": 2, "char_start": 1000}) + "\n")
    config = Config(source, tmp_path / "out", input_mode="passages", overflow_policy="split", batch_size=2)
    manifest = run(config, SplitEncoder)
    assert manifest["input_audit"]["overflow_source_windows"] == 1
    assert manifest["input_audit"]["passages"] > 1
    assert manifest["input_audit"]["source_windows"] == 1
    assert manifest["token_splitter"]["tokenizer"]["revision"] == "test-1"
    assert verify(config.output_dir)["token_splitter"] == manifest["token_splitter"]
    # A resumable output must not silently switch tokenizer identity.
    manifest["complete"] = False
    path = config.output_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    class ChangedEncoder(SplitEncoder):
        tokenizer_info = dict(SplitEncoder.tokenizer_info, sha256="changed")
    with pytest.raises(ValueError, match="identity changed"):
        run(replace(config, resume=True), ChangedEncoder)
    assert run(replace(config, resume=True), SplitEncoder)["complete"]
