"""Exam controls: burst history (41) and the burst cost ceiling (45).

The write side of history is growth.improve(): every burst persists one candidate row
per candidate (status, verdict, fitness delta/mean/spread) plus gate/veto/promote
ledger events — the candidate, the fitness deltas, and the decision. history() reads
that record back for /exam/history.

The ceiling binds at core.runner.make_runner — the seam where improve() builds its
scorer — via a module-attribute swap held for the duration of one burst. Bursts are
serialized by a module lock so two concurrent bursts cannot fight over the seam.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager

from core import db

HISTORY_LIMIT_MAX = 500
LEDGER_SCAN_LIMIT = 1000

CANDIDATE_FIELDS = ("name", "axis", "status", "veto_reason", "verdict", "mean", "spread", "delta",
                    "ratio", "sandbox_id", "runner", "slot_hash", "ts")


async def history(limit: int = 100) -> list[dict]:
    """Exam record oldest -> newest: one entry per persisted candidate with its ledger decisions attached."""
    limit = max(1, min(int(limit), HISTORY_LIMIT_MAX))
    rows = await db.q(f"SELECT * FROM candidate ORDER BY ts DESC LIMIT {limit}")
    ledger_rows = await db.q(f"SELECT * FROM ledger ORDER BY ts DESC LIMIT {LEDGER_SCAN_LIMIT}")
    # candidate-prefixed targets are filtered client-side: works on any SurrealQL build and on the fake
    by_candidate: dict[str, list[dict]] = {}
    for ev in reversed(ledger_rows):  # reverse the DESC scan back into ascending time
        target = str(ev.get("target", ""))
        if not target.startswith("candidate:"):
            continue
        by_candidate.setdefault(target.removeprefix("candidate:"), []).append(ev)

    out = []
    for row in reversed(rows):  # DESC scan -> ascending order
        rec = {k: row.get(k) for k in CANDIDATE_FIELDS if k in row}
        rec["events"] = by_candidate.get(str(row.get("name", "")), [])
        out.append(rec)
    return out


class BurstBudgetExhausted(RuntimeError):
    """A burst's model-call budget is spent — the hard stop."""


class BudgetedRunner:
    """Runner wrapper capping model calls per burst: each pack is one claude call.

    The first run receives at most the remaining budget in packs; once spent, every
    further run raises instead of calling the wrapped runner, so a queued burst can
    never exceed the configured cost.
    """

    def __init__(self, inner, budget: int):
        self._inner = inner
        self._budget = max(0, int(budget))
        self._remaining = self._budget

    @property
    def calls_used(self) -> int:
        return self._budget - self._remaining

    @property
    def exhausted(self) -> bool:
        return self._remaining <= 0

    async def run(self, slot: str, packs: list[dict], label: str):
        if self._remaining <= 0:
            raise BurstBudgetExhausted(f"burst stopped hard: {self._budget}-call budget spent")
        take = packs[: self._remaining]
        self._remaining -= len(take)
        return await self._inner.run(slot, take, label)


def burst_budget() -> int:
    """Configured per-burst model-call ceiling (EXAM_BURST_MAX_CALLS); 0 = unlimited."""
    try:
        return max(0, int(os.environ.get("EXAM_BURST_MAX_CALLS", "0")))
    except ValueError:
        return 0


@contextmanager
def _capped_make_runner(budget: int):
    """Bind the budget at the seam growth.improve() uses to build its scorer."""
    from core import runner as runner_mod

    original = runner_mod.make_runner
    made: list[BudgetedRunner] = []

    def capped():
        b = BudgetedRunner(original(), budget)
        made.append(b)
        return b

    runner_mod.make_runner = capped
    try:
        yield made
    finally:
        runner_mod.make_runner = original


_burst_lock = asyncio.Lock()


async def improve_burst(budget: int | None = None) -> dict:
    """growth.improve() under a hard per-burst cost ceiling (feature 45).

    Budget mode trades statistical power for cost control: scored candidates see fewer
    packs than the baseline runs did. budget=0/None means unlimited (plain improve()).
    """
    from core import growth

    cap = burst_budget() if budget is None else max(0, int(budget))
    async with _burst_lock:
        if cap <= 0:
            return await growth.improve()
        with _capped_make_runner(cap) as made:
            out = await growth.improve()
    used = made[0].calls_used if made else 0
    out["budget"] = cap
    out["calls_used"] = used
    out["stopped_early"] = used >= cap and any(
        "BurstBudgetExhausted" in str(r.get("veto_reason") or "") for r in out.get("candidates", [])
    )
    return out
