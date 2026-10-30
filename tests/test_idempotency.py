"""Idempotency keys on /ask (feature 9): the same Idempotency-Key twice yields
one answer and one ledger event — the ask pipeline (which is what writes the
ledger row) runs exactly once, so the second response is the cached first answer.
"""
from __future__ import annotations

import sys
import types

import httpx
import pytest

from app import idempotency


def _stub(monkeypatch, name: str, **funcs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _fresh_cache():
    idempotency.clear()


@pytest.fixture
def ask_calls(monkeypatch):
    calls: list[str] = []

    async def ask(text, session_id):
        calls.append(text)
        return {"spoken": f"answer {text}", "question_id": "question:x"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    return calls


async def test_same_key_twice_yields_one_answer_one_pipeline_call(client, ask_calls):
    async with client as c:
        r1 = await c.post("/ask", json={"text": "who owns billing?", "session_id": "s1"},
                          headers={"Idempotency-Key": "retry-abc"})
        r2 = await c.post("/ask", json={"text": "who owns billing?", "session_id": "s1"},
                          headers={"Idempotency-Key": "retry-abc"})
    assert r1.status_code == r2.status_code == 200
    b1, b2 = r1.json(), r2.json()
    assert {k: v for k, v in b1.items() if k != "request_id"} == {k: v for k, v in b2.items() if k != "request_id"}
    assert ask_calls == ["who owns billing?"], "the ask pipeline (one ledger event) must run exactly once"


async def test_different_key_runs_the_pipeline_again(client, ask_calls):
    async with client as c:
        await c.post("/ask", json={"text": "q", "session_id": "s1"}, headers={"Idempotency-Key": "k1"})
        r = await c.post("/ask", json={"text": "q", "session_id": "s1"}, headers={"Idempotency-Key": "k2"})
    assert r.status_code == 200
    assert len(ask_calls) == 2


async def test_no_header_keeps_old_behavior(client, ask_calls):
    async with client as c:
        await c.post("/ask", json={"text": "q", "session_id": "s1"})
        await c.post("/ask", json={"text": "q", "session_id": "s1"})
    assert len(ask_calls) == 2


def test_cache_is_bounded_lru():
    from app.idempotency import IDEMPOTENCY_CACHE_MAX

    for i in range(IDEMPOTENCY_CACHE_MAX + 10):
        idempotency.remember_answer(f"k{i}", {"n": i})
    assert len(idempotency._cache) == IDEMPOTENCY_CACHE_MAX
    assert idempotency.cached_answer("k0") is None  # oldest evicted
    assert idempotency.cached_answer(f"k{IDEMPOTENCY_CACHE_MAX + 9}") == {"n": IDEMPOTENCY_CACHE_MAX + 9}
    # a hit refreshes recency: it survives the next eviction round
    assert idempotency.cached_answer(f"k{IDEMPOTENCY_CACHE_MAX + 9}") is not None
    idempotency.remember_answer("fresh", {})
    assert idempotency.cached_answer("k1") is None
