"""Feature 13: model.encode runs once per EMBED_BATCH upload batch, never once per chunk."""
from __future__ import annotations

import math

import numpy as np

from scripts import ingest


class CountingModel:
    """Stands in for the sentence-transformer; records every encode() call."""

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.calls: list[list[str]] = []

    def encode(self, texts, normalize_embeddings=False):
        self.calls.append(list(texts))
        return np.ones((len(texts), self.dim), dtype=np.float32)


def test_one_encode_call_per_batch(monkeypatch):
    model = CountingModel()
    monkeypatch.setattr(ingest, "_model", model)
    texts = [f"chunk number {i}" for i in range(100)]  # 100 chunks / EMBED_BATCH 32 -> 4 calls

    vecs = ingest.embed(texts)

    expected = math.ceil(len(texts) / ingest.EMBED_BATCH)
    assert len(model.calls) == expected
    assert len(model.calls) < len(texts)  # never one call per chunk
    assert [len(b) for b in model.calls] == [ingest.EMBED_BATCH] * (expected - 1) + [len(texts) % ingest.EMBED_BATCH]
    assert all(len(b) <= ingest.EMBED_BATCH for b in model.calls)
    assert len(vecs) == len(texts)


def test_batches_cover_texts_in_order(monkeypatch):
    model = CountingModel()
    monkeypatch.setattr(ingest, "_model", model)
    texts = [f"text {i}" for i in range(70)]

    ingest.embed(texts)

    flattened = [t for batch in model.calls for t in batch]
    assert flattened == texts  # no text lost or reordered across batch boundaries


def test_single_text_is_a_single_encode_call(monkeypatch):
    model = CountingModel()
    monkeypatch.setattr(ingest, "_model", model)

    ingest.embed(["just one"])

    assert len(model.calls) == 1 and model.calls[0] == ["just one"]
