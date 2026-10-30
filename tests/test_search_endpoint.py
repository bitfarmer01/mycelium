"""Feature 20: /search debug endpoint — chunk id, cosine, fitness, rank; bearer-gated when auth is on."""
from __future__ import annotations

import importlib

import httpx
import pytest
from fastapi import HTTPException, Request

from core import retrieve as r

search_router = importlib.import_module("app.routers.search")


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def corpus_db(fake_db, monkeypatch):
    """Two ranked candidates through the real knn/rerank path; embeddings present (no dedup drop)."""
    monkeypatch.setattr(r, "embed_query", lambda text: [1.0, 0.0])
    rows = [
        {"id": "chunk:a", "document": "alpha body", "title": "TA", "source_link": "",
         "alpha": 1.0, "beta": 1.0, "cosine": 0.9, "embedding": [1.0, 0.0]},
        {"id": "chunk:b", "document": "beta body", "title": "TB", "source_link": "",
         "alpha": 3.0, "beta": 1.0, "cosine": 0.6, "embedding": [1.0, 0.0]},
    ]
    embeddings = {"chunk:a": [1.0, 0.0], "chunk:b": [0.0, 1.0]}  # orthogonal: never de-duped

    def handler(sql, vars=None):
        if "IN $ids" in sql:
            return [{"id": cid, "embedding": embeddings[cid]} for cid in (vars or {}).get("ids", [])]
        return rows

    fake_db.rows["FROM chunk"] = handler
    return fake_db


async def test_search_returns_rank_chunk_id_cosine_fitness(client, corpus_db):
    async with client as c:
        resp = await c.get("/search", params={"q": "anything", "k": 2})
    body = resp.json()
    assert resp.status_code == 200
    assert [row["rank"] for row in body["results"]] == [1, 2]  # 1-based, dense
    for row in body["results"]:
        assert set(row) >= {"rank", "chunk_id", "cosine", "fitness"}
        assert row["chunk_id"] in ("chunk:a", "chunk:b")
        assert 0.0 < row["fitness"] <= 1.0  # alpha/(alpha+beta) of the row's Beta state
    assert body["results"][0]["chunk_id"] == "chunk:a"  # higher cosine ranks first


async def test_search_is_read_only(client, corpus_db):
    async with client as c:
        await c.get("/search", params={"q": "anything"})
    updates = [s for s in corpus_db.sql() if "retrieved_count" in s]
    assert updates == []  # debug endpoint must not bump fitness counters


async def test_search_honors_k(client, corpus_db):
    async with client as c:
        resp = await c.get("/search", params={"q": "anything", "k": 1})
    body = resp.json()
    assert [row["rank"] for row in body["results"]] == [1]
    assert len(body["results"]) == 1


# ---- auth gating: feature 8 env contract applied to feature 20 ----


async def test_search_open_when_auth_unset(client, corpus_db, monkeypatch):
    monkeypatch.delenv("MYCELIUM_AUTH_TOKEN", raising=False)
    async with client as c:
        resp = await c.get("/search", params={"q": "anything"})
    assert resp.status_code == 200


async def test_search_401_without_bearer_when_auth_on(client, corpus_db, monkeypatch):
    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", "secret-token")
    async with client as c:
        no_header = await c.get("/search", params={"q": "anything"})
        wrong = await c.get("/search", params={"q": "anything"}, headers={"Authorization": "Bearer nope"})
        right = await c.get("/search", params={"q": "anything"}, headers={"Authorization": "Bearer secret-token"})
    assert no_header.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200


async def test_require_bearer_reads_env_per_request(monkeypatch):
    """The gate is a live env read, not an import-time constant."""
    request = Request({"type": "http", "headers": []})

    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", "t1")
    with pytest.raises(HTTPException) as exc:
        search_router._require_bearer(request)
    assert exc.value.status_code == 401

    monkeypatch.delenv("MYCELIUM_AUTH_TOKEN")
    search_router._require_bearer(request)  # auth off → open; must not raise
