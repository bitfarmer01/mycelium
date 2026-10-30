"""Feature 12: adding one file embeds only that file's chunks; unchanged files' vectors untouched."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.retrieve import EmbeddingCache
from scripts import ingest


class RecordingEmbed:
    def __init__(self):
        self.batches: list[list[str]] = []

    def __call__(self, texts):
        self.batches.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    @property
    def texts(self) -> list[str]:
        return [t for batch in self.batches for t in batch]


@pytest.fixture
def recording_embed(monkeypatch):
    fake = RecordingEmbed()
    monkeypatch.setattr(ingest, "embed", fake)
    return fake


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = EmbeddingCache(tmp_path / "cache.npz")
    monkeypatch.setattr(ingest, "EMBED_CACHE", c)  # keep tests off the real data/embed_cache.npz
    return c


def _docs(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("# Alpha\n\nalpha document body " + " ".join(f"a{i}" for i in range(400)))
    b = tmp_path / "b.md"
    b.write_text("# Beta\n\nbeta document body " + " ".join(f"b{i}" for i in range(400)))
    return a, b


def _seed_source_row(fake_db, path: Path):
    """Pretend this file was already ingested with its current bytes."""
    fake_db.rows["FROM source"] = [
        {"id": f"source:{ingest.source_id(str(path.resolve()))}", "fingerprint": ingest.file_fingerprint(path)}
    ]


def _written_chunk_rows(fake_db) -> list[dict]:
    rows = [v["rows"] for sql, v in fake_db.calls if "INSERT INTO chunk" in sql]
    return [r for batch in rows for r in batch]


def test_new_file_embeds_only_new_file_chunks(tmp_path, cache, recording_embed, fake_db):
    a, b = _docs(tmp_path)
    _seed_source_row(fake_db, a)  # a is already ingested and unchanged

    n_chunks, n_sources = asyncio.run(ingest.ingest_docs([a, b]))
    assert n_chunks > 0  # b's chunks were produced and embedded
    assert n_sources == 1  # only b got a source row
    written = _written_chunk_rows(fake_db)
    assert written and all(r["metadata"]["path"] == str(b.resolve()) for r in written)
    assert recording_embed.texts and any("beta document body" in t for t in recording_embed.texts)
    assert not any("alpha document" in t for t in recording_embed.texts)  # a never loaded or encoded


def test_unchanged_file_rows_not_rewritten(tmp_path, cache, recording_embed, fake_db):
    a, b = _docs(tmp_path)
    _seed_source_row(fake_db, a)
    a_chunk_id = ingest.chunk_id(str(a.resolve()), 0)

    asyncio.run(ingest.ingest_docs([a, b]))

    written_ids = {r["id"] for r in _written_chunk_rows(fake_db)}
    assert a_chunk_id not in written_ids  # a's chunks untouched
    assert ingest.chunk_id(str(b.resolve()), 0) in written_ids


def test_changed_file_is_reingested(tmp_path, cache, recording_embed, fake_db):
    a, _ = _docs(tmp_path)
    _seed_source_row(fake_db, a)

    a.write_text("# Alpha v2\n\nrewritten alpha body " + " ".join(f"z{i}" for i in range(400)))
    chunks, _ = asyncio.run(ingest.ingest_docs([a]))

    assert chunks
    assert any("rewritten alpha" in t for t in recording_embed.texts)


def test_all_new_files_ingest_everything(tmp_path, cache, recording_embed, fake_db):
    a, b = _docs(tmp_path)
    chunks, sources = asyncio.run(ingest.ingest_docs([a, b]))
    assert sources == 2 and chunks
    assert len(recording_embed.batches) >= 1


def test_store_persists_fingerprint(tmp_path, cache, recording_embed, fake_db):
    a, _ = _docs(tmp_path)
    asyncio.run(ingest.ingest_docs([a]))
    fp_vars = [v for sql, v in fake_db.calls if "MERGE" in sql]  # the source upsert, not the fingerprint SELECT
    assert fp_vars and fp_vars[0]["fingerprint"] == ingest.file_fingerprint(a)


def test_record_id_forms_normalize_to_bare_key():
    assert ingest._bare_id("source:abc123") == "abc123"
    assert ingest._bare_id("source:" + chr(0x27E8) + "0abc123" + chr(0x27E9)) == "0abc123"  # digit-leading ids get wrapped
    assert ingest._bare_id("abc123") == "abc123"
