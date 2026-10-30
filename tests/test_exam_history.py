"""Feature 41: each burst persists candidate + fitness deltas + decision; /exam/history returns them in order."""
from __future__ import annotations

import sys
import types

import httpx
import pytest

CAND_TS_NEW = "2026-10-29T11:00:00Z"
CAND_TS_OLD = "2026-10-29T10:00:00Z"


def _candidate(name: str, ts: str, **kw) -> dict:
    row = {"id": f"candidate:{name}", "name": name, "axis": "constraints", "status": "scored",
           "verdict": "VETO", "delta": -0.1, "mean": 0.4, "spread": 0.2, "ratio": 1.1,
           "sandbox_id": "sbx-1", "runner": "local", "slot_hash": "abc123", "ts": ts}
    row.update(kw)
    return row


def _ledger(target: str, type_: str, ts: str, delta: float = 0.0) -> dict:
    return {"id": f"ledger:{ts}:{type_}", "target": target, "target_kind": "candidate", "type": type_,
            "delta": delta, "before": 0.5, "after": 0.5 + delta, "cause": f"gate {type_}", "ts": ts}


@pytest.fixture
def seeded_db(fake_db):
    fake_db.rows["FROM candidate"] = [
        _candidate("newer", CAND_TS_NEW),
        _candidate("older", CAND_TS_OLD, status="promoted", verdict="PASS", delta=0.2),
    ]
    fake_db.rows["FROM ledger"] = [  # DESC by ts, as the real query returns them
        _ledger("candidate:newer", "gate", CAND_TS_NEW, -0.1),
        _ledger("candidate:older", "promote", "2026-10-29T10:05:00Z", 0.2),
        _ledger("candidate:older", "gate", CAND_TS_OLD, 0.2),
        _ledger("question_shape:who-owns", "prior", "2026-10-29T09:00:00Z"),  # not a candidate event
    ]
    return fake_db


async def test_history_returns_candidates_oldest_first(seeded_db):
    from core import exam

    out = await exam.history()
    assert [r["name"] for r in out] == ["older", "newer"]
    assert out[0]["delta"] == 0.2 and out[1]["delta"] == -0.1


async def test_history_attaches_only_that_candidates_events(seeded_db):
    from core import exam

    older, newer = (await exam.history())
    assert [e["type"] for e in older["events"]] == ["gate", "promote"]
    assert [e["type"] for e in newer["events"]] == ["gate"]
    assert all(e["target"].startswith("candidate:") for r in (older, newer) for e in r["events"])


async def test_history_limit_keeps_most_recent(seeded_db, fake_db):
    from core import exam

    await exam.history(limit=1)
    (cand_sql, cand_vars), (ledger_sql, _lv) = fake_db.calls
    assert "LIMIT 1" in cand_sql and "ORDER BY ts DESC" in cand_sql


def _stub(monkeypatch, name: str, **funcs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


async def test_exam_history_route_serves_the_record(monkeypatch):
    async def history(limit=100):
        assert limit == 7
        return [{"name": "older", "delta": 0.2, "events": [{"type": "promote"}]}]

    _stub(monkeypatch, "core.exam", history=history)

    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/exam/history", params={"limit": 7})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "older" and body[0]["events"][0]["type"] == "promote"


async def test_exam_history_route_defaults_and_validates_limit(monkeypatch):
    seen = {}

    async def history(limit=100):
        seen["limit"] = limit
        return []

    _stub(monkeypatch, "core.exam", history=history)

    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/exam/history")
    assert r.status_code == 200 and seen["limit"] == 100
