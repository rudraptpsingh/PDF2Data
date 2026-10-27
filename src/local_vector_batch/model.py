"""Optional local encoder backends. Importing the package never downloads a model."""
from __future__ import annotations

import importlib.util
import math
import platform
from typing import Protocol


class Encoder(Protocol):
    dimensions: int | None
    device: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


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
    def __init__(self, model: str, device: str, normalize: bool):
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
        self.normalize = normalize
        self.dimensions = None
        self.device = f"mlx:{chosen}"

    def encode(self, texts: list[str]) -> list[list[float]]:
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
        self.max_tokens = max_tokens
        self.normalize = normalize
        self.dimensions = getattr(self.model.config, "hidden_size", None)
        self.device = f"torch:{chosen}"

    def encode(self, texts: list[str]) -> list[list[float]]:
        tokens = self.tokenizer(texts, padding=True, truncation=True, max_length=self.max_tokens,
                                return_tensors="pt")
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
        if pooling != "cls":
            raise ValueError("MLX registry backend currently supports CLS pooling only")
        if revision:
            raise ValueError("MLX registry backend does not support --revision; use Torch for pinned revisions")
        return MlxEncoder(model, device, normalize)
    if selected == "torch":
        return TorchEncoder(model, device, pooling, max_tokens, normalize, revision, threads, offline)
    raise ValueError(f"unknown backend: {selected}")
