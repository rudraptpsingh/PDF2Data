"""Overflow splitting at real tokenizer offsets, retaining exact source slices."""
from __future__ import annotations

import hashlib
from .chunking import Passage


def nonspace_gaps(text: str, spans: list[tuple[int, int]]) -> int:
    end = missing = 0
    for start, stop in sorted(spans):
        if start > end:
            missing += sum(not char.isspace() for char in text[end:start])
        end = max(end, stop)
    missing += sum(not char.isspace() for char in text[end:])
    return missing


class TokenSplitter:
    VERSION = 1

    def __init__(self, tokenizer, limit: int, overlap: int, model: str):
        if not getattr(tokenizer, "is_fast", False):
            raise ValueError("automatic splitting requires a fast tokenizer with codepoint offsets")
        self.tokenizer = tokenizer
        self.limit = limit
        self.budget = limit - tokenizer.num_special_tokens_to_add(pair=False)
        if self.budget < 1:
            raise ValueError("token budget cannot fit the model's special tokens")
        self.overlap = min(overlap, max(0, self.budget // 4))
        self.model = model

    def _count(self, text: str) -> int:
        return len(self.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])

    def split(self, passage: Passage) -> list[Passage]:
        # Preserve already-safe exact passages and ordinary window IDs.
        if self._count(passage.text) <= self.limit:
            return [passage]
        encoded = self.tokenizer(passage.text, add_special_tokens=False, truncation=False,
                                 return_offsets_mapping=True)
        offsets = [(int(start), int(end)) for start, end in encoded["offset_mapping"]]
        if not offsets or any(start < 0 or end < start or end > len(passage.text) for start, end in offsets):
            raise ValueError("tokenizer did not return valid source offsets")
        if any(offsets[index][0] > offsets[index + 1][0] for index in range(len(offsets) - 1)):
            raise ValueError("tokenizer source offsets are not in reading order")
        result, spans = [], []
        position = 0
        while position < len(offsets):
            start = 0 if position == 0 else offsets[position][0]
            stop_token = min(position + self.budget, len(offsets))
            stop = len(passage.text) if stop_token == len(offsets) else offsets[stop_token][0]
            # Subword segmentation can change when a slice starts mid-word.
            # Check the actual slice, not just its original token count.
            while stop <= start or self._count(passage.text[start:stop]) > self.limit:
                stop_token -= 1
                if stop_token <= position:
                    raise ValueError("a source character/token cannot fit the encoder budget without loss")
                stop = offsets[stop_token][0]
            text = passage.text[start:stop]
            absolute_start, absolute_end = passage.start + start, passage.start + stop
            identity = f"{self.model}\0token-offset-v{self.VERSION}\0{passage.vector_id}\0{absolute_start}\0{absolute_end}"
            fields = dict(passage.metadata, char_start=absolute_start, char_length=len(text),
                          token_split_parent=passage.vector_id, token_split_version=self.VERSION)
            result.append(Passage(hashlib.sha256(identity.encode()).hexdigest()[:32], passage.document_id,
                                  absolute_start, absolute_end, text, fields))
            spans.append((start, stop))
            if stop == len(passage.text):
                break
            next_position = max(position + 1, stop_token - self.overlap)
            while next_position < len(offsets) and offsets[next_position][0] <= start:
                next_position += 1
            position = next_position
        if nonspace_gaps(passage.text, spans):
            raise ValueError("tokenizer splitting left uncovered source characters; refusing incomplete vectors")
        return result
