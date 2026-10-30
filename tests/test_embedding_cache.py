"""Feature 11: second ingest of unchanged corpus performs zero model encodes (mock asserts call count)."""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from core.retrieve import EmbeddingCache, content_hash
from scripts import ingest


class CountingEmbed:
    """Replaces ingest.embed; records every batch of texts it was asked to encode."""

    def __init__(self):
        self.batches: list[list[str]] = []

    def __call__(self, texts):
        self.batches.append(list(texts))
        return [[1.0, 0.5] for _ in texts]  # dummy 2-d vectors

    @property
    def texts(self) -> list[str]:
        return [t for batch in self.batches for t in batch]


@pytest.fixture
def counting_embed(monkeypatch):
    fake = CountingEmbed()
    monkeypatch.setattr(ingest, "embed", fake)
    return fake


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = EmbeddingCache(tmp_path / "cache.npz")
    monkeypatch.setattr(ingest, "EMBED_CACHE", c)  # keep tests off the real data/embed_cache.npz
    return c


def _write_docs(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("# Alpha\n\nalpha document body " + " ".join(f"a{i}" for i in range(400)))
    b = tmp_path / "b.md"
    b.write_text("# Beta\n\nbeta document body " + " ".join(f"b{i}" for i in range(400)))
    return a, b


def test_second_ingest_of_unchanged_corpus_encodes_nothing(tmp_path, cache, counting_embed, fake_db):
    a, b = _write_docs(tmp_path)

    first_chunks, first_sources = asyncio.run(ingest.ingest_docs([a, b]))
    assert first_chunks > 0 and first_sources == 2
    assert len(counting_embed.batches) >= 1  # cold cache encoded

    batches_after_first = len(counting_embed.batches)

    chunks, sources = asyncio.run(ingest.ingest_docs([a, b]))
    assert chunks == first_chunks and sources == 2  # same rows produced...
    assert len(counting_embed.batches) == batches_after_first  # ...but the second ingest added zero encode batches
    # explicit call-count assertion: every chunk text was encoded exactly once across both ingests
    assert len(counting_embed.texts) == first_chunks


def test_put_tolerates_concurrent_cache_instances(tmp_path):
    # PR #3 review: two EmbeddingCache instances on one path must not clobber each
    # other's entries — the worst case without this is extra re-encodes, never stale vectors.
    path = tmp_path / "cache.npz"
    a, b = EmbeddingCache(path), EmbeddingCache(path)
    a.put({"h1": [1.0, 0.0]})  # a warms its in-memory view
    b.put({"h2": [0.0, 1.0]})  # b never saw h1
    a.put({"h3": [1.0, 1.0]})  # pre-fix, this write dropped h2 from the file
    final = EmbeddingCache(path)
    assert set(final.get_many(["h1", "h2", "h3"])) == {"h1", "h2", "h3"}
    assert np.allclose(final.get_many(["h2"])["h2"], [0.0, 1.0])


def test_second_build_chunks_encodes_nothing(tmp_path, cache, counting_embed):
    a, _ = _write_docs(tmp_path)
    chunks1, _ = ingest.build_chunks([a], cache=cache)
    batches_after_first = len(counting_embed.batches)
    assert batches_after_first >= 1
    first_vecs = [c["embedding"] for c in chunks1]

    chunks2, _ = ingest.build_chunks([a], cache=cache)
    assert len(counting_embed.batches) == batches_after_first  # zero new encode calls
    assert all(c["embedding"] for c in chunks2)  # embeddings still attached
    for v1, v2 in zip(first_vecs, (c["embedding"] for c in chunks2)):
        assert v1 == pytest.approx(v2)  # cache returns the same vectors


def test_changed_text_reencodes_only_the_change(tmp_path, cache, counting_embed):
    a, b = _write_docs(tmp_path)
    ingest.build_chunks([a, b], cache=cache)
    texts_after_first = set(counting_embed.texts)

    b.write_text("# Beta rewritten\n\ncompletely different content " + " ".join(f"c{i}" for i in range(400)))
    chunks, _ = ingest.build_chunks([a, b], cache=cache)

    second_round = [t for t in counting_embed.texts if t not in texts_after_first]
    assert second_round  # the changed file was re-encoded...
    assert not any(t.startswith("alpha document") for t in second_round)  # ...but a's chunks came from cache
    assert all(c["embedding"] for c in chunks)


def test_content_hash_is_model_scoped():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("goodbye")
    assert content_hash("hello", model="other-model") != content_hash("hello")
    assert len(content_hash("hello")) == 40  # sha1 hex


def test_identical_text_encodes_once(tmp_path, cache, counting_embed):
    text = "# Dup\n\n" + " ".join(["same body words"] * 100)
    a = tmp_path / "x.md"
    a.write_text(text)
    b = tmp_path / "y.md"
    b.write_text(text)

    chunks, _ = ingest.build_chunks([a, b], cache=cache)

    assert len(chunks) >= 2  # both files produced chunks
    assert counting_embed.texts.count(chunks[0]["document"]) == 1  # shared text encoded once


def test_cache_roundtrip_survives_new_instance(tmp_path):
    path = tmp_path / "cache.npz"
    cache1 = EmbeddingCache(path)
    cache1.put({"h1": [0.25, 0.5], "h2": [1.0, 0.0]})
    cache2 = EmbeddingCache(path)  # fresh instance, must read the file

    assert sorted(cache2.get_many(["h1", "h2", "h3"])) == ["h1", "h2"]
    assert cache2.get_many(["h1"])["h1"] == pytest.approx([0.25, 0.5])
    assert len(cache1) == 2
