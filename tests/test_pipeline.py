import json
from pathlib import Path

import pytest

from local_vector_batch.chunking import passages
from local_vector_batch.pipeline import Config, run, verify


class FakeEncoder:
    device = "fake:cpu"
    dimensions = 3

    def encode(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]


def input_file(tmp_path):
    path = tmp_path / "in.jsonl"
    records = [
        {"id": "a", "text": "Alpha " * 42, "metadata": {"url": "https://example.org/a"}},
        {"id": "b", "text": "Beta " * 40, "metadata": {"url": "https://example.org/b"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n")
    return path


def test_chunks_have_stable_offsets_and_ids():
    text = "0123456789" * 30
    chunks = list(passages("doc", text, {"source": "test"}, "model", 100, 20, 1))
    assert [p.start for p in chunks] == [0, 80, 160, 240]
    assert all(text[p.start:p.end] == p.text for p in chunks)
    assert [p.vector_id for p in chunks] == [p.vector_id for p in passages("doc", text, {"source": "test"}, "model", 100, 20, 1)]


def test_embed_verify_and_resume_without_reencoding(tmp_path):
    source = input_file(tmp_path)
    config = Config(source, tmp_path / "out", batch_size=2, chunk_chars=100, overlap=10, min_chars=1)
    manifest = run(config, encoder_factory=FakeEncoder)
    assert manifest["complete"]
    checked = verify(config.output_dir)
    assert checked["vectors"] > 2 and checked["dimensions"] == 3
    for item in manifest["batches"]:
        for row in (config.output_dir / item["file"]).read_text().splitlines():
            record = json.loads(row)
            assert len(record["values"]) == 3
            assert record["metadata"]["document_id"] in {"a", "b"}
    assert run(Config(**{**config.__dict__, "resume": True}),
               encoder_factory=lambda: (_ for _ in ()).throw(AssertionError("reencoded"))) == manifest


def test_resume_rejects_changed_input_and_corruption(tmp_path):
    source = input_file(tmp_path)
    config = Config(source, tmp_path / "out", chunk_chars=100, overlap=10, min_chars=1)
    manifest = run(config, encoder_factory=FakeEncoder)
    batch = config.output_dir / manifest["batches"][0]["file"]
    batch.write_text(batch.read_text() + "corrupt")
    with pytest.raises(ValueError, match="checksum"):
        verify(config.output_dir)
    with pytest.raises(ValueError, match="checksum"):
        run(Config(**{**config.__dict__, "resume": True}), encoder_factory=FakeEncoder)
    source.write_text(source.read_text() + "\n")
    with pytest.raises(ValueError, match="settings changed"):
        run(Config(**{**config.__dict__, "resume": True}), encoder_factory=FakeEncoder)


def test_gpu_worker_guard_and_output_guard(tmp_path):
    source = input_file(tmp_path)
    with pytest.raises(ValueError, match="parallel encoder workers"):
        run(Config(source, tmp_path / "out", workers=2, backend="mlx", device="gpu"), encoder_factory=FakeEncoder)
    config = Config(source, tmp_path / "out", chunk_chars=100, overlap=10, min_chars=1)
    run(config, encoder_factory=FakeEncoder)
    with pytest.raises(ValueError, match="not empty"):
        run(config, encoder_factory=FakeEncoder)


def test_reserved_metadata_rejected():
    with pytest.raises(ValueError, match="reserved"):
        list(passages("x", "test" * 30, {"char_start": 7}, "model", 100, 0, 1))


def test_resume_recovers_uncommitted_batch(tmp_path):
    source = input_file(tmp_path)
    config = Config(source, tmp_path / "out", batch_size=2, chunk_chars=100, overlap=10, min_chars=1)
    original = run(config, encoder_factory=FakeEncoder)
    manifest_path = config.output_dir / "manifest.json"
    interrupted = json.loads(manifest_path.read_text())
    interrupted["complete"] = False
    interrupted["batches"].pop()
    manifest_path.write_text(json.dumps(interrupted))
    resumed = run(Config(**{**config.__dict__, "resume": True}), encoder_factory=FakeEncoder)
    assert resumed["complete"] and resumed["batches"] == original["batches"]
    assert verify(config.output_dir)["vectors"] > 2
