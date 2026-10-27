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


def passages(document_id: str, text: str, metadata: dict, model: str,
             chunk_chars: int, overlap: int, min_chars: int = 80):
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document id must be a nonempty string")
    if not isinstance(text, str):
        raise ValueError(f"{document_id}: text must be a string")
    if not isinstance(metadata, dict) or RESERVED.intersection(metadata):
        raise ValueError(f"{document_id}: metadata must be an object without reserved offset keys")
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
            vector_id = hashlib.sha256(f"{model}\0{document_id}\0{start}\0{end}".encode()).hexdigest()[:32]
            fields = dict(metadata)
            fields.update(document_id=document_id, char_start=start, char_length=end-start,
                          offset_encoding="codepoint")
            yield Passage(vector_id, document_id, start, end, window, fields)
        if end == len(text):
            break
        start = end - overlap
