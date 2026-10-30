"""Optional bearer-token auth (feature 8): with MYCELIUM_AUTH_TOKEN unset
nothing changes; set, every unauthenticated request gets 401 except /healthz,
and a matching `Authorization: Bearer` passes.
"""
from __future__ import annotations

import sys
import types

import httpx
import pytest

TOKEN = "s3cret-token"


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


@pytest.fixture
def stubbed(monkeypatch):
    async def ask(text, session_id):
        return {"spoken": "ok", "question_id": "question:x"}

    _stub(monkeypatch, "core.reflex", ask=ask)


async def test_token_unset_changes_nothing(monkeypatch, stubbed, client):
    monkeypatch.delenv("MYCELIUM_AUTH_TOKEN", raising=False)
    async with client as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.post("/ask", json={"text": "q", "session_id": "s"})).status_code == 200
        assert (await c.get("/vitals")).status_code == 200


async def test_token_set_unauthenticated_gets_401_except_healthz(monkeypatch, stubbed, client):
    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", TOKEN)
    async with client as c:
        assert (await c.get("/healthz")).status_code == 200
        r = await c.post("/ask", json={"text": "q", "session_id": "s"})
        assert r.status_code == 401 and "bearer token" in r.json()["detail"]
        assert (await c.get("/vitals")).status_code == 401
        assert (await c.get("/readyz")).status_code == 401


async def test_token_set_valid_bearer_passes(monkeypatch, stubbed, client):
    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", TOKEN)
    async with client as c:
        r = await c.post("/ask", json={"text": "q", "session_id": "s"},
                         headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert (await c.get("/healthz", headers={"Authorization": "Bearer nope"})).status_code == 200


async def test_wrong_token_gets_401(monkeypatch, stubbed, client):
    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", TOKEN)
    async with client as c:
        r = await c.get("/vitals", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_401_still_carries_request_id(monkeypatch, stubbed, client):
    monkeypatch.setenv("MYCELIUM_AUTH_TOKEN", TOKEN)
    async with client as c:
        r = await c.get("/vitals")
    assert r.status_code == 401
    assert r.headers.get("x-request-id"), "auth rejections stay inside the request-id middleware"
