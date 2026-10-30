"""Feature 46: a second burst within pool TTL starts scoring without a create-sandbox call.

Warm sandboxes are the point of the pool: burst 1 pays the create calls, burst 2 pops
the same sandboxes off the warm list. Past the TTL the sweep deletes them and the next
acquire creates anew. Live Daytona is unreachable (expired Bedrock auth — spec open
question), so every test runs on the test-double provider with the identical interface.
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
def fake_pool(monkeypatch):
    """Fresh pool singleton over the test double; restores the module state afterwards."""
    from core import exam

    provider = FakeSandboxProvider()
    monkeypatch.setattr(exam, "_pool_provider", lambda: provider)
    monkeypatch.setattr(exam, "_POOL", None)
    return provider


def small_pool(provider, size=2, ttl=100.0, budget_gib=10.0, sandbox_gib=3.0, clock=None):
    from core.exam import SandboxPool

    return SandboxPool(provider, size=size, ttl_seconds=ttl, budget_gib=budget_gib,
                       sandbox_gib=sandbox_gib, clock=clock or (lambda: 0.0))


async def test_second_burst_within_ttl_performs_zero_create_sandbox_calls(exam_files, fake_factory, fake_pool, fake_db):
    """The acceptance row: burst 2 inside the TTL reuses warm sandboxes — create count does not move."""
    from core import exam

    out1 = await exam.improve_burst(budget=0, pool_slots=3)
    assert out1["pool"]["created"] == 3, "burst 1 fills the pool's three slots"
    assert fake_pool.create_calls == 3

    out2 = await exam.improve_burst(budget=0, pool_slots=3)
    assert fake_pool.create_calls == 3, "second burst within pool TTL performs zero create-sandbox calls"
    assert out2["pool"]["created"] == 3, "pool-wide create count unchanged: burst 2 created nothing"
    assert out2["pool"]["reused"] == 7, "all five burst-2 runs reused a warm sandbox (2 in burst 1)"

    burst1_ids = {r["sandbox_id"] for r in out1["candidates"] if r["status"] == "scored"}
    burst2_ids = {r["sandbox_id"] for r in out2["candidates"] if r["status"] == "scored"}
    assert burst2_ids <= burst1_ids, "burst 2 scored on burst 1's sandboxes"


async def test_sandboxes_idle_past_ttl_are_swept_and_recreated():
    provider = FakeSandboxProvider()
    now = [0.0]
    pool = small_pool(provider, size=2, ttl=100.0, clock=lambda: now[0])

    await pool.run_in("a", lambda sb: asyncio.sleep(0))
    now[0] = 50.0
    await pool.run_in("b", lambda sb: asyncio.sleep(0))  # within TTL: warm reuse
    assert provider.create_calls == 1 and pool.stats()["reused"] == 1

    now[0] = 200.0  # sandbox from acquire at t=0 is 200s idle > 100s TTL
    await pool.run_in("c", lambda sb: asyncio.sleep(0))
    assert pool.stats()["expired"] == 1, "the idle sandbox was swept"
    assert provider.deleted == ["fake-0"], "the expired sandbox was deleted"
    assert provider.create_calls == 2, "post-TTL acquire creates anew"


async def test_pool_is_off_without_a_provider(monkeypatch):
    from core import exam

    monkeypatch.setattr(exam, "_pool_provider", lambda: None)
    monkeypatch.setattr(exam, "_POOL", None)
    assert exam._get_pool(3) is None


async def test_burst_reports_pool_requested_but_off(exam_files, fake_factory, fake_db, monkeypatch):
    """A configured pool with no provider says so in the burst output instead of failing silently."""
    from core import exam

    monkeypatch.setattr(exam, "_pool_provider", lambda: None)
    monkeypatch.setattr(exam, "_POOL", None)

    out = await exam.improve_burst(budget=0, pool_slots=3)
    assert out["pool"] == {"off": True, "requested_size": 3, "reason": "no sandbox provider"}


async def test_pool_is_off_by_default(exam_files, fake_factory, fake_db, monkeypatch):
    """Without EXAM_POOL_SIZE the burst behaves exactly as before — no pool key at all."""
    from core import exam

    monkeypatch.delenv("EXAM_POOL_SIZE", raising=False)
    out = await exam.improve_burst(budget=0)
    assert "pool" not in out
    assert "poisons" in out  # the poison report rides on every burst path


async def test_failed_run_discards_its_sandbox():
    provider = FakeSandboxProvider()
    pool = small_pool(provider, size=1)

    async def boom(sandbox):
        raise RuntimeError("exec failed")

    with pytest.raises(RuntimeError):
        await pool.run_in("a", boom)
    assert pool.stats()["discarded"] == 1
    assert provider.deleted == ["fake-0"], "a sandbox that watched an error is not recycled warm"
    assert pool.stats()["warm"] == 0


async def test_close_exam_pool_deletes_warm_sandboxes_and_closes_provider():
    from core import exam

    provider = FakeSandboxProvider()
    pool = small_pool(provider, size=2)
    await pool.run_in("a", lambda sb: asyncio.sleep(0))
    exam._POOL = pool
    try:
        await exam.close_exam_pool()
        assert provider.deleted == ["fake-0"] and provider.closed
        assert exam._POOL is None
    finally:
        exam._POOL = None  # the fixture-less path restores module state by hand
