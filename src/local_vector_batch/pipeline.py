"""Bounded, resumable JSONL embedding pipeline."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Callable, Iterator

from .chunking import Passage, passages
from .model import Encoder, make_encoder, select_backend


@dataclass(frozen=True)
class Config:
    input_path: Path
    output_dir: Path
    model: str = "BAAI/bge-base-en-v1.5"
    backend: str = "auto"
    device: str = "auto"
    pooling: str = "cls"
    format: str = "vectorize"
    batch_size: int = 32
    chunk_chars: int = 1500
    overlap: int = 150
    min_chars: int = 80
    workers: int = 1
    max_tokens: int = 512
    normalize: bool = True
    revision: str | None = None
    threads_per_worker: int = 1
    offline: bool = False
    resume: bool = False


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _validate(config: Config) -> None:
    if not config.input_path.is_file():
        raise ValueError(f"input JSONL not found: {config.input_path}")
    if config.batch_size < 1 or config.workers < 1 or config.max_tokens < 1 or config.threads_per_worker < 1:
        raise ValueError("batch_size, workers, max_tokens, and threads_per_worker must be positive")
    if not 0 <= config.overlap < config.chunk_chars or config.min_chars < 1:
        raise ValueError("require 0 <= overlap < chunk_chars and min_chars >= 1")
    if config.format not in {"vectorize", "generic"}:
        raise ValueError("format must be vectorize or generic")
    if config.pooling not in {"cls", "mean"}:
        raise ValueError("pooling must be cls or mean")
    if config.workers > 1 and (select_backend(config.backend) != "torch" or config.device not in {"cpu"}):
        raise ValueError("parallel encoder workers require --backend torch --device cpu; GPU uses one batched encoder")


def _batches(config: Config) -> Iterator[list[Passage]]:
    batch: list[Passage] = []
    with config.input_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not isinstance(record.get("text"), str):
                    raise ValueError("record requires string id and text")
                if not record["id"]:
                    raise ValueError("id must not be empty")
                metadata = record.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise ValueError("metadata must be an object")
                for passage in passages(record["id"], record["text"], metadata, config.model,
                                        config.chunk_chars, config.overlap, config.min_chars):
                    batch.append(passage)
                    if len(batch) == config.batch_size:
                        yield batch
                        batch = []
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError(f"input line {line_number}: {error}") from error
    if batch:
        yield batch


_worker_encoder: Encoder | None = None


def _worker_init(options: dict) -> None:
    global _worker_encoder
    _worker_encoder = make_encoder(**options)


def _worker_encode(texts: list[str]) -> list[list[float]]:
    assert _worker_encoder is not None
    return _worker_encoder.encode(texts)


def _model_options(config: Config) -> dict:
    return {"backend": config.backend, "model": config.model, "device": config.device,
            "pooling": config.pooling, "max_tokens": config.max_tokens, "normalize": config.normalize,
            "revision": config.revision, "threads": config.threads_per_worker, "offline": config.offline}


def _emit_batches(config: Config, encoder_factory: Callable[[], Encoder] | None, skip: int):
    batches = enumerate(_batches(config))
    if config.workers == 1:
        encoder = None
        for index, batch in batches:
            if index < skip:
                continue
            if encoder is None:
                encoder = encoder_factory() if encoder_factory else make_encoder(**_model_options(config))
            yield index, batch, encoder.encode([passage.text for passage in batch]), encoder.device
        return
    # A bounded queue prevents a large corpus from filling RAM while preserving input order.
    import multiprocessing as mp
    with ProcessPoolExecutor(max_workers=config.workers, mp_context=mp.get_context("spawn"),
                             initializer=_worker_init, initargs=(_model_options(config),)) as pool:
        pending = deque()
        exhausted = False
        while pending or not exhausted:
            while len(pending) < config.workers * 2 and not exhausted:
                try:
                    index, batch = next(batches)
                except StopIteration:
                    exhausted = True
                    break
                if index < skip:
                    continue
                pending.append((index, batch, pool.submit(_worker_encode, [p.text for p in batch])))
            if pending:
                index, batch, future = pending.popleft()
                yield index, batch, future.result(), "torch:cpu"


def _fingerprint(config: Config, input_hash: str) -> str:
    options = asdict(config)
    for key in ("input_path", "output_dir", "resume"):
        del options[key]
    return hashlib.sha256(_json({"input_sha256": input_hash, "options": options})).hexdigest()


def run(config: Config, encoder_factory: Callable[[], Encoder] | None = None) -> dict:
    """Embed an input JSONL file; return the verifiable output manifest.

    ``encoder_factory`` is an in-process extension hook, useful for local custom
    encoders and offline tests. It cannot be combined with multiple workers.
    """
    _validate(config)
    if encoder_factory and config.workers != 1:
        raise ValueError("custom encoder_factory requires workers=1")
    input_hash = _sha(config.input_path)
    fingerprint = _fingerprint(config, input_hash)
    output = config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    existing = sorted(output.iterdir())
    if config.resume:
        if not manifest_path.is_file():
            raise ValueError("cannot resume: manifest.json is missing")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("fingerprint") != fingerprint:
            raise ValueError("cannot resume: input or embedding settings changed")
        # A crash between atomic batch rename and manifest rename can leave
        # exactly the next uncommitted batch. Recompute it on resume.
        orphan = output / f"batch-{len(manifest['batches']):06d}.ndjson"
        if orphan.is_file():
            orphan.unlink()
        verify(output)
    elif existing:
        raise ValueError("output directory is not empty; pass --resume or choose another directory")
    else:
        manifest = {"schema_version": 1, "input_sha256": input_hash,
                    "fingerprint": fingerprint, "model": config.model,
                    "backend": select_backend(config.backend), "pooling": config.pooling,
                    "normalized": config.normalize, "format": config.format,
                    "dimensions": None, "device": None, "batches": [], "complete": False}
        _atomic(manifest_path, _json(manifest))
    if manifest["complete"]:
        return manifest
    completed = len(manifest["batches"])
    count = completed
    for index, batch, vectors, device in _emit_batches(config, encoder_factory, completed):
        if len(vectors) != len(batch):
            raise ValueError(f"encoder returned {len(vectors)} vectors for {len(batch)} passages")
        rows = []
        for passage, vector in zip(batch, vectors):
            if not vector or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in vector):
                raise ValueError("encoder returned invalid vector")
            if manifest["dimensions"] is None:
                manifest["dimensions"] = len(vector)
            elif len(vector) != manifest["dimensions"]:
                raise ValueError("encoder dimensions changed during run")
            if config.format == "vectorize":
                rows.append({"id": passage.vector_id, "values": vector, "metadata": passage.metadata})
            else:
                rows.append({"id": passage.vector_id, "embedding": vector, "metadata": passage.metadata})
        name = f"batch-{index:06d}.ndjson"
        payload = b"".join(_json(row) for row in rows)
        _atomic(output / name, payload)
        manifest["device"] = device
        manifest["batches"].append({"file": name, "sha256": hashlib.sha256(payload).hexdigest(),
                                    "count": len(rows)})
        _atomic(manifest_path, _json(manifest))
        count += 1
    if count != len(manifest["batches"]):
        raise ValueError("resume failed: output contains batches past end of input")
    manifest["complete"] = True
    _atomic(manifest_path, _json(manifest))
    return manifest


def verify(output_dir: Path) -> dict:
    """Verify batch checksums, vector shape, and unique IDs; no model needed."""
    output = Path(output_dir)
    path = output / "manifest.json"
    if not path.is_file():
        raise ValueError("manifest.json is missing")
    manifest = json.loads(path.read_text())
    dimension = manifest["dimensions"]
    field = "values" if manifest["format"] == "vectorize" else "embedding"
    seen: set[str] = set()
    total = 0
    for index, item in enumerate(manifest["batches"]):
        expected = f"batch-{index:06d}.ndjson"
        if item["file"] != expected:
            raise ValueError(f"batch order is invalid at {index}")
        batch_path = output / expected
        if not batch_path.is_file() or _sha(batch_path) != item["sha256"]:
            raise ValueError(f"batch checksum mismatch: {expected}")
        actual = 0
        with batch_path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                values = row[field]
                if row["id"] in seen or not isinstance(values, list) or len(values) != dimension or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v) for v in values
                ):
                    raise ValueError(f"invalid or duplicate vector in {expected}")
                seen.add(row["id"])
                actual += 1
        if actual != item["count"]:
            raise ValueError(f"batch row count mismatch: {expected}")
        total += actual
    unexpected = set(output.glob("batch-*.ndjson")) - {output / b["file"] for b in manifest["batches"]}
    if unexpected:
        raise ValueError("untracked batch files in output directory")
    return {"complete": manifest["complete"], "batches": len(manifest["batches"]),
            "vectors": total, "dimensions": dimension, "model": manifest["model"]}
