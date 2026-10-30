"""Per-user voice preferences (feature 57): volume/speed stored in SurrealDB via
core.prefs and served at /voice/prefs. The db layer is mocked (core.db.q), the
clamping logic runs for real — same split as test_priors_api.py."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from core import prefs


def _fake_db(monkeypatch, rows: list | None = None, store: dict | None = None):
    """Mock core.db.q: an in-memory store keyed by the UPSERT's user var.
    `rows` stands in for the SELECT result; `store` records what was written."""
    import core.db

    store = store if store is not None else {}

    async def q(sql, vars=None):
        v = vars or {}
        if "UPSERT" not in sql:  # read path (a DEFINE + SELECT batch)
            return list(rows) if rows else ([{"user": v["user"], "volume": store[v["user"]]["volume"], "speed": store[v["user"]]["speed"]}] if v.get("user") in store else [])
        cur = store.get(v["user"], {})
        # emulate the coalescing UPSERT: a None var keeps the stored value
        store[v["user"]] = {
            "volume": v["volume"] if v["volume"] is not None else cur.get("volume", prefs.DEFAULT_VOLUME),
            "speed": v["speed"] if v["speed"] is not None else cur.get("speed", prefs.DEFAULT_SPEED),
        }
        return [{"user": v["user"], "volume": store[v["user"]]["volume"], "speed": store[v["user"]]["speed"]}]

    monkeypatch.setattr(core.db, "q", q)
    return store


# ---------------------------------------------------------------- clamping logic --


def test_volume_clamped_into_range():
    assert prefs.clamp(5.0, prefs.VOLUME_RANGE, "volume") == 1.0
    assert prefs.clamp(-1, prefs.VOLUME_RANGE, "volume") == 0.0
    assert prefs.clamp("0.35", prefs.VOLUME_RANGE, "volume") == 0.35


def test_speed_clamped_into_range():
    assert prefs.clamp(0.1, prefs.SPEED_RANGE, "speed") == 0.5
    assert prefs.clamp(9, prefs.SPEED_RANGE, "speed") == 2.0
    assert prefs.clamp(1.25, prefs.SPEED_RANGE, "speed") == 1.25


def test_non_numeric_value_is_an_error_not_a_default():
    with pytest.raises(prefs.PrefsError):
        prefs.clamp("loud", prefs.VOLUME_RANGE, "volume")


def test_empty_user_rejected():
    for bad in ("", "   ", None):
        with pytest.raises(prefs.PrefsError):
            prefs.user_key(bad)


# ----------------------------------------------------------- persistence (mocked db) --


def test_set_then_get_round_trips_exact_values(monkeypatch):
    store = _fake_db(monkeypatch)
    saved = asyncio.run(prefs.set_prefs("raja", volume=0.6, speed=1.5))
    assert (saved["volume"], saved["speed"]) == (0.6, 1.5)
    got = asyncio.run(prefs.get_prefs("raja"))
    assert got["stored"] is True
    assert (got["volume"], got["speed"]) == (0.6, 1.5)
    assert "raja" in store  # the upsert actually wrote


def test_get_without_a_stored_row_returns_documented_defaults(monkeypatch):
    _fake_db(monkeypatch)
    got = asyncio.run(prefs.get_prefs("newcomer"))
    assert (got["volume"], got["speed"], got["stored"]) == (prefs.DEFAULT_VOLUME, prefs.DEFAULT_SPEED, False)


def test_partial_update_keeps_the_other_value(monkeypatch):
    _fake_db(monkeypatch)
    asyncio.run(prefs.set_prefs("raja", volume=0.25, speed=1.75))
    # volume-only update: speed survives; None fields never overwrite
    saved = asyncio.run(prefs.set_prefs("raja", volume=0.9))
    assert saved["speed"] == 1.75
    assert saved["volume"] == 0.9


def test_out_of_range_values_clamped_before_the_db_write(monkeypatch):
    store = _fake_db(monkeypatch)
    saved = asyncio.run(prefs.set_prefs("raja", volume=42, speed=-3))
    assert saved["volume"] == 1.0 and saved["speed"] == 0.5
    assert store["raja"] == {"volume": 1.0, "speed": 0.5}


def test_user_key_trimmed_and_bounded():
    assert prefs.user_key("  raja ") == "raja"
    assert len(prefs.user_key("x" * 500)) == prefs.MAX_USER_LEN


# ------------------------------------------------------------------ API endpoints --


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_put_then_get_voice_prefs_via_http(monkeypatch, client):
    _fake_db(monkeypatch)
    async with client as c:
        r = await c.put("/voice/prefs", json={"user": "raja", "volume": 0.7, "speed": 1.4})
        assert r.status_code == 200
        assert (r.json()["volume"], r.json()["speed"]) == (0.7, 1.4)
        r2 = await c.get("/voice/prefs", params={"user": "raja"})
        assert r2.status_code == 200
        assert r2.json()["stored"] is True
        assert (r2.json()["volume"], r2.json()["speed"]) == (0.7, 1.4)


async def test_put_voice_prefs_validates_user(monkeypatch, client):
    _fake_db(monkeypatch)
    async with client as c:
        r = await c.put("/voice/prefs", json={"user": "  ", "volume": 0.5})
        assert r.status_code == 422


async def test_get_voice_prefs_503_when_db_down(monkeypatch, client):
    import core.db

    async def boom(sql, vars=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(core.db, "q", boom)
    async with client as c:
        r = await c.get("/voice/prefs", params={"user": "raja"})
        assert r.status_code == 503
