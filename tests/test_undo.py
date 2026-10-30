"""Feature 38 — undo reverses the exact prior delta of the correction it targets; only the actor can undo their own."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import learn


def _uniform() -> list[float]:
    return [1 / 3] * 3


def _learn_router(state):
    """Fake SurrealDB covering correct() then undo() against one shared store."""

    def route(sql, vars):
        if "->cites->chunk" in sql:
            return [{"c": []}]
        if sql.startswith("SELECT * FROM question:"):
            return [{"id": "question:q1", "shape": "who-owns", "taken": "literal"}]
        if sql.startswith("SELECT * FROM correction:"):
            state["fetched"] = sql
            return [dict(state["correction"])] if state.get("correction") else []
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": list(state["prior"])}]
        if "UPSERT question_shape" in sql:
            state["prior"] = list(vars["prior"])
            return [{"id": "question_shape:who-owns"}]
        if sql.startswith("UPDATE correction:"):
            state["correction"]["undone"] = vars["u"]
            return [{"id": "correction:1"}]
        if sql.startswith("CREATE ledger"):
            state["ledger"].append(dict(vars["row"]))
            return [{"id": f"ledger:{len(state['ledger'])}"}]
        if sql.startswith("CREATE correction"):
            state["correction"] = dict(vars["row"])
            state["correction"]["id"] = "correction:1"
            state["corrections"].append(state["correction"])
            return [{"id": "correction:1"}]
        if sql.startswith("CREATE"):
            return [{"id": "observation:x"}]
        return []

    return route


def _make_correction(fake_db, state, **kw):
    fake_db.rows[""] = _learn_router(state)
    return asyncio.run(learn.correct("question:q1", "intent", **kw))


def test_undo_restores_prior_exactly(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    out = _make_correction(fake_db, state, confidence=0.6, actor="rajath")
    prior_before_correction = list(out["prior_before"])
    d_applied = out["prior_after"][1] - out["prior_before"][1]

    undone = asyncio.run(learn.undo("correction:1", "rajath"))

    # bit-exact restore of the pre-correction prior
    assert undone["prior_after"] == prior_before_correction
    assert state["prior"] == prior_before_correction
    # the ledger records the exact reversal of the applied delta
    undo_rows = [r for r in state["ledger"] if r["cause"] == "undo correction:1"]
    assert undo_rows and undo_rows[0]["delta"] == pytest.approx(-d_applied, rel=1e-12)
    assert undo_rows[0]["before"] == pytest.approx(prior_before_correction[1] + d_applied, rel=1e-12)
    assert undo_rows[0]["after"] == pytest.approx(prior_before_correction[1], rel=1e-12)
    assert state["correction"]["undone"] is True


def test_undo_denied_to_anyone_but_the_actor(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    _make_correction(fake_db, state, actor="rajath")

    with pytest.raises(PermissionError):
        asyncio.run(learn.undo("correction:1", "mallory"))


def test_undo_requires_the_recorded_actor_even_when_empty(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    _make_correction(fake_db, state, actor="")

    # an unattributed correction is owned by the anonymous actor only
    with pytest.raises(PermissionError):
        asyncio.run(learn.undo("correction:1", "someone"))
    asyncio.run(learn.undo("correction:1", ""))


def test_undo_unknown_correction_is_lookup_error(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    fake_db.rows[""] = _learn_router(state)

    with pytest.raises(LookupError):
        asyncio.run(learn.undo("correction:nope", "rajath"))


def test_undo_refused_after_the_prior_has_moved_on(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    _make_correction(fake_db, state, actor="rajath")

    state["prior"] = [0.2, 0.6, 0.2]  # a later correction moved the prior
    with pytest.raises(ValueError, match="refusing an inexact undo"):
        asyncio.run(learn.undo("correction:1", "rajath"))


def test_undo_twice_is_refused(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    _make_correction(fake_db, state, actor="rajath")
    asyncio.run(learn.undo("correction:1", "rajath"))

    with pytest.raises(ValueError, match="already undone"):
        asyncio.run(learn.undo("correction:1", "rajath"))


def test_correction_record_carries_actor_and_deltas(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    out = _make_correction(fake_db, state, confidence=0.5, actor="rajath")

    assert out["correction_id"] == "correction:1"
    rec = state["correction"]
    assert rec["actor"] == "rajath"
    assert rec["confidence"] == pytest.approx(0.5)
    assert rec["prior_before"] == pytest.approx(_uniform())
    assert rec["prior_after"] == pytest.approx(out["prior_after"])
    assert rec["shape"] == "who-owns"


# ---------- API surface ----------

@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_undo_route_restores_and_denies_strangers(monkeypatch, client):
    import core.db

    calls = []

    async def q(sql, vars=None):
        calls.append(sql)
        if sql.startswith("SELECT * FROM correction:"):
            return [{"id": "correction:1", "shape": "who-owns", "to_role": "intent",
                     "actor": "rajath", "undone": False,
                     "prior_before": [1 / 3] * 3, "prior_after": [0.3, 0.5, 0.2]}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": [0.3, 0.5, 0.2]}]
        if sql.startswith("CREATE ledger"):
            return [{"id": "ledger:9"}]
        return []

    monkeypatch.setattr(core.db, "q", q)
    async with client as c:
        ok = await c.post("/learning/undo", json={"correction_id": "correction:1", "actor": "rajath"})
        denied = await c.post("/learning/undo", json={"correction_id": "correction:1", "actor": "mallory"})

    assert ok.status_code == 200
    body = ok.json()
    assert body["prior_after"] == [pytest.approx(1 / 3)] * 3
    assert denied.status_code == 403


async def test_undo_route_maps_unknown_and_drift_to_404_and_409(monkeypatch, client):
    import core.db

    async def q(sql, vars=None):
        if sql.startswith("SELECT * FROM correction:unknown"):
            return []
        if sql.startswith("SELECT * FROM correction:drift"):
            return [{"id": "correction:drift", "shape": "who-owns", "to_role": "intent",
                     "actor": "rajath", "undone": False,
                     "prior_before": [1 / 3] * 3, "prior_after": [0.3, 0.5, 0.2]}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": [0.9, 0.05, 0.05]}]  # nothing like the correction's result
        return []

    monkeypatch.setattr(core.db, "q", q)
    async with client as c:
        missing = await c.post("/learning/undo", json={"correction_id": "correction:unknown", "actor": "rajath"})
        drifted = await c.post("/learning/undo", json={"correction_id": "correction:drift", "actor": "rajath"})

    assert missing.status_code == 404
    assert drifted.status_code == 409
