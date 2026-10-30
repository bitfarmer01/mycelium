"""Exam controls: burst history (41), poison detection (42), scheduled bursts (43),
burst cost ceiling (45), warm sandbox pool (46), parallel scoring (50).

Workstream E surface. growth.improve() stays the manual candidate-promotion burst
(D-owned, untouched); this module wraps it with a hard cost ceiling, drives scheduled
runs on an injectable clock, classifies the pool's poison candidates against burst
results, and reads the persisted exam record back out in order.

The ceiling binds at core.runner.make_runner — the seam where improve() builds its
scorer — via a module-attribute swap held for the duration of one burst. Bursts are
serialized by a module lock so two concurrent bursts cannot fight over the seam.
The warm pool (46) binds at the same seam: when enabled, make_runner hands improve()
a PooledRunner whose sandboxes survive across bursts within a TTL, and whose size is
capped by the org memory budget (docs/services.md: 10 GiB — three daytona-small).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core import db

if TYPE_CHECKING:
    from core.runner import RunResult

log = logging.getLogger("mycelium.exam")

ROOT = Path(__file__).resolve().parents[1]
STATE_NAME = "exam_last_burst.txt"
HISTORY_LIMIT_MAX = 500
LEDGER_SCAN_LIMIT = 1000

CANDIDATE_FIELDS = ("name", "axis", "status", "veto_reason", "verdict", "mean", "spread", "delta",
                    "ratio", "sandbox_id", "runner", "slot_hash", "ts")


# ---------------------------------------------------------------------------------------------------- 41: history
async def history(limit: int = 100) -> list[dict]:
    """Exam record oldest -> newest: one entry per persisted candidate with its ledger decisions attached.

    Fitness deltas live on the candidate row (delta/mean/spread) and on its gate/veto/promote
    ledger events; the decision trail is what /exam/history serves.
    """
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


# ---------------------------------------------------------------------------------------------------- 42: poison report
DEFAULT_POISON_MODES = {
    "no-citations": "citations_dropped: the candidate forbids source citations",
    "ignore-wiki": "context_ignored: the candidate discards the wiki context",
    "invent": "fabrication: the candidate licenses invented sources or titles",
}
GENERIC_POISON_MODE = "poisoned_instruction: the candidate degrades grounded answering"


def poison_threshold() -> float:
    """Score below which a poison candidate is confirmed degraded (EXAM_POISON_THRESHOLD)."""
    try:
        return float(os.environ.get("EXAM_POISON_THRESHOLD", "0.5"))
    except ValueError:
        return 0.5


def _failure_mode(entry: dict) -> str:
    """Name how the poison fails: the pool's own label, then the known defaults, then generic."""
    mode = entry.get("failure_mode")
    if mode:
        return str(mode)
    name = str(entry.get("name", ""))
    for key, named in DEFAULT_POISON_MODES.items():
        if key in name:
            return named
    return GENERIC_POISON_MODE


def _poison_status(row: dict | None, threshold: float) -> tuple[str, bool]:
    """(status, promotion_risk) for one poison against its candidate row, if any."""
    if row is None:
        return "not_run", False
    status = str(row.get("status") or "")
    if status == "vetoed_prelint":
        return "vetoed", False
    if status == "error":
        return "errored", False
    mean = row.get("mean")
    if mean is None:
        return "not_run", False
    if float(mean) < threshold:
        return "detected", False
    return "escaped", str(row.get("verdict")) == "PASS"


def poison_report(candidates: list[dict], pool: dict | None, threshold: float) -> dict:
    """Pure: classify the pool's poison candidates against burst candidate rows (feature 42).

    A poison is `detected` when it scored below threshold (degraded as designed),
    `escaped` when it scored at/above it — inert, or damage the scorer cannot see — and
    carries promotion_risk when its verdict was PASS. `vetoed` poisons were stopped by
    lint before any sandbox; `errored` runs never produced a score; `not_run` poisons
    were absent from this burst. The report names the failure mode either way.
    """
    by_name = {str(r.get("name")): r for r in (candidates or []) if r.get("name")}
    entries: list[dict] = []
    escaped: list[str] = []
    for entry in (pool or {}).get("poisons", []):
        name = str(entry.get("name", ""))
        row = by_name.get(name)
        status, risk = _poison_status(row, threshold)
        if status == "escaped":
            escaped.append(name)
        entries.append({"name": name, "failure_mode": _failure_mode(entry), "status": status,
                        "mean": row.get("mean") if row else None,
                        "veto_reason": row.get("veto_reason") if row else None,
                        "promotion_risk": risk})
    return {"threshold": threshold, "entries": entries, "escaped": escaped, "escaped_count": len(escaped)}


def pool_definition() -> dict:
    """The pool poisons are defined against: data/candidates.json or the built-in defaults."""
    from core import growth

    return growth._load_json("candidates.json") or growth.DEFAULT_POOL


