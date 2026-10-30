"""Stable codepoint-offset passage windows."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    vector_id: str
    document_id: str
    start: int
    end: int
    text: str
    metadata: dict[str, str | int | float | bool]


RESERVED = {"document_id", "char_start", "char_length", "offset_encoding"}


def exact_passage(document_id: str, text: str, metadata: dict, model: str,
                  chunk_index: int, char_start: int) -> Passage:
    """A caller-selected exact slice of an owning chunk, without rechunking."""
    if not isinstance(char_start, int) or isinstance(char_start, bool) or char_start < 0:
        raise ValueError("char_start must be a nonnegative integer in passages mode")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
        raise ValueError("chunk_index must be a nonnegative integer in passages mode")
    # Reuse metadata/type checks; one window covers the exact supplied text.
    if not isinstance(text, str) or not text.strip():
        raise ValueError("passages mode requires nonempty text")
    base = next(passages(document_id, text, metadata, model, max(80, len(text)), 0, 1,
                         chunk_index=chunk_index))
    end = char_start + len(text)
    identity = f"{model}\0{document_id}\0passage\0{chunk_index}\0{char_start}\0{end}"
    fields = dict(base.metadata, char_start=char_start, char_length=len(text))
    return Passage(hashlib.sha256(identity.encode()).hexdigest()[:32], document_id,
                   char_start, end, text, fields)


def passages(document_id: str, text: str, metadata: dict, model: str,
             chunk_chars: int, overlap: int, min_chars: int = 80,
             chunk_index: int | None = None):
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document id must be a nonempty string")
    if not isinstance(text, str):
        raise ValueError(f"{document_id}: text must be a string")
    if not isinstance(metadata, dict) or RESERVED.intersection(metadata):
        raise ValueError(f"{document_id}: metadata must be an object without reserved offset keys")
    if chunk_index is not None and (not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0):
        raise ValueError(f"{document_id}: chunk_index must be a nonnegative integer")
    if chunk_index is not None and "chunk_index" in metadata:
        raise ValueError(f"{document_id}: chunk_index belongs on the input record, not in metadata")
    if any(not isinstance(value, (str, int, float, bool)) or
           (isinstance(value, float) and not math.isfinite(value)) for value in metadata.values()):
        raise ValueError(f"{document_id}: metadata values must be finite scalars")
    if chunk_chars < 80 or overlap < 0 or overlap >= chunk_chars or min_chars < 1:
        raise ValueError("invalid chunk_chars, overlap, or min_chars")
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        window = text[start:end]
        if len(window.strip()) >= min_chars:
            identity = (f"{model}\0{document_id}\0{start}\0{end}" if chunk_index is None else
                        f"{model}\0{document_id}\0chunk\0{chunk_index}\0{start}\0{end}")
            vector_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
            fields = dict(metadata)
            fields.update(document_id=document_id, char_start=start, char_length=end-start,
                          offset_encoding="codepoint")
            if chunk_index is not None:
                fields["chunk_index"] = chunk_index
            yield Passage(vector_id, document_id, start, end, window, fields)
        if end == len(text):
            break
        start = end - overlap
