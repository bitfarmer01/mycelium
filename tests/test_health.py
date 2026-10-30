"""Health/readiness probes (feature 2): /healthz 200 always, /readyz 503 when SurrealDB is down."""
from __future__ import annotations

import sys
import types

import httpx
import pytest


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


async def test_healthz_always_200(client):
    # No DB stub on purpose: liveness must not depend on any external service.
    async with client as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_200_when_db_reachable(client, monkeypatch):
    import core.db

    async def ok(sql, vars=None):
        return [1]

    monkeypatch.setattr(core.db, "q", ok)
    async with client as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


async def test_readyz_503_when_db_down(client, monkeypatch):
    import core.db

    async def down(sql, vars=None):
        raise ConnectionError("db stopped")

    monkeypatch.setattr(core.db, "q", down)
    async with client as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert "ConnectionError" in r.json()["detail"]


async def test_readyz_503_when_db_module_missing(client, monkeypatch):
    # Driver unusable at use time (e.g. the surrealdb dependency import fails): the
    # router degrades to 503. Patch the `core` package's `db` attribute — what the
    # route's `from core import db` actually resolves — because a sys.modules entry
    # alone is bypassed by the already-cached package attribute.
    import core

    def boom(*a, **k):
        raise ImportError("surrealdb: connect failed")

    broken = types.ModuleType("core.db")
    broken.q = boom
    monkeypatch.setattr(core, "db", broken, raising=True)
    async with client as c:
        r = await c.get("/readyz")
    assert r.status_code == 503


async def test_readyz_503_on_slow_db(client, monkeypatch):
    import asyncio

    import core.db

    async def slow(sql, vars=None):
        await asyncio.sleep(10)

    monkeypatch.setattr(core.db, "q", slow)
    async with client as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
