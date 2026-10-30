"""Per-session rate limiting on the ask surface (feature 5): the 11th request
inside the window gets 429, the limit is reused from core.llm's bucket config,
and other sessions are unaffected.
"""
from __future__ import annotations

import sys
import types

import httpx
import pytest

from app import rate_limit


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
def _fresh_limiter():
    rate_limit.limiter.reset()


def test_limit_is_reused_from_llm_bucket_config():
    # The gateway's config is the single tuning surface; sessions inherit it.
    from core.llm import BUCKET_LIMITS

    expected = BUCKET_LIMITS.get("ask", BUCKET_LIMITS.get("growth", (10, 60.0)))
    assert rate_limit.ASK_SESSION_LIMIT == expected == (10, 60.0)
    assert rate_limit.limiter.limit == expected[0]
    assert rate_limit.limiter.window_s == expected[1]


def test_active_window_drops_expired_hits():
    hits = [0.0, 10.0, 59.0, 60.0]
    assert rate_limit.active_window(hits, now=70.0, window_s=60.0) == [59.0, 60.0]


async def test_11th_request_in_window_returns_429(monkeypatch, client):
    async def ask(text, session_id):
        return {"spoken": "ok", "question_id": "question:x"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    async with client as c:
        statuses = []
        for _ in range(rate_limit.limiter.limit + 1):  # 11th request is the acceptance probe
            r = await c.post("/ask", json={"text": "who owns billing?", "session_id": "s1"})
            statuses.append(r.status_code)
    assert statuses == [200] * rate_limit.limiter.limit + [429]
    assert "Retry-After" in r.headers
    assert "rate limit exceeded" in r.json()["detail"]


async def test_full_window_does_not_block_other_sessions(monkeypatch, client):
    async def ask(text, session_id):
        return {"spoken": "ok", "question_id": "question:x"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    async with client as c:
        for _ in range(rate_limit.limiter.limit):
            await c.post("/ask", json={"text": "q", "session_id": "s1"})
        r = await c.post("/ask", json={"text": "q", "session_id": "s2"})
    assert r.status_code == 200


def test_limiter_check_and_retry_after_pure_clock():
    lim = rate_limit.SessionRateLimiter(limit=2, window_s=10.0)
    assert lim.check("k", now=0.0) and lim.check("k", now=1.0)
    assert not lim.check("k", now=2.0)
    assert lim.retry_after("k", now=2.0) == pytest.approx(8.0)
    # after the window rolls, a hit is allowed again
    assert lim.check("k", now=10.5)
