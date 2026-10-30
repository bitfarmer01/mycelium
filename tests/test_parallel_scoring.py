"""Feature 50: parallel scoring across more sandboxes — bounded by the 10 GiB org pool.

Semantics: the pool dispatches candidate runs across `size` sandboxes concurrently; the
size is capped at budget_gib // sandbox_gib so the pool can never hold more memory than
the org allows (docs/services.md: "10 GiB total — two daytona-medium or three
daytona-small"). A pool larger than three is therefore available when footprints fit
(EXAM_SANDBOX_GIB), and the call-count asserts below pin both directions.
"""
from __future__ import annotations

import asyncio

import pytest
from fake_provider import FakeSandboxProvider

PACKS = [{"question_id": f"q{i}", "text": f"question {i}", "context": f"context {i}", "n_docs": 3}
         for i in range(5)]
CANDIDATES = {"suffixes": [{"name": f"cand-{i}", "axis": "test", "text": f"Apply rule {i}."} for i in range(5)]}
FILES = {"candidates.json": CANDIDATES,
         "baseline_runs.json": {"runs": [[0.5, 0.5], [0.6, 0.4]], "runner": "fake"},
         "heldout_packs.json": PACKS,
         "terms.json": {}}


@pytest.fixture
def exam_files(monkeypatch):
    from core import growth

    monkeypatch.setattr(growth, "_load_json", lambda name: FILES.get(name))


@pytest.fixture
def fake_factory(monkeypatch):
    """Fallback scorer for the no-pool path; unused while the pool is active."""
    async def run(slot: str, packs: list[dict], label: str) -> dict:
        return {"runner": "fake", "sandbox_id": f"fake-{label}", "ms": 1,
                "answers": [{"question_id": p["question_id"], "result": None, "ms": 0} for p in packs]}

    monkeypatch.setattr("core.runner.make_runner", lambda: type("F", (), {"run": staticmethod(run)})())


@pytest.fixture
def fake_pool_5(monkeypatch):
    """Fresh pool singleton over the test double, sized to 5 via EXAM_SANDBOX_GIB=1."""
    from core import exam

    provider = FakeSandboxProvider()
    monkeypatch.setattr(exam, "_pool_provider", lambda: provider)
    monkeypatch.setattr(exam, "_POOL", None)
    monkeypatch.setenv("EXAM_SANDBOX_GIB", "1.0")
    return provider


async def run_all(runner, n: int) -> list[dict]:
    """Fire n candidate runs concurrently, the way growth.improve()'s gather does."""
    return await asyncio.gather(*(runner.run("slot", PACKS, f"cand-{i}") for i in range(n)))


async def test_pool_beyond_three_dispatches_scoring_across_all_five():
    """With footprints that fit, a pool of 5 scores 5 candidates on 5 distinct sandboxes."""
    from core.exam import PooledRunner, SandboxPool

    provider = FakeSandboxProvider()
    pool = SandboxPool(provider, size=5, ttl_seconds=900.0, budget_gib=10.0, sandbox_gib=1.0)
    assert pool.size == 5, "1 GiB footprints: all five requested slots fit the 10 GiB budget"

    results = await run_all(PooledRunner(pool), 5)
    ids = {r["sandbox_id"] for r in results}
    assert provider.create_calls == 5, "call-count assert: scoring used the larger pool"
    assert len(ids) == 5, "each candidate scored on its own pool sandbox"
    stats = pool.stats()
    assert stats["size"] == 5 and stats["capped_by_budget"] == 0


async def test_scoring_never_exceeds_the_ten_gib_budget():
    """Default daytona-small footprints: a size-8 request is capped at 3 live sandboxes."""
    from core.exam import PooledRunner, SandboxPool

    provider = FakeSandboxProvider()
    pool = SandboxPool(provider, size=8, ttl_seconds=900.0, budget_gib=10.0, sandbox_gib=3.0)
    assert pool.size == 3, "three daytona-small fit 10 GiB (docs/services.md)"
    assert pool.stats()["capped_by_budget"] == 5

    results = await run_all(PooledRunner(pool), 6)
    assert provider.create_calls == 3, "call-count assert: no more sandboxes than the budget allows"
    assert len({r["sandbox_id"] for r in results}) == 3
    assert all(len(r["answers"]) == len(PACKS) for r in results), "all six candidates still scored"


async def test_runs_beyond_pool_size_queue_until_a_slot_frees():
    from core.exam import SandboxPool

    provider = FakeSandboxProvider(exec_delay=0.02)
    pool = SandboxPool(provider, size=2, ttl_seconds=900.0, budget_gib=10.0, sandbox_gib=3.0)
    active = 0
    peak = 0

    async def track(sandbox):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await provider.exec(sandbox, "cmd", 1)
        finally:
            active -= 1

    async def work(sandbox):
        await track(sandbox)
        return sandbox

    # drive the pool directly so the concurrency probe wraps run_in itself
    await asyncio.gather(*(pool.run_in(f"c-{i}", work) for i in range(6)))
    assert peak == 2, "runs queued: at most `size` executed concurrently"
    assert provider.create_calls == 2, "queued runs reused the freed slots instead of creating"
    assert pool.stats()["reused"] == 4 and pool.stats()["created"] == 2


async def test_burst_uses_the_larger_pool_and_reports_it(exam_files, fake_factory, fake_db, fake_pool_5):
    """End to end: EXAM_SANDBOX_GIB=1 with pool_slots=5 — five scored rows on five sandboxes."""
    from core import exam

    out = await exam.improve_burst(budget=0, pool_slots=5)
    stats = out["pool"]
    assert stats["size"] == 5 and stats["created"] == 5, "the burst built and used the larger pool"
    scored = [r for r in out["candidates"] if r["status"] == "scored"]
    assert len({r["sandbox_id"] for r in scored}) == 5
    assert stats["budget_gib"] == 10.0, "the 10 GiB budget is part of the reported pool state"
