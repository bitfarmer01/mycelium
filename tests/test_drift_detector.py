"""Feature 39 — paraphrases landing in a different shape fire a warning into the ledger feed."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import drift
from core.contracts import LEDGER_TYPES, TARGET_KINDS


def test_is_drift_pure_cases():
    assert drift.is_drift("who-owns", "how-to")
    assert not drift.is_drift("who-owns", "who-owns")
    assert not drift.is_drift("who-owns", "")  # unknown classification never counts as drift
    assert not drift.is_drift("", "how-to")


def test_drifted_paraphrase_warns_into_ledger_feed(fake_db):
    fake_db.rows["SELECT id, shape, question FROM"] = [
        {"id": "question:q1", "shape": "who-owns", "question": "Who owns the roadmap?"}]
    fake_db.rows["CREATE ledger"] = [{"id": "ledger:1"}]

    out = asyncio.run(drift.check("question:q1", "How do I file a ticket?",
                                  classify=lambda p: "how-to"))

    assert out["drifted"] is True
    assert out["original_shape"] == "who-owns" and out["paraphrase_shape"] == "how-to"
    assert out["ledger_id"] == "ledger:1"
    lids = [(s, v) for s, v in fake_db.calls if s.startswith("CREATE ledger")]
    assert len(lids) == 1
    row = lids[0][1]["row"]
    assert row["type"] == "drift" and row["target_kind"] == "question" and row["target"] == "question:q1"
    assert "how-to" in row["cause"] and "who-owns" in row["cause"]


def test_same_shape_paraphrase_is_silent(fake_db):
    fake_db.rows["SELECT id, shape, question FROM"] = [
        {"id": "question:q1", "shape": "who-owns", "question": "Who owns the roadmap?"}]
    out = asyncio.run(drift.check("question:q1", "Who is accountable for the roadmap?",
                                  classify=lambda p: "who-owns"))
    assert out["drifted"] is False and out["ledger_id"] is None
    assert not any(s.startswith("CREATE ledger") for s in fake_db.sql())


def test_default_classifier_uses_one_model_call(fake_db, fake_llm):
    fake_db.rows["SELECT id, shape, question FROM"] = [
        {"id": "question:q1", "shape": "who-owns", "question": "Who owns the roadmap?"}]
    fake_llm.push({"shape": "policy"})
    out = asyncio.run(drift.check("question:q1", "Is the roadmap public?"))
    assert out["paraphrase_shape"] == "policy" and out["drifted"] is True
    assert len(fake_llm.calls) == 1


def test_unknown_question_raises(fake_db):
    with pytest.raises(LookupError):
        asyncio.run(drift.check("question:missing", "anything", classify=lambda p: "who-owns"))


async def test_drift_check_route_maps_unknown_to_404(monkeypatch):
    async def fake_q(sql, vars=None):
        if "SELECT id, shape, question FROM" in sql:
            return []  # no such question
        raise AssertionError(f"unexpected sql: {sql}")

    import core.db

    monkeypatch.setattr(core.db, "q", fake_q)
    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        r = await c.post("/learning/drift-check",
                         json={"question_id": "question:missing", "paraphrase": "x"})
    assert r.status_code == 404


async def test_drift_check_route_returns_verdict(monkeypatch):
    async def fake_q(sql, vars=None):
        if "SELECT id, shape, question FROM" in sql:
            return [{"id": "question:q1", "shape": "who-owns", "question": "Who owns the roadmap?"}]
        if sql.startswith("CREATE ledger"):
            return [{"id": "ledger:1"}]
        raise AssertionError(f"unexpected sql: {sql}")

    import core.db

    monkeypatch.setattr(core.db, "q", fake_q)

    real_check = drift.check

    async def stub_check(question_id, paraphrase, classify=None):
        return await real_check(question_id, paraphrase, classify=lambda p: "how-to")

    monkeypatch.setattr(drift, "check", stub_check)
    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        r = await c.post("/learning/drift-check",
                         json={"question_id": "question:q1", "paraphrase": "How do I file a ticket?"})
    assert r.status_code == 200
    body = r.json()
    assert body["drifted"] is True and body["ledger_id"] == "ledger:1"


def test_ledger_drift_type_is_a_registered_contract():
    """The warning row type is part of the shared contract, not a magic string."""
    assert "drift" in LEDGER_TYPES
    assert "question" in TARGET_KINDS
