"""Features 36 — attribution badge: badge count equals the shape's ledger correction events."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import learn


def _ledger_router(state):
    """Fake SurrealDB for learn.correct + learn.correction_count, sharing one ledger store."""

    def route(sql, vars):
        if "->cites->chunk" in sql:
            return [{"c": []}]
        if sql.startswith("SELECT * FROM question:"):
            return [{"id": "question:q1", "shape": "who-owns", "taken": "literal"}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": list(state["prior"])}]
        if "UPSERT question_shape" in sql:
            state["prior"] = list(vars["prior"])
            return [{"id": "question_shape:who-owns"}]
        if sql.startswith("CREATE ledger"):
            state["ledger"].append(dict(vars["row"]))
            return [{"id": f"ledger:{len(state['ledger'])}"}]
        if sql.startswith("CREATE correction"):
            state["corrections"].append(dict(vars["row"]))
            return [{"id": f"correction:{len(state['corrections'])}"}]
        if "SELECT cause FROM ledger" in sql:
            return [r for r in state["ledger"] if r.get("target") == vars["shape"]]
        if sql.startswith("CREATE"):
            return [{"id": "observation:x"}]
        return []

    return route


def _uniform() -> list[float]:
    return [1 / 3] * 3


def test_badge_equals_ledger_correction_events(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    fake_db.rows[""] = _ledger_router(state)

    # three corrections, one decay tick between them
    asyncio.run(learn.correct("question:q1", "intent", actor="rajath"))
    state["ledger"].append({"target": "who-owns", "target_kind": "shape", "type": "prior",
                            "cause": "decay tick", "delta": -0.01, "before": 0.5, "after": 0.49})
    asyncio.run(learn.correct("question:q1", "intent", actor="rajath"))
    asyncio.run(learn.correct("question:q1", "intent", actor="rajath"))

    count = asyncio.run(learn.correction_count("who-owns"))
    corrections = [r for r in state["ledger"]
                   if r["target_kind"] == "shape" and r["type"] == "prior"
                   and str(r.get("cause", "")).startswith("correction")]
    assert len(corrections) == 3
    assert count == len(corrections)  # the badge definition, exactly


def test_decay_and_undo_rows_do_not_count_as_corrections(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    fake_db.rows[""] = _ledger_router(state)
    asyncio.run(learn.correct("question:q1", "intent", actor="rajath"))

    for cause in ("decay tick", "undo correction:1", "answered as literal"):
        state["ledger"].append({"target": "who-owns", "target_kind": "shape", "type": "prior",
                                "cause": cause, "delta": 0.0, "before": 0.5, "after": 0.5})

    assert asyncio.run(learn.correction_count("who-owns")) == 1


def test_badge_is_scoped_to_one_shape(fake_db):
    state = {"prior": _uniform(), "ledger": [], "corrections": []}
    fake_db.rows[""] = _ledger_router(state)
    asyncio.run(learn.correct("question:q1", "intent", actor="rajath"))

    state["ledger"].append({"target": "factual", "target_kind": "shape", "type": "prior",
                            "cause": "correction scope→intent", "delta": 0.3,
                            "before": 1 / 3, "after": 0.633})

    assert asyncio.run(learn.correction_count("who-owns")) == 1
    assert asyncio.run(learn.correction_count("factual")) == 1


# ---------- API surface ----------

@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_attribution_route_counts_shape_corrections(monkeypatch, client):
    import core.db

    async def q(sql, vars=None):
        if "SELECT cause FROM ledger" in sql:
            return [{"cause": "correction literal→intent"}, {"cause": "decay tick"}]
        return []

    monkeypatch.setattr(core.db, "q", q)
    async with client as c:
        r = await c.get("/learning/attribution/who-owns")

    assert r.status_code == 200
    assert r.json() == {"shape": "who-owns", "corrections": 1}


async def test_attribution_route_rejects_unknown_shape(client):
    async with client as c:
        r = await c.get("/learning/attribution/not-a-shape")

    assert r.status_code == 422
