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

from .chunking import Passage, exact_passage, passages
from .model import Encoder, make_encoder, select_backend, load_split_tokenizer
from .token_windows import TokenSplitter


@dataclass(frozen=True)
class Config:
    input_path: Path
    output_dir: Path
    model: str = "BAAI/bge-base-en-v1.5"
    backend: str = "auto"
    device: str = "auto"
    pooling: str = "cls"
    format: str = "vectorize"
    input_mode: str = "documents"
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
    overflow_policy: str = "error"
    token_overlap: int = 64


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
    if config.input_mode not in {"documents", "chunks", "passages"}:
        raise ValueError("input_mode must be documents, chunks, or passages")
    if config.overflow_policy not in {"error", "split"} or config.token_overlap < 0:
        raise ValueError("require overflow_policy error or split and nonnegative token_overlap")
    if config.pooling not in {"cls", "mean"}:
        raise ValueError("pooling must be cls or mean")
    if config.workers > 1 and (select_backend(config.backend) != "torch" or config.device not in {"cpu"}):
        raise ValueError("parallel encoder workers require --backend torch --device cpu; GPU uses one batched encoder")


def _batches(config: Config, audit: dict | None = None) -> Iterator[list[Passage]]:
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
                chunk_index = None
                if config.input_mode in {"chunks", "passages"}:
                    chunk_index = record.get("chunk_index")
                    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
                        raise ValueError("chunk_index must be a nonnegative integer in chunks or passages mode")
                selected = [exact_passage(record["id"], record["text"], metadata, config.model,
                                          chunk_index, record.get("char_start"))] if config.input_mode == "passages" else passages(
                    record["id"], record["text"], metadata, config.model, config.chunk_chars,
                    config.overlap, config.min_chars, chunk_index=chunk_index)
                produced = 0
                for passage in selected:
                    produced += 1
                    batch.append(passage)
                    if len(batch) == config.batch_size:
                        yield batch
                        batch = []
                if audit is not None:
                    audit["records"] += 1
                    audit["passages"] += produced
                    audit["records_with_passages"] += int(produced > 0)
                    audit["records_without_passages"] += int(produced == 0)
                    if not produced and len(audit["skipped_examples"]) < 20:
                        audit["skipped_examples"].append({"id": record["id"], "line": line_number,
                                                          "reason": "empty_or_below_min_chars"})
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError(f"input line {line_number}: {error}") from error
    if batch:
        yield batch


_worker_encoder: Encoder | None = None


def _worker_init(options: dict) -> None:
    global _worker_encoder
    _worker_encoder = make_encoder(**options)


def _worker_encode(texts: list[str]):
    assert _worker_encoder is not None
    return _worker_encoder.encode(texts), getattr(_worker_encoder, "tokenizer_info", None)


def _model_options(config: Config) -> dict:
    return {"backend": config.backend, "model": config.model, "device": config.device,
            "pooling": config.pooling, "max_tokens": config.max_tokens, "normalize": config.normalize,
            "revision": config.revision, "threads": config.threads_per_worker, "offline": config.offline}


def _split_batches(source, splitter, size, audit):
    batch, source_count, total, overflow = [], 0, 0, 0
    for original in source:
        for passage in original:
            children = splitter.split(passage)
            source_count += 1
            overflow += int(len(children) > 1)
            for child in children:
                total += 1
                batch.append(child)
                if len(batch) == size:
                    yield batch
                    batch = []
    audit.update(source_windows=source_count, overflow_source_windows=overflow,
                 extra_passages=total - source_count, passages=total)
    if batch:
        yield batch


def _emit_batches(config: Config, encoder_factory: Callable[[], Encoder] | None, skip: int, audit: dict, processing: dict):
    encoder = None
    source = _batches(config, audit)
    if config.overflow_policy == "split":
        if config.workers == 1:
            encoder = encoder_factory() if encoder_factory else make_encoder(**_model_options(config))
            tokenizer = getattr(encoder, "tokenizer", None) or getattr(getattr(encoder, "model", None), "tokenizer", None)
            limit = getattr(encoder, "max_tokens", None)
            receipt = getattr(encoder, "tokenizer_info", None)
            if tokenizer is None or limit is None or not receipt or not receipt.get("sha256"):
                raise ValueError("split policy requires an encoder with a fast tokenizer and hashed tokenizer receipt")
        else:
            tokenizer, limit, receipt = load_split_tokenizer(backend=config.backend, model=config.model,
                max_tokens=config.max_tokens, revision=config.revision, offline=config.offline)
        splitter = TokenSplitter(tokenizer, limit, config.token_overlap, config.model)
        processing.update(version=TokenSplitter.VERSION, policy="split", token_overlap=splitter.overlap,
                          requested_overlap=config.token_overlap, tokenizer=receipt)
        source = _split_batches(source, splitter, config.batch_size, audit)
    batches = enumerate(source)
    if config.workers == 1:
        for index, batch in batches:
            if index < skip:
                continue
            if encoder is None:
                encoder = encoder_factory() if encoder_factory else make_encoder(**_model_options(config))
            yield index, batch, encoder.encode([passage.text for passage in batch]), encoder.device, getattr(encoder, "tokenizer_info", None)
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
                vectors, tokenizer_info = future.result()
                yield index, batch, vectors, "torch:cpu", tokenizer_info


