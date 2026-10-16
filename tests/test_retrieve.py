"""Tests for core.retrieve: numbered context, fitness rerank, knn fallback, retrieve() with fakes."""
from __future__ import annotations

import asyncio

import pytest

from core import retrieve as r


def _row(i, title, cos=0.9, alpha=1.0, beta=1.0):
    return {
        "id": f"chunk:c{i}",
        "document": f"Body of doc {i}. " * 30,
        "title": title,
        "source_link": f"https://wiki/{title}",
        "alpha": alpha,
        "beta": beta,
        "cosine": cos,
    }


def test_numbered_context_format():
    ctx, cites = r.numbered_context([_row(1, "A"), _row(2, "B")])
    assert ctx.startswith("[Document 1 (Source: A)]\n")
    assert "\n\n[Document 2 (Source: B)]\n" in ctx
    assert [c.n for c in cites] == [1, 2]
    assert cites[0].chunk_id == "chunk:c1"
    assert cites[1].link == "https://wiki/B"
    assert len(cites[0].snippet) == 200


def test_rerank_prefers_fit_chunk():
    plain = _row(1, "plain", cos=0.8, alpha=1, beta=1)
    fit = _row(2, "fit", cos=0.8, alpha=3, beta=1)
    out = r.rerank([plain, fit], floor=0.5)
    assert out[0]["title"] == "fit"
    assert out[0]["score"] > out[1]["score"]
    assert out[1]["score"] == pytest.approx(0.8 * (0.5 + 0.5 * 0.5))


def test_knn_falls_back_to_numpy(fake_db):
    def handler(sql, vars):
        if "<|" in sql:
            raise RuntimeError("knn unsupported")
        return [
            {"id": "chunk:x", "embedding": [1.0, 0.0], "document": "x", "title": "X",
             "source_link": "", "alpha": 1, "beta": 1},
            {"id": "chunk:y", "embedding": [0.0, 1.0], "document": "y", "title": "Y",
             "source_link": "", "alpha": 1, "beta": 1},
        ]

    fake_db.rows["FROM chunk"] = handler
    rows = asyncio.run(r.knn([0.0, 1.0], k=1))
    assert rows[0]["id"] == "chunk:y"
    assert rows[0]["cosine"] == pytest.approx(1.0)
    assert "embedding" not in rows[0]


def test_retrieve_with_fakes(fake_db, monkeypatch):
    monkeypatch.setattr(r, "embed_query", lambda text: [0.1] * 1024)
    fake_db.rows["vector::similarity::cosine"] = [
        _row(1, "A", cos=0.9), _row(2, "B", cos=0.7, alpha=5, beta=1), _row(3, "C", cos=0.2)
    ]
    ctx, cites, rows = asyncio.run(r.retrieve("who owns billing?", k=2))
    assert len(cites) == 2 and len(rows) == 2
    assert ctx.startswith("[Document 1 (Source: ")
    knn_sql, knn_vars = fake_db.calls[0]
    assert "$v" in knn_sql and len(knn_vars["v"]) == 1024
    upd = [s for s in fake_db.sql() if s.lstrip().upper().startswith("UPDATE")]
    assert upd and "retrieved_count" in upd[0]
    pack = r.evidence_pack("q1", "who owns billing?", ctx, cites)
    assert pack == {"question_id": "q1", "text": "who owns billing?", "context": ctx,
                    "n_docs": 2, "chunk_ids": [c.chunk_id for c in cites]}


def test_retrieve_empty_table(fake_db, monkeypatch):
    monkeypatch.setattr(r, "embed_query", lambda text: [0.1] * 1024)
    ctx, cites, rows = asyncio.run(r.retrieve("anything"))
    assert ctx == "" and cites == [] and rows == []


@pytest.mark.slow
def test_embed_query_dims():
    v = r.embed_query("who owns the billing service?")
    assert len(v) == 1024
    assert abs(sum(x * x for x in v) - 1.0) < 1e-3
