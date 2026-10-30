"""Optional local encoder backends. Importing the package never downloads a model."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import platform
from typing import Protocol


class Encoder(Protocol):
    dimensions: int | None
    device: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def tokenize_without_truncation(tokenizer, texts: list[str], limit: int) -> dict:
    """Reject overflow before inference; character length is not a token budget."""
    tokens = tokenizer(texts, padding=False, truncation=False, add_special_tokens=True)
    overflow = [(index, len(ids)) for index, ids in enumerate(tokens["input_ids"]) if len(ids) > limit]
    if overflow:
        index, count = overflow[0]
        raise ValueError(f"passage {index + 1} has {count} tokens including special tokens; encoder limit is {limit}. "
                         "Use shorter windows or tokenizer-offset pre-splitting; no text was silently truncated")
    return tokens


def _token_limit(requested: int, *limits) -> int:
    valid = [int(value) for value in limits if isinstance(value, (int, float)) and 0 < value < 1_000_000]
    return min([requested, *valid])


def tokenizer_receipt(tokenizer, limit: int, revision: str | None = None) -> dict:
    digest = None
    if getattr(tokenizer, "is_fast", False):
        definition = json.loads(tokenizer.backend_tokenizer.to_str())
        # Padding/truncation are mutable call settings, not tokenizer identity.
        definition.pop("padding", None)
        definition.pop("truncation", None)
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"model": tokenizer.name_or_path, "revision": revision,
            "sha256": digest, "max_tokens": limit, "truncation_policy": "error"}


def load_split_tokenizer(*, backend: str, model: str, max_tokens: int,
                         revision: str | None, offline: bool):
    """Tokenizer-only setup for splitting before parallel CPU encoder workers."""
    try:
        from transformers import AutoConfig, AutoTokenizer
    except ImportError as error:
        raise RuntimeError("automatic splitting requires the installed encoder's transformers dependency") from error
    kwargs = {"trust_remote_code": False, "local_files_only": offline}
    if revision:
        kwargs["revision"] = revision
    if select_backend(backend) == "mlx":
        if offline or revision:
            raise ValueError("MLX registry cannot pin revisions or guarantee cached-only downloads; use Torch")
        from mlx_embedding_models.embedding import registry
        entry = registry["bge-base" if model == "BAAI/bge-base-en-v1.5" else model]
        tokenizer = AutoTokenizer.from_pretrained(entry["repo"], **kwargs)
        limit = _token_limit(max_tokens, entry["max_length"], getattr(tokenizer, "model_max_length", None))
        resolved = tokenizer.init_kwargs.get("_commit_hash")
    else:
        config = AutoConfig.from_pretrained(model, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(model, **kwargs)
        limit = _token_limit(max_tokens, getattr(config, "max_position_embeddings", None),
                             getattr(tokenizer, "model_max_length", None))
        resolved = revision or getattr(config, "_commit_hash", None)
    return tokenizer, limit, tokenizer_receipt(tokenizer, limit, resolved)


def select_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if (platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
            and importlib.util.find_spec("mlx_embedding_models") is not None):
        return "mlx"
    return "torch"


def unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not norm or not math.isfinite(norm):
        raise ValueError("encoder returned a zero or nonfinite vector")
    return [float(value) / norm for value in vector]


class MlxEncoder:
    def __init__(self, model: str, device: str, normalize: bool, max_tokens: int = 512):
        if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
            raise RuntimeError("MLX requires an Apple Silicon Mac")
        try:
            import mlx.core as mx
            from mlx_embedding_models.embedding import EmbeddingModel
        except ImportError as error:
            raise RuntimeError("Install the Mac backend: pip install 'local-vector-batch[mlx]'") from error
        chosen = "gpu" if device in {"auto", "gpu", "mps"} else device
        if chosen not in {"gpu", "cpu"}:
            raise ValueError("MLX device must be auto, gpu, mps, or cpu")
        mx.set_default_device(mx.gpu if chosen == "gpu" else mx.cpu)
        registry_name = "bge-base" if model == "BAAI/bge-base-en-v1.5" else model
        self.model = EmbeddingModel.from_registry(registry_name)
        self.max_tokens = _token_limit(max_tokens, self.model.max_length,
                                       getattr(self.model.tokenizer, "model_max_length", None))
        self.model.max_length = self.max_tokens
        self.tokenizer_info = tokenizer_receipt(self.model.tokenizer, self.max_tokens,
                                                self.model.tokenizer.init_kwargs.get("_commit_hash"))
        self.normalize = normalize
        self.dimensions = None
        self.device = f"mlx:{chosen}"

    def encode(self, texts: list[str]) -> list[list[float]]:
        tokenize_without_truncation(self.model.tokenizer, texts, self.max_tokens)
        values = self.model.encode(texts, batch_size=len(texts), show_progress=False).tolist()
        result = [unit(row) if self.normalize else [float(value) for value in row] for row in values]
        if result:
            self.dimensions = len(result[0])
        return result


class TorchEncoder:
    def __init__(self, model: str, device: str, pooling: str, max_tokens: int,
                 normalize: bool, revision: str | None, threads: int, offline: bool):
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as error:
            raise RuntimeError("Install the cross-platform backend: pip install 'local-vector-batch[torch]'") from error
        if threads > 0:
            torch.set_num_threads(threads)
        chosen = device
        if chosen == "auto":
            chosen = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        if chosen == "gpu":
            if not torch.cuda.is_available() and not torch.backends.mps.is_available():
                raise RuntimeError("GPU requested but PyTorch cannot see CUDA or MPS; use --device cpu")
            chosen = "cuda" if torch.cuda.is_available() else "mps"
        if chosen == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but this PyTorch install cannot see a CUDA GPU")
        if chosen == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but this PyTorch install cannot see an Apple GPU")
        if chosen not in {"cpu", "cuda", "mps"}:
            raise ValueError("Torch device must be auto, cpu, cuda, mps, or gpu")
        kwargs = {"trust_remote_code": False, "local_files_only": offline}
        if revision:
            kwargs["revision"] = revision
        self.tokenizer = AutoTokenizer.from_pretrained(model, **kwargs)
        self.model = AutoModel.from_pretrained(model, **kwargs).eval().to(chosen)
        self.torch = torch
        self.pooling = pooling
        self.max_tokens = _token_limit(max_tokens, getattr(self.model.config, "max_position_embeddings", None),
                                       getattr(self.tokenizer, "model_max_length", None))
        self.tokenizer_info = tokenizer_receipt(self.tokenizer, self.max_tokens,
                                                revision or getattr(self.model.config, "_commit_hash", None))
        self.normalize = normalize
        self.dimensions = getattr(self.model.config, "hidden_size", None)
        self.device = f"torch:{chosen}"

    def encode(self, texts: list[str]) -> list[list[float]]:
        tokens = tokenize_without_truncation(self.tokenizer, texts, self.max_tokens)
        tokens = self.tokenizer.pad(tokens, padding=True, return_tensors="pt")
        tokens = {key: value.to(self.model.device) for key, value in tokens.items()}
        with self.torch.inference_mode():
            hidden = self.model(**tokens).last_hidden_state
            if self.pooling == "mean":
                mask = tokens["attention_mask"].unsqueeze(-1)
                vectors = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            else:
                vectors = hidden[:, 0, :]
            values = vectors.float().cpu().tolist()
        result = [unit(row) if self.normalize else [float(value) for value in row] for row in values]
        if result:
            self.dimensions = len(result[0])
        return result


def make_encoder(*, backend: str, model: str, device: str, pooling: str, max_tokens: int,
                 normalize: bool, revision: str | None, threads: int, offline: bool) -> Encoder:
    selected = select_backend(backend)
    if selected == "mlx":
        if offline:
            raise ValueError("cached-only mode requires the Torch backend; MLX registry downloads cannot be disabled reliably")
        if pooling != "cls":
            raise ValueError("MLX registry backend currently supports CLS pooling only")
        if revision:
            raise ValueError("MLX registry backend does not support --revision; use Torch for pinned revisions")
        return MlxEncoder(model, device, normalize, max_tokens)
    if selected == "torch":
        return TorchEncoder(model, device, pooling, max_tokens, normalize, revision, threads, offline)
    raise ValueError(f"unknown backend: {selected}")