def _fingerprint(config: Config, input_hash: str) -> str:
    options = asdict(config)
    for key in ("input_path", "output_dir", "resume"):
        del options[key]
    if config.overflow_policy == "error":
        options.pop("overflow_policy")
        options.pop("token_overlap")
    else:
        options["token_split_version"] = TokenSplitter.VERSION
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
                    "input_mode": config.input_mode,
                    "requested_model_revision": config.revision,
                    "requested_max_tokens": config.max_tokens,
                    "tokenizer": None, "token_budget_validated": True,
                    "dimensions": None, "device": None, "batches": [], "complete": False}
        _atomic(manifest_path, _json(manifest))
    if manifest["complete"]:
        return manifest
    completed = len(manifest["batches"])
    count = completed
    audit = {"records": 0, "records_with_passages": 0, "records_without_passages": 0,
             "passages": 0, "skipped_examples": []}
    processing = {}
    def check_processing():
        previous = manifest.get("token_splitter")
        if previous and previous != processing:
            raise ValueError("cannot resume: token splitter or tokenizer identity changed")
        if processing:
            manifest["token_splitter"] = dict(processing)
    for index, batch, vectors, device, tokenizer_info in _emit_batches(config, encoder_factory, completed, audit, processing):
        check_processing()
        if processing and tokenizer_info != processing["tokenizer"]:
            raise ValueError("splitter and encoder tokenizer receipts differ")
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
        if manifest.get("tokenizer") and (not tokenizer_info or any(tokenizer_info.get(key) != value for key, value in manifest["tokenizer"].items())):
            raise ValueError("encoder tokenizer configuration changed during run")
        _atomic(output / name, payload)
        manifest["device"] = device
        manifest["tokenizer"] = tokenizer_info
        manifest["token_budget_validated"] = bool(manifest.get("token_budget_validated", False) and
                                                   tokenizer_info and tokenizer_info.get("truncation_policy") == "error")
        manifest["batches"].append({"file": name, "sha256": hashlib.sha256(payload).hexdigest(),
                                    "count": len(rows)})
        _atomic(manifest_path, _json(manifest))
        count += 1
    check_processing()
    if count != len(manifest["batches"]):
        raise ValueError("resume failed: output contains batches past end of input")
    if audit["passages"] != sum(item["count"] for item in manifest["batches"]):
        raise ValueError("input passage count does not match the completed vector batches")
    manifest["input_audit"] = audit
    manifest["input_coverage_complete"] = audit["records"] > 0 and audit["records_without_passages"] == 0
    if not audit["passages"]:
        manifest["token_budget_validated"] = False
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
    splitter = manifest.get("token_splitter")
    if splitter and (splitter.get("version") != TokenSplitter.VERSION or
                     splitter.get("tokenizer") != manifest.get("tokenizer") or
                     not splitter.get("tokenizer", {}).get("sha256")):
        raise ValueError("token splitter receipt is inconsistent with encoder receipt")
    audit = manifest.get("input_audit")
    if audit and "source_windows" in audit and (audit["passages"] != audit["source_windows"] + audit["extra_passages"] or
            not 0 <= audit["overflow_source_windows"] <= audit["source_windows"]):
        raise ValueError("token split coverage counters are inconsistent")
    if manifest["complete"] and audit is not None and (audit["passages"] != total or
                              audit["records"] != audit["records_with_passages"] + audit["records_without_passages"] or
                              manifest.get("input_coverage_complete") != (audit["records"] > 0 and audit["records_without_passages"] == 0)):
        raise ValueError("input coverage receipt is inconsistent with the vector batches")
    return {"complete": manifest["complete"], "batches": len(manifest["batches"]),
            "vectors": total, "dimensions": dimension, "model": manifest["model"],
            "input_coverage_complete": manifest.get("input_coverage_complete"),
            "token_budget_validated": manifest.get("token_budget_validated", False),
            "tokenizer": manifest.get("tokenizer"), "token_splitter": manifest.get("token_splitter")}
