import pytest

from local_vector_batch.model import tokenize_without_truncation
from local_vector_batch.pipeline import Config, run, verify


class Tokenizer:
    def __call__(self, texts, **options):
        assert options["truncation"] is False
        assert options["add_special_tokens"] is True
        return {"input_ids": [[0, *range(len(text)), 1] for text in texts]}


def test_token_overflow_fails_instead_of_silently_shortening_text():
    with pytest.raises(ValueError, match="12 tokens.*limit is 8"):
        tokenize_without_truncation(Tokenizer(), ["abcdefghij"], 8)
    assert len(tokenize_without_truncation(Tokenizer(), ["abc"], 8)["input_ids"][0]) == 5


def test_manifest_records_effective_tokenizer_budget(tmp_path):
    source = tmp_path / "in.jsonl"
    source.write_text('{"id":"doc","text":"a legal clause"}\n')

    class Encoder:
        device = "test:cpu"
        tokenizer_info = {"model": "test-tokenizer", "revision": "revision-1", "max_tokens": 8,
                          "truncation_policy": "error"}

        def encode(self, texts):
            return [[1., 0.] for text in texts]

    output = tmp_path / "out"
    manifest = run(Config(source, output, min_chars=1), encoder_factory=Encoder)
    assert manifest["token_budget_validated"]
    assert verify(output)["tokenizer"] == Encoder.tokenizer_info
