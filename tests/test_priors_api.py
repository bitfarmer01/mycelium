"""Feature 31 — /learning/priors returns per-shape prior values with edit history matching DB rows."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import learn
from core.contracts import ROLES, SHAPES

_DB_PRIOR = {"who-owns": [0.5, 0.3, 0.2]}
_DB_HISTORY = [
    {"id": "ledger:1", "target": "who-owns", "delta": 0.1, "before": 0.2, "after": 0.3,
     "cause": "correction literal→intent", "ts": "2026-01-01T00:00:00+00:00"},
    {"id": "ledger:2", "target": "who-owns", "delta": -0.02, "before": 0.3, "after": 0.28,
     "cause": "decay tick", "ts": "2026-01-02T00:00:00+00:00"},
    {"id": "ledger:3", "target": "factual", "delta": 0.3, "before": 1 / 3, "after": 0.633,
     "cause": "correction scope→intent", "ts": "2026-01-03T00:00:00+00:00"},
]


def _fake_q(monkeypatch):
    async def q(sql, vars=None):
        if "FROM question_shape" in sql:
            return [{"id": "question_shape:who-owns", "shape": "who-owns", "prior": _DB_PRIOR["who-owns"]}]
        if "FROM ledger" in sql:
            return list(_DB_HISTORY)
        return []

    import core.db

    monkeypatch.setattr(core.db, "q", q)
    return q


def test_snapshot_prior_matches_db_rows_exactly(monkeypatch):
    _fake_q(monkeypatch)
    snap = asyncio.run(learn.priors_snapshot())

    by_shape = {row["shape"]: row for row in snap["priors"]}
    assert [row["shape"] for row in snap["priors"]] == list(SHAPES)
    assert snap["roles"] == list(ROLES)
    # stored shape: values are the DB rows verbatim
    assert by_shape["who-owns"]["prior"] == [0.5, 0.3, 0.2]
    # unstored shape: uniform default, but its ledger edits still show
    assert by_shape["factual"]["prior"] == [1 / 3] * 3
    assert by_shape["factual"]["edit_count"] == 1
    assert [h["id"] for h in by_shape["factual"]["history"]] == ["ledger:3"]


def test_snapshot_history_is_that_shapes_ledger_rows_in_order(monkeypatch):
    _fake_q(monkeypatch)
    snap = asyncio.run(learn.priors_snapshot())

    who = next(row for row in snap["priors"] if row["shape"] == "who-owns")
    assert who["edit_count"] == 2
    assert [h["id"] for h in who["history"]] == ["ledger:1", "ledger:2"]
    for h in who["history"]:
        assert h["target"] == "who-owns"
    assert who["history"][0]["cause"] == "correction literal→intent"


def test_snapshot_edit_count_sums_ledger_rows_per_shape(monkeypatch):
    _fake_q(monkeypatch)
    snap = asyncio.run(learn.priors_snapshot())

    assert sum(row["edit_count"] for row in snap["priors"]) == len(_DB_HISTORY)


# ---------- API surface ----------

@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_learning_priors_route_serves_db_rows(monkeypatch, client):
    _fake_q(monkeypatch)
    async with client as c:
        r = await c.get("/learning/priors")

    assert r.status_code == 200
    body = r.json()
    who = next(row for row in body["priors"] if row["shape"] == "who-owns")
    assert who["prior"] == [0.5, 0.3, 0.2]
    assert who["edit_count"] == 2
    assert who["history"][0]["delta"] == 0.1


async def test_learning_priors_route_on_dead_db_is_503(monkeypatch, client):
    import core.db

    async def boom(sql, vars=None):
        raise RuntimeError("no db")

    monkeypatch.setattr(core.db, "q", boom)
    async with client as c:
        r = await c.get("/learning/priors")

    assert r.status_code == 503
