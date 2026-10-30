"""Feature 17: two chunks with >0.9 cosine never both enter one context pack (core.retrieve)."""
from __future__ import annotations

import asyncio

import numpy as np

from core import retrieve as r


def _row(i: int, cos: float) -> dict:
    return {"id": f"chunk:{i}", "document": f"body of chunk {i} without the query term", "title": f"T{i}",
            "source_link": "", "alpha": 1.0, "beta": 1.0, "cosine": cos}


A = [1.0, 0.0]          # reference direction
NEAR_A = [0.96, 0.28]   # cosine to A = 0.96 (> threshold: a duplicate)
FAR_A = [0.895, 0.446]  # cosine to A ~= 0.895 (< threshold: kept)
ORTH = [0.0, 1.0]       # cosine to A = 0


def test_dedup_drops_later_near_duplicate_in_rank_order():
    rows = [_row(1, 0.9), _row(2, 0.85), _row(3, 0.6)]
    embs = [np.asarray(v) for v in (A, NEAR_A, ORTH)]
    kept = r.dedup_chunks(rows, embs)
    assert [row["id"] for row in kept] == ["chunk:1", "chunk:3"]


def test_dedup_keeps_rows_below_threshold():
    rows = [_row(1, 0.9), _row(2, 0.85)]
    embs = [np.asarray(v) for v in (A, FAR_A)]  # 0.895 < 0.9: both survive
    assert len(r.dedup_chunks(rows, embs)) == 2


def test_dedup_is_noop_without_embeddings():
    rows = [_row(1, 0.9), _row(2, 0.85)]
    assert r.dedup_chunks(rows, None) == rows
    assert r.dedup_chunks(rows, [np.asarray(A)]) == rows  # length mismatch: skip, don't guess
    assert r.dedup_chunks([], []) == []


def test_dedup_zero_vector_is_never_a_duplicate():
    rows = [_row(1, 0.9), _row(2, 0.85)]
    kept = r.dedup_chunks(rows, [np.asarray(A), np.zeros(2)])
    assert [row["id"] for row in kept] == ["chunk:1", "chunk:2"]


# ---- acceptance, end to end through retrieve() ----


def _dedup_db(fake_db, embeddings: dict[str, list[float]] | None):
    rows = [
        dict(_row("a", 0.90), embedding=embeddings and embeddings["chunk:a"]),
        dict(_row("a2", 0.85), embedding=embeddings and embeddings["chunk:a2"]),
        dict(_row("b", 0.60), embedding=embeddings and embeddings["chunk:b"]),
    ]

    def handler(sql, vars=None):
        if "IN $ids" in sql:
            if embeddings is None:
                raise RuntimeError("db down")
            return [{"id": cid, "embedding": embeddings[cid]} for cid in (vars or {}).get("ids", [])]
        return rows

    fake_db.rows["FROM chunk"] = handler


def _dedup_fixtures(fake_db, monkeypatch, embeddings):
    monkeypatch.setattr(r, "embed_query", lambda text: [1.0, 0.0])
    _dedup_db(fake_db, embeddings)


def test_near_duplicates_never_share_a_pack(fake_db, monkeypatch):
    embs = {"chunk:a": A, "chunk:a2": NEAR_A, "chunk:b": ORTH}
    _dedup_fixtures(fake_db, monkeypatch, embs)
    _ctx, cites, rows = asyncio.run(r.retrieve("anything", k=2))
    # a2 ranks second on raw cosine but duplicates a, so b takes the slot
    assert [c.chunk_id for c in cites] == ["chunk:a", "chunk:b"]
    assert [row["id"] for row in rows] == ["chunk:a", "chunk:b"]


def test_dedup_skips_gracefully_when_db_fails(fake_db, monkeypatch):
    _dedup_fixtures(fake_db, monkeypatch, None)  # embedding fetch raises -> skip the check
    _ctx, cites, _rows = asyncio.run(r.retrieve("anything", k=2))
    assert [c.chunk_id for c in cites] == ["chunk:a", "chunk:a2"]  # degradation, not failure
