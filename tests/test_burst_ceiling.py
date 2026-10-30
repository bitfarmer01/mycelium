"""Feature 45: a burst configured to 2 calls stops at 2 even with 5 packs queued.

"Call" = one model call = one pack scored (the sandbox exam script runs one claude
invocation per pack). The ceiling binds at core.runner.make_runner, the seam where
growth.improve() builds its scorer.
"""
from __future__ import annotations

import httpx
import pytest

PACKS = [{"question_id": f"q{i}", "text": f"question {i}", "context": f"context {i}", "n_docs": 3}
         for i in range(5)]  # 5 packs queued = 5 model calls if uncapped
CANDIDATES = {"suffixes": [{"name": f"cand-{i}", "axis": "test", "text": f"Apply rule {i}."} for i in range(5)]}


class FakeRunner:
    """Records every run() and the packs it actually received; answers with nothing scoreable."""

    def __init__(self):
        self.runs: list[list[dict]] = []

    async def run(self, slot: str, packs: list[dict], label: str) -> dict:
        self.runs.append(list(packs))
        return {"runner": "fake", "sandbox_id": f"fake-{label}", "ms": 1,
                "answers": [{"question_id": p["question_id"], "result": None, "ms": 0} for p in packs]}


@pytest.fixture
def exam_files(monkeypatch):
    """Growth's JSON inputs without touching disk: 5 clean candidates, baseline, 5 packs, no terms."""
    from core import growth

    files = {
        "candidates.json": CANDIDATES,
        "baseline_runs.json": {"runs": [[0.5, 0.5], [0.6, 0.4]], "runner": "fake"},
        "heldout_packs.json": PACKS,
        "terms.json": {},
    }
    monkeypatch.setattr(growth, "_load_json", lambda name: files.get(name))


@pytest.fixture
def fake_factory(monkeypatch):
    """make_runner returns a fresh FakeRunner recording cross-candidate pack totals."""
    fake = FakeRunner()
    monkeypatch.setattr("core.runner.make_runner", lambda: fake)
    return fake


async def test_budgeted_runner_truncates_packs_and_then_raises_hard(fake_db):
    from core.exam import BudgetedRunner, BurstBudgetExhausted

    inner = FakeRunner()
    capped = BudgetedRunner(inner, budget=2)

    res = await capped.run("slot", PACKS, "first")
    assert len(res["answers"]) == 2, "first run consumes exactly the remaining budget in packs"
    assert inner.runs == [PACKS[:2]]
    assert capped.calls_used == 2 and capped.exhausted is True

    with pytest.raises(BurstBudgetExhausted):
        await capped.run("slot", PACKS, "second")
    assert len(inner.runs) == 1, "no further model calls after the budget is spent"


async def test_burst_of_5_candidates_with_2_call_budget_scores_exactly_2_calls(exam_files, fake_factory, fake_db):
    from core.exam import improve_burst

    out = await improve_burst(budget=2)
    total_packs = sum(len(r) for r in fake_factory.runs)
    assert total_packs == 2, f"hard stop at 2 model calls, saw {total_packs}"
    assert out["budget"] == 2 and out["calls_used"] == 2 and out["stopped_early"] is True

    by_status = {}
    for row in out["candidates"]:
        by_status.setdefault(row["status"], []).append(row)
    assert len(by_status["scored"]) == 1
    assert len(by_status["error"]) == 4
    assert all("BurstBudgetExhausted" in r["veto_reason"] for r in by_status["error"])


async def test_uncapped_burst_scores_every_queued_pack(exam_files, fake_factory, fake_db):
    from core.exam import improve_burst

    out = await improve_burst(budget=0)
    total_packs = sum(len(r) for r in fake_factory.runs)
    assert len(fake_factory.runs) == 5 and total_packs == 25, "no ceiling: 5 candidates x 5 packs"
    assert "budget" not in out and "calls_used" not in out


async def test_burst_budget_is_env_configured(monkeypatch):
    from core.exam import burst_budget

    monkeypatch.delenv("EXAM_BURST_MAX_CALLS", raising=False)
    assert burst_budget() == 0
    monkeypatch.setenv("EXAM_BURST_MAX_CALLS", "2")
    assert burst_budget() == 2
    monkeypatch.setenv("EXAM_BURST_MAX_CALLS", "not-a-number")
    assert burst_budget() == 0


async def test_budgeted_burst_writes_candidates_and_decisions_for_the_record(exam_files, fake_factory, fake_db):
    """Feature 41 contract on the capped path: bursts persist candidate rows, fitness deltas, decisions."""
    from core.exam import improve_burst

    await improve_burst(budget=2)
    creates = [v for sql, v in fake_db.calls if "CREATE candidate" in sql]
    ledger = [v for sql, v in fake_db.calls if "CREATE ledger" in sql]
    assert len(creates) == 5, "one candidate row per queued candidate"
    scored = [c["c"] for c in creates if c["c"]["status"] == "scored"]
    errored = [c["c"] for c in creates if c["c"]["status"] == "error"]
    assert len(scored) == 1 and scored[0]["delta"] is not None, "scored candidate carries its fitness delta"
    assert all("BurstBudgetExhausted" in (c["veto_reason"] or "") for c in errored)
    assert ledger, "decisions (gate events) land in the ledger"
    assert all(l["row"]["type"] in ("gate", "veto") for l in ledger)


async def test_manual_improve_route_is_not_the_budgeted_path(monkeypatch):
    """/exam/burst is the capped entry point; /improve must keep the unlimited growth.improve binding."""
    async def improve():
        return {"budget": None}

    async def improve_burst():
        return {"budget": 2}

    monkeypatch.setattr("core.growth.improve", improve)
    monkeypatch.setattr("core.exam.improve_burst", improve_burst)

    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r1 = await c.post("/improve")
        r2 = await c.post("/exam/burst")
    assert r1.json() == {"budget": None}
    assert r2.json() == {"budget": 2}