async def standing_poison_report() -> dict:
    """Poison report over persisted candidates, no burst required (feature 42, GET /exam/poisons)."""
    rows = await db.q("SELECT name, status, veto_reason, mean, verdict FROM candidate ORDER BY ts DESC LIMIT 200")
    return poison_report(list(rows or []), pool_definition(), poison_threshold())


# ---------------------------------------------------------------------------------------------------- 45: burst ceiling
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
def _burst_scoring_runner(cap: int):
    """Bind the burst's scorer at the seam growth.improve() uses to build it.

    A cap wraps the original scorer in a BudgetedRunner. The pool seam (46/50) lands
    with the warm sandbox pool workstream batch.
    """
    from core import runner as runner_mod

    original = runner_mod.make_runner
    made: list[BudgetedRunner] = []

    def make():
        inner = original()
        b = BudgetedRunner(inner, cap) if cap > 0 else inner
        made.append(b)
        return b

    runner_mod.make_runner = make
    try:
        yield made
    finally:
        runner_mod.make_runner = original


_burst_lock = asyncio.Lock()


async def improve_burst(budget: int | None = None) -> dict:
    """growth.improve() under a hard per-burst cost ceiling (feature 45).

    Budget mode trades statistical power for cost control: scored candidates see fewer
    packs than the baseline runs did. budget=0/None means unlimited. Every burst path
    returns the poison report (42) over its candidate rows.
    """
    from core import growth

    cap = burst_budget() if budget is None else max(0, int(budget))
    made: list[BudgetedRunner] = []
    async with _burst_lock:
        if cap <= 0:
            out = await growth.improve()
        else:
            with _burst_scoring_runner(cap) as ctx:
                made = ctx
                out = await growth.improve()
    if cap > 0:
        used = getattr(made[0], "calls_used", 0) if made else 0
        out["budget"] = cap
        out["calls_used"] = used
        out["stopped_early"] = used >= cap and any(
            "BurstBudgetExhausted" in str(r.get("veto_reason") or "") for r in out.get("candidates", [])
        )
    out["poisons"] = poison_report(out.get("candidates", []), pool_definition(), poison_threshold())
    return out


# ---------------------------------------------------------------------------------------------------- 43: scheduler
def _state_path() -> Path:
    local = Path.cwd() / "data" / STATE_NAME
    return local if local.parent.exists() else ROOT / "data" / STATE_NAME


def schedule_interval_minutes() -> int:
    """Configured burst interval (EXAM_INTERVAL_MINUTES); 0 = scheduled bursts off."""
    try:
        return max(0, int(os.environ.get("EXAM_INTERVAL_MINUTES", "0")))
    except ValueError:
        return 0


def read_last_burst(path: Path | None = None) -> _dt.datetime | None:
    f = path or _state_path()
    try:
        ts = _dt.datetime.fromisoformat(f.read_text().strip())
        return ts if ts.tzinfo else ts.replace(tzinfo=_dt.UTC)
    except (OSError, ValueError):
        return None


def write_last_burst(ts: _dt.datetime, path: Path | None = None) -> None:
    f = path or _state_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(ts.isoformat() + "\n")


class ExamScheduler:
    """Config-driven exam trigger with an injectable clock (feature 43).

    tick() runs a burst when the configured interval has elapsed since the last one and
    records the run. Missing state counts as due: an operator who configures an interval
    is asking for a burst, and the system has been waiting. Manual /improve is untouched.
    """

    def __init__(self, interval_minutes: int | None = None, clock: Callable[[], _dt.datetime] | None = None,
                 improve_fn: Callable[[], dict] | None = None, state_path: Path | None = None):
        self.interval = schedule_interval_minutes() if interval_minutes is None else int(interval_minutes)
        self.clock = clock or (lambda: _dt.datetime.now(_dt.UTC))
        self.improve_fn = improve_fn or improve_burst
        self.state_path = state_path

    def due(self, now: _dt.datetime | None = None) -> bool:
        if self.interval <= 0:
            return False
        last = read_last_burst(self.state_path)
        if last is None:
            return True
        return (now or self.clock()) - last >= _dt.timedelta(minutes=self.interval)

    async def tick(self, now: _dt.datetime | None = None) -> dict | None:
        """Run the exam burst if due; None when the interval has not elapsed."""
        if not self.due(now):
            return None
        out = await self.improve_fn()
        write_last_burst(now or self.clock(), self.state_path)
        return out


async def scheduler_loop(scheduler: ExamScheduler, poll_seconds: float = 60.0) -> None:
    """Drive tick() forever; a failed burst is logged and retried on the next poll."""
    while True:
        try:
            await scheduler.tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  graceful degradation: the API must outlive a failed burst
            log.warning("scheduled exam burst failed: %s", type(e).__name__)
        await asyncio.sleep(poll_seconds)
