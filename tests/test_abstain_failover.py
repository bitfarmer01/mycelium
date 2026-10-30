"""Feature 30: graceful abstain — with the claude CLI unreachable, /ask answers with HTTP 200 and
a speakable abstain, the ledger records the failure, and non-CLI failures keep their semantics."""
from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from core import llm


def test_clidown_is_an_llmerror_subclass():
    assert issubclass(llm.CLIDown, llm.LLMError)


def test_run_maps_oserror_to_clidown(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "claude")

    monkeypatch.setattr(llm.subprocess, "run", boom)
    with pytest.raises(llm.CLIDown):
        llm.call_json("system", "hi", {"type": "object"})


def test_run_maps_timeout_to_plain_llmerror(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(llm.subprocess, "run", slow)
    with pytest.raises(llm.LLMError) as ei:
        llm.call_json("system", "hi", {"type": "object"})
    assert not isinstance(ei.value, llm.CLIDown)  # timeouts keep their retry/fallback semantics


def test_missing_cli_binary_raises_clidown(monkeypatch):
    calls = []

    def counting_run(*a, **k):
        calls.append(a)
        raise FileNotFoundError(2, "No such file or directory", "/nonexistent/claude-binary")

    monkeypatch.setattr(llm.subprocess, "run", counting_run)
    with pytest.raises(llm.CLIDown):
        llm.call_json("system", "q", {"type": "object"})
    assert len(calls) == 1  # a dead CLI is not retried into correctness


def test_schema_retry_raises_clidown_immediately(monkeypatch):
    n = {"calls": 0}

    def counting_run(*a, **k):
        n["calls"] += 1
        raise OSError("cannot spawn")

    monkeypatch.setattr(llm.subprocess, "run", counting_run)
    with pytest.raises(llm.CLIDown):
        llm.call_json("system", "q", {"type": "object"})
    assert n["calls"] == 1  # fail fast: no schema-retry rounds against a dead CLI


# ---------------------------------------------------------------------------------------------------- ask-path abstain
@pytest.fixture()
def fake_db(monkeypatch):
    """Minimal db.q/db.now doubles; records created questions and ledger rows."""

    class FakeDB:
        def __init__(self) -> None:
            self.rows: list[dict] = []
            self.ledger: list[dict] = []

        async def q(self, _sql, params=None):
            params = params or {}
            if "CONTENT $row" in _sql:
                row = dict(params["row"])
                row["id"] = f"question:{len(self.rows) + 1}"
                self.rows.append(row)
                return [row]
            return []

        @staticmethod
        def now():
            return "2026-01-01T00:00:00Z"

    import core.db as db_module

    fake = FakeDB()
    monkeypatch.setattr(db_module, "q", fake.q)
    monkeypatch.setattr(db_module, "now", fake.now)
    return fake


@pytest.fixture()
def fake_learn(monkeypatch):
    import core.learn as learn_module

    recorded: list[dict] = []

    async def noop(*a, **k):
        return None

    async def rec(row):
        recorded.append(row.to_dict() if hasattr(row, "to_dict") else dict(row))

    monkeypatch.setattr(learn_module, "get_prior", noop, raising=False)
    monkeypatch.setattr(learn_module, "record_answer", noop, raising=False)
    monkeypatch.setattr(learn_module, "write_ledger", rec, raising=False)
    return recorded


@pytest.fixture()
def fake_llm(monkeypatch):
    """call_json that raises queued items: a BaseException is raised as-is, an Exception too."""
    queue: list[BaseException] = []

    async def call(*_a, **_k):
        if queue:
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise AssertionError("no queued response")

    monkeypatch.setattr(llm, "call_json", call)
    return queue


@pytest.fixture()
def fake_retrieve(monkeypatch):
    import core.retrieve as retrieve_module
    from core.contracts import Citation

    async def retrieve(_q, k=8):
        cits = [Citation(n=i + 1, chunk_id=f"c{i}", title=f"Doc {i}", link="", snippet="t", cosine=0.9)
                for i in range(2)]
        return "\n".join(f"[Document {c.n}] {c.title}\ntext" for c in cits), cits, []

    monkeypatch.setattr(retrieve_module, "retrieve", retrieve)


async def test_ask_abstains_when_cli_down(fake_db, fake_learn, fake_llm, fake_retrieve, monkeypatch):
    from core import reflex

    fake_llm.append(llm.CLIDown("claude failed to start: not found"))
    out = await reflex.ask("what is X?", "s1")
    assert out["abstained"] is True
    assert out["abstain_reason"] == "cli_unreachable"
    assert out["confidence"] == "low"
    assert "unreachable" in out["spoken"].lower()
    assert out["claims"] == [] and out["citations"] == []
    assert fake_learn and fake_learn[0]["type"] == "abstain"  # ledger records the failure


async def test_ask_abstain_survives_broken_ledger(fake_db, fake_llm, fake_retrieve, monkeypatch):
    from core import reflex

    async def broken_ledger(row):
        raise RuntimeError("db down")

    import core.learn as learn_module

    monkeypatch.setattr(learn_module, "write_ledger", broken_ledger, raising=False)
    fake_llm.append(llm.CLIDown("down"))
    out = await reflex.ask("what is X?", "s1")  # must not raise
    assert out["abstained"] is True


async def test_ask_abstain_record_is_typed(fake_db, fake_learn, fake_llm, fake_retrieve):
    from core import reflex

    fake_llm.append(llm.CLIDown("down"))
    await reflex.ask("how do I ship?", "s9")
    row = fake_learn[0]
    assert row["type"] in ("abstain",)  # contracts.LEDGER_TYPES now carries abstain
    assert row["target_kind"] == "question"
    assert "CLI" in row["cause"]


async def test_readings_still_falls_back_for_plain_llmerror(fake_db, fake_learn, fake_llm, fake_retrieve):
    from core import reflex

    fake_llm.append(llm.LLMError("claude timed out"))  # not CLI-down: readings falls back to literal
    fake_llm.append({"spoken": "x", "screen": "y"})  # the synthesis call then succeeds
    out = await reflex.ask("what is X?", "s1")
    assert "abstained" not in out  # a plain LLMError is not an abstention
    assert out["taken"] == "literal"  # the literal fallback still serves a real answer


# ---------------------------------------------------------------------------------------------------- route level
async def test_ask_route_returns_http_200_abstain(fake_db, fake_learn, fake_llm, fake_retrieve, monkeypatch):
    fake_llm.append(llm.CLIDown("claude failed to start"))
    from app.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post("/ask", json={"text": "what is X?", "session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["abstained"] is True and "unreachable" in body["spoken"].lower()
    assert fake_learn and fake_learn[0]["type"] == "abstain"
    assert json.dumps(body)  # serializable


def test_ledger_types_include_abstain():
    from core.contracts import LEDGER_TYPES

    assert "abstain" in LEDGER_TYPES
