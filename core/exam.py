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
from core.contracts import CONFIG, LedgerRow

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
def _burst_scoring_runner(cap: int, pool: SandboxPool | None):
    """Bind the burst's scorer at the seam growth.improve() uses to build it.

    With a pool, the scorer is the pool's PooledRunner; the base make_runner is not
    consulted (the pool owns the sandbox lifecycle). Without one, the original
    selection applies. A cap wraps whichever scorer in a BudgetedRunner.
    """
    from core import runner as runner_mod

    original = runner_mod.make_runner
    made: list[BudgetedRunner] = []

    def make():
        inner = PooledRunner(pool) if pool is not None else original()
        b = BudgetedRunner(inner, cap) if cap > 0 else inner
        made.append(b)
        return b

    runner_mod.make_runner = make
    try:
        yield made
    finally:
        runner_mod.make_runner = original


_burst_lock = asyncio.Lock()


async def improve_burst(budget: int | None = None, pool_slots: int | None = None) -> dict:
    """growth.improve() under a hard per-burst cost ceiling (45) and, when configured,
    the warm sandbox pool (46/50).

    Budget mode trades statistical power for cost control: scored candidates see fewer
    packs than the baseline runs did. budget=0/None means unlimited. pool_slots=0/None
    keeps the ephemeral-sandbox behavior unless EXAM_POOL_SIZE is set. Every burst path
    returns the poison report (42) over its candidate rows.
    """
    from core import growth

    cap = burst_budget() if budget is None else max(0, int(budget))
    size = pool_size() if pool_slots is None else max(0, int(pool_slots))
    pre_version = growth.slot_version()
    snapshot_slot()  # feature 47: remember what to roll back to before anyone can promote
    made: list[BudgetedRunner] = []
    async with _burst_lock:
        pool = _get_pool(size) if size > 0 else None
        if cap <= 0 and pool is None:
            out = await growth.improve()
        else:
            with _burst_scoring_runner(cap, pool) as ctx:
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
    if pool is not None:
        out["pool"] = pool.stats()
    elif size > 0:  # requested but unavailable — say so instead of failing silently
        out["pool"] = {"off": True, "requested_size": size, "reason": "no sandbox provider"}
    if growth.slot_version() != pre_version:  # a promotion happened — hold it to its follow-up fitness (47)
        out["rollback"] = await check_rollback(candidate=_promoted_candidate(out))
        if out["rollback"].get("reverted"):
            out["slot_version"] = growth.slot_version()  # the report shows the version actually live
    return out


# ---------------------------------------------------------------------------------------------------- 46/50: warm sandbox pool
POOL_GIB_BUDGET_DEFAULT = 10.0  # docs/services.md: "10 GiB total" for the org's sandbox pool
SANDBOX_GIB_DEFAULT = 3.0       # docs/services.md: three daytona-small fit that budget
POOL_TTL_MINUTES_DEFAULT = 15.0


def pool_size() -> int:
    """Configured warm-pool size (EXAM_POOL_SIZE); 0 = pool off, bursts build ephemeral sandboxes."""
    try:
        return max(0, int(os.environ.get("EXAM_POOL_SIZE", "0")))
    except ValueError:
        return 0


def pool_ttl_seconds() -> float:
    """Idle seconds a warm sandbox stays reusable (EXAM_POOL_TTL_MINUTES)."""
    try:
        return max(0.0, float(os.environ.get("EXAM_POOL_TTL_MINUTES", str(POOL_TTL_MINUTES_DEFAULT)))) * 60.0
    except ValueError:
        return POOL_TTL_MINUTES_DEFAULT * 60.0


def pool_budget_gib() -> float:
    """Memory the pool may hold (EXAM_POOL_GIB_BUDGET); docs/services.md caps the org pool at 10 GiB."""
    try:
        return max(0.0, float(os.environ.get("EXAM_POOL_GIB_BUDGET", str(POOL_GIB_BUDGET_DEFAULT))))
    except ValueError:
        return POOL_GIB_BUDGET_DEFAULT


def sandbox_gib() -> float:
    """Memory footprint of one pool sandbox (EXAM_SANDBOX_GIB); a daytona-small is ~3 GiB.

    A pool larger than three needs smaller footprints: the budget cap below enforces
    size <= budget_gib // sandbox_gib either way.
    """
    try:
        return max(0.1, float(os.environ.get("EXAM_SANDBOX_GIB", str(SANDBOX_GIB_DEFAULT))))
    except ValueError:
        return SANDBOX_GIB_DEFAULT


class SandboxProvider(Protocol):
    """Lifecycle seam for pool sandboxes; the live Daytona path and test doubles share this shape."""

    name: str

    async def create(self, label: str) -> Any: ...
    async def upload(self, sandbox, path: str, data: bytes) -> None: ...
    async def exec(self, sandbox, command: str, timeout: float) -> str: ...  # stdout
    async def delete(self, sandbox) -> None: ...
    def env_bytes(self) -> bytes: ...
    def exam_command(self) -> str: ...
    async def close(self) -> None: ...


class DaytonaPoolProvider:
    """Live provider: exam sandboxes stay non-ephemeral so the pool can reuse them (46).

    Mirrors core.runner.DaytonaRunner's lifecycle minus the per-run create/delete: the
    pool creates with auto-stop off (docs/services.md exam posture) and deletes on
    discard or TTL expiry. Auth env is built in memory only, as in the runner, and is
    uploaded once per sandbox rather than once per run.
    """

    name = "daytona"

    def __init__(self, snapshot: str | None = None):
        from daytona import AsyncDaytona, DaytonaConfig

        runner_mod = _runner_mod()
        self.snapshot = snapshot or runner_mod.SNAPSHOT
        self._client = AsyncDaytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))

    async def create(self, label: str) -> Any:
        from daytona import CreateSandboxFromSnapshotParams

        runner_mod = _runner_mod()
        params = CreateSandboxFromSnapshotParams(
            snapshot=self.snapshot, auto_stop_interval=0, ephemeral=False,
            name=f"myc-pool-{label}-{uuid.uuid4().hex[:6]}"[:60], labels={"app": "mycelium", "pool": "exam"})
        sandbox = await self._client.create(params, timeout=240)
        # snapshot ships claude 2.1.19; same upgrade fallback as the ephemeral runner
        await sandbox.process.exec(
            f"bash -lc '{runner_mod.PATHX}; claude --help 2>&1 | grep -q -- --json-schema || "
            "npm i -g @anthropic-ai/claude-code@latest >/tmp/inst.log 2>&1 || "
            "sudo -n npm i -g @anthropic-ai/claude-code >>/tmp/inst.log 2>&1; claude --version'", timeout=600)
        return sandbox

    async def upload(self, sandbox, path: str, data: bytes) -> None:
        await sandbox.fs.upload_file(data, path)

    async def exec(self, sandbox, command: str, timeout: float) -> str:
        resp = await sandbox.process.exec(command, timeout=timeout)
        return resp.result

    async def delete(self, sandbox) -> None:
        try:
            await self._client.delete(sandbox, timeout=120)
        except Exception as e:  # noqa: BLE001 — a failed delete must not fail the burst
            log.warning("pool sandbox delete failed: %s", e)

    def env_bytes(self) -> bytes:
        return _runner_mod().DaytonaRunner._env_bytes()

    def exam_command(self) -> str:
        # unlike the ephemeral runner the env file stays, so a warm sandbox can re-source it
        return f"bash -lc '{_runner_mod().PATHX}; set -a; . /tmp/env; set +a; cd /tmp && python3 /tmp/exam.py'"

    async def close(self) -> None:
        with suppress(Exception):
            await self._client.close()


def _runner_mod():
    from core import runner

    return runner


@dataclass
class _Slot:
    """One pool sandbox plus its warm-state bookkeeping."""

    sandbox: Any
    last_used: float
    label: str = ""

    @property
    def id(self) -> str:
        return str(getattr(self.sandbox, "id", ""))


class SandboxPool:
    """Bounded set of warm exam sandboxes (features 46 + 50).

    Size is capped by the org memory budget: with daytona-small footprints (~3 GiB) at
    most three sandboxes may be alive at once; smaller footprints allow a larger pool.
    Runs queue on a semaphore of that size, so scoring parallelism can never exceed the
    budget. A run that fails discards its sandbox (delete, not recycle) — a sandbox that
    watched an error is not trusted warm. Sandboxes idle past the TTL are deleted on the
    next acquire (lazy sweep): a second burst within the TTL reuses them and performs
    zero create calls.
    """

    def __init__(self, provider: SandboxProvider, *, size: int, ttl_seconds: float,
                 budget_gib: float, sandbox_gib: float, clock: Callable[[], float] = time.monotonic):
        self.provider = provider
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.budget_gib = float(budget_gib)
        self.sandbox_gib = float(sandbox_gib)
        self.requested_size = max(1, int(size))
        fits = int(self.budget_gib // self.sandbox_gib) if self.sandbox_gib > 0 else self.requested_size
        self.size = max(1, min(self.requested_size, fits))
        self.clock = clock
        self._sem = asyncio.Semaphore(self.size)
        self._warm: list[_Slot] = []
        self._stats = {"created": 0, "reused": 0, "expired": 0, "discarded": 0}

    def stats(self) -> dict:
        return {"provider": self.provider.name, "requested_size": self.requested_size,
                "size": self.size, "capped_by_budget": self.requested_size - self.size,
                "budget_gib": self.budget_gib, "sandbox_gib": self.sandbox_gib,
                "ttl_seconds": self.ttl_seconds, "warm": len(self._warm), **self._stats}

    async def _delete(self, slot: _Slot) -> None:
        try:
            await self.provider.delete(slot.sandbox)
        except Exception as e:  # noqa: BLE001
            log.warning("pool delete failed for %s: %s", slot.id, type(e).__name__)

    async def _sweep(self) -> None:
        now = self.clock()
        expired = [s for s in self._warm if now - s.last_used > self.ttl_seconds]
        if expired:
            gone = {id(s) for s in expired}
            self._warm = [s for s in self._warm if id(s) not in gone]
            self._stats["expired"] += len(expired)
            for s in expired:
                await self._delete(s)

    async def _acquire(self, label: str) -> _Slot:
        await self._sweep()
        if self._warm:
            slot = self._warm.pop()
            slot.label = label
            self._stats["reused"] += 1
            return slot
        self._stats["created"] += 1
        return _Slot(sandbox=await self.provider.create(label), last_used=self.clock(), label=label)

    async def run_in(self, label: str, work: Callable[[Any], Any]):
        """Run work(sandbox) in a pool slot: bounded by size, warm within TTL, deleted on failure."""
        async with self._sem:
            slot = await self._acquire(label)
            try:
                out = await work(slot.sandbox)
            except Exception:
                self._stats["discarded"] += 1
                await self._delete(slot)
                raise
            slot.last_used = self.clock()
            self._warm.append(slot)
            return out

    async def close(self) -> None:
        warm, self._warm = self._warm, []
        for s in warm:
            await self._delete(s)
        await self.provider.close()


class PooledRunner:
    """Runner-protocol scorer: every candidate run executes in a pool sandbox (46/50).

    Answers reuse the runner module's exam script, renderer, and parser verbatim — the
    only change vs the ephemeral DaytonaRunner is who owns the sandbox lifecycle (the
    pool, not the run), so a burst of N candidates dispatches across the pool's slots
    and the next burst within the TTL starts with zero create calls.
    """

    name = "daytona-pool"

    def __init__(self, pool: SandboxPool):
        self.pool = pool

    async def run(self, slot: str, packs: list[dict], label: str) -> RunResult:
        runner_mod = _runner_mod()
        t0 = time.monotonic()
        used: dict = {}

        async def work(sandbox) -> list[dict]:
            used["sandbox_id"] = str(getattr(sandbox, "id", ""))
            if not getattr(sandbox, "myc_env_uploaded", False):
                await self.pool.provider.upload(sandbox, "/tmp/env", self.pool.provider.env_bytes())
                try:
                    sandbox.myc_env_uploaded = True
                except AttributeError:
                    pass  # frozen handle: re-uploaded next run (correct, just less warm)
            if not getattr(sandbox, "myc_exam_uploaded", False):
                await self.pool.provider.upload(sandbox, "/tmp/exam.py", runner_mod.exam_script().encode())
                try:
                    sandbox.myc_exam_uploaded = True
                except AttributeError:
                    pass
            items = runner_mod._render(slot, packs)
            await self.pool.provider.upload(sandbox, "/tmp/packs.json", json.dumps(items).encode())
            stdout = await self.pool.provider.exec(sandbox, self.pool.provider.exam_command(),
                                                   timeout=runner_mod.EXEC_TIMEOUT)
            return runner_mod._parse_exam_output(stdout, packs)

        answers = await self.pool.run_in(label, work)
        return {"runner": self.name, "sandbox_id": str(used.get("sandbox_id", "")),
                "answers": answers, "ms": int((time.monotonic() - t0) * 1000)}


_POOL: SandboxPool | None = None


def _pool_provider() -> SandboxProvider | None:
    """Live provider selection: Daytona only when the key is present and Bedrock auth works.

    Dead Bedrock auth (the documented workshop-account expiry) keeps the pool off so
    bursts fall back to the plain make_runner path instead of scoring against
    sandboxes whose claude cannot answer — live bursts wait on fresh credentials.
    """
    if not os.environ.get("DAYTONA_API_KEY"):
        return None
    try:
        if not _runner_mod().bedrock_health():
            log.warning("exam pool off: Bedrock auth unhealthy (live bursts wait on fresh credentials)")
            return None
        return DaytonaPoolProvider()
    except Exception as e:  # noqa: BLE001 — no SDK/key: the pool is simply off
        log.warning("exam pool provider unavailable: %s", type(e).__name__)
        return None


def _get_pool(size: int) -> SandboxPool | None:
    """Module-level pool singleton — warm sandboxes must outlive one burst to be reused (46).

    Returns None when no provider is available; bursts then behave exactly as before.
    The pool is sized from the env at first use and kept for the process lifetime.
    """
    global _POOL
    if _POOL is None:
        provider = _pool_provider()
        if provider is None:
            return None
        _POOL = SandboxPool(provider, size=size, ttl_seconds=pool_ttl_seconds(),
                            budget_gib=pool_budget_gib(), sandbox_gib=sandbox_gib())
    return _POOL


async def close_exam_pool() -> None:
    """Delete warm sandboxes and shut the provider down (ops hook and test reset)."""
    global _POOL
    if _POOL is not None:
        pool, _POOL = _POOL, None
        await pool.close()


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


# ---------------------------------------------------------------------------------------------------- 47: auto-rollback
SNAPSHOT_NAME = "slot_pre_burst.json"


def snapshot_path() -> Path:
    """Where the pre-burst slot snapshot lives (same cwd/data-or-ROOT/data rule as the burst state)."""
    local = Path.cwd() / "data" / SNAPSHOT_NAME
    return local if local.parent.exists() else ROOT / "data" / SNAPSHOT_NAME


def read_snapshot(path: Path | None = None) -> dict | None:
    try:
        snap = json.loads((path or snapshot_path()).read_text())
        return snap if isinstance(snap, dict) and "slot" in snap and "version" in snap else None
    except (OSError, ValueError):
        return None


def snapshot_slot(path: Path | None = None) -> None:
    """Record the current slot text and version before a burst, so a regressing promotion can be undone (47)."""
    p = path or snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": growth_version(), "slot": growth_slot()}, indent=2) + "\n")


def clear_snapshot(path: Path | None = None) -> None:
    with suppress(OSError):
        (path or snapshot_path()).unlink()


def growth_version() -> str:
    from core import growth

    return growth.slot_version()


def growth_slot() -> str:
    from core import growth

    return growth.load_slot()


def rollback_floor() -> float:
    """Follow-up fitness floor (EXAM_ROLLBACK_FLOOR); default is the documented retrieval fitness floor.

    docs/required-features.md's config record lists retrieval_fitness_floor = 0.5; a promoted slot must
    keep the live slot's held-out fitness at or above it, or the promotion is rolled back.
    """
    try:
        return float(os.environ.get("EXAM_ROLLBACK_FLOOR", str(CONFIG["retrieval_fitness_floor"])))
    except ValueError:
        return float(CONFIG["retrieval_fitness_floor"])


async def followup_fitness(runner=None) -> dict:
    """Score the CURRENT slot on the held-out packs with the same scorer machinery improve() uses (47)."""
    from core import growth

    packs = growth._load_json("heldout_packs.json") or []
    if not packs:
        return {"mean": None, "scores": [], "n_packs": 0, "floor": rollback_floor()}
    terms = growth._load_json("terms.json") or {}
    rnr = runner or _runner_mod().make_runner()
    res = await rnr.run(growth.load_slot(), packs, "followup")
    by_q = {a["question_id"]: a.get("result") for a in res.get("answers", [])}
    scores = [growth.score_answer(by_q.get(p["question_id"]), terms.get(p["question_id"], []),
                                  p.get("n_docs", 0)) for p in packs]
    mean = sum(scores) / len(scores)
    return {"mean": mean, "scores": scores, "n_packs": len(packs), "floor": rollback_floor()}


def _promoted_candidate(out: dict) -> str | None:
    """Name the burst's promoted candidate: improve() promotes the best-delta gate-PASS row (growth.improve)."""
    scored = [r for r in out.get("candidates", []) if r.get("verdict") == "PASS"]
    if not scored:
        return None
    return str(max(scored, key=lambda r: r.get("delta") or 0.0).get("name"))


async def check_rollback(runner=None, candidate: str | None = None, snapshot: Path | None = None) -> dict:
    """Auto-rollback (feature 47): undo the last promotion when follow-up fitness drops below the floor.

    A pre-burst snapshot records the slot the burst started from; if the current slot version differs
    (growth.improve promoted) and the promoted slot's held-out fitness is below rollback_floor(), the
    snapshot's slot text and version are restored via growth.promote and one `revert` ledger event
    records the shortfall (delta = mean - floor, <= 0). Fitness at or above the floor closes the
    rollback window (snapshot cleared) — the promotion stands.
    """
    from core import growth, learn

    p = snapshot or snapshot_path()
    snap = read_snapshot(p)
    if snap is None:
        return {"reverted": False, "reason": "no pre-burst snapshot"}
    current = growth.slot_version()
    if snap["version"] == current:
        return {"reverted": False, "reason": f"slot still at snapshot version {current}"}
    fit = await followup_fitness(runner)
    result = {"followup_mean": fit["mean"], "floor": fit["floor"], "scores": fit["scores"],
              "n_packs": fit["n_packs"]}
    if fit["mean"] is None:
        return {"reverted": False, "reason": "no held-out packs to score follow-up fitness", **result}
    if fit["mean"] >= fit["floor"]:
        clear_snapshot(p)
        return {"reverted": False, "reason": "follow-up fitness at or above floor", **result}
    growth.promote(snap["slot"], snap["version"])
    floor = fit["floor"]
    await learn.write_ledger(LedgerRow(
        target=f"candidate:{candidate or 'slot'}", target_kind="candidate", type="revert",
        delta=fit["mean"] - floor, before=fit["mean"], after=floor,
        cause=f"auto-rollback: follow-up fitness {fit['mean']:.3f} below floor {floor:.3f}; "
              f"slot reverted to v{snap['version']}"))
    clear_snapshot(p)
    return {"reverted": True, "version": snap["version"], **result}


# ---------------------------------------------------------------------------------------------------- 49: share page
SHARE_ROW_FIELDS = ("name", "axis", "status", "veto_reason", "verdict", "mean", "spread", "delta",
                    "ratio", "runner", "ts")
SHARE_LIMIT_MAX = 100


async def share_payload(limit: int = 25) -> dict:
    """Read-only snapshot for the public share page (feature 49).

    Only SELECTs run here — a viewer of the link can never mutate the ledger. Any DB failure is
    reported as an `error` field and the page renders degraded, never a 500 (demo-day resilience).
    """
    limit = max(1, min(int(limit), SHARE_LIMIT_MAX))
    try:
        rows = await db.q(f"SELECT * FROM candidate ORDER BY ts DESC LIMIT {limit}")
        poisons = await standing_poison_report()
        error = None
    except Exception as e:  # noqa: BLE001 — degraded page beats a 500 on a public link
        log.warning("share payload degraded: %s", type(e).__name__)
        rows, poisons, error = [], None, f"{type(e).__name__}"
    return {
        "generated_at": db.now(),
        "slot_version": growth_version(),
        "candidates": [{k: row.get(k) for k in SHARE_ROW_FIELDS if k in row} for row in (rows or [])],
        "poisons": poisons,
        "error": error,
    }


def render_share_page(payload: dict) -> str:
    """Pure HTML for the share link (feature 49): the last burst's candidates and the poison report.

    Static markup only — no scripts, no forms, no POST targets — and every dynamic value is
    escaped, so candidate names or causes from the pool cannot inject markup.
    """
    import html as _html

    def esc(v) -> str:
        return _html.escape(str(v if v is not None else "—"))

    def fmt(v) -> str:
        return "—" if v is None else (f"{float(v):.3f}" if isinstance(v, (int, float)) else esc(v))

    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Mycelium — exam results</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background: #0d1117; color: #e6edf3; font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; padding: 0 1rem; }}
  h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
  .muted {{ color: #8b949e; }}
  .error, .empty {{ border: 1px solid #30363d; border-radius: 8px; padding: 0.75rem 1rem; margin: 1rem 0; }}
  .error {{ border-color: #b62324; color: #ffa198; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #30363d; padding: 0.4rem 0.6rem; text-align: left; font-variant-numeric: tabular-nums; }}
  th {{ color: #8b949e; font-weight: 500; }}
  .pass {{ color: #3fb950; }} .veto {{ color: #f85149; }}
</style></head><body>
<h1>Mycelium exam results</h1>
<p class="muted">Slot v{esc(payload.get("slot_version"))} · generated {esc(payload.get("generated_at"))} · read-only</p>"""

    parts = [head]
    if payload.get("error"):
        parts.append(f'<p class="error">Exam data unavailable: {esc(payload["error"])}</p>')
    cands = payload.get("candidates") or []
    if not cands and not payload.get("error"):
        parts.append('<p class="empty">No exam results yet — run a burst at /exam/burst.</p>')
    if cands:
        rows = "".join(
            "<tr>"
            f"<td>{esc(c.get('name'))}</td><td>{esc(c.get('axis'))}</td>"
            f"<td>{esc(c.get('status'))}</td>"
            f"<td class=\"{'pass' if c.get('verdict') == 'PASS' else 'veto' if c.get('verdict') else ''}\">"
            f"{esc(c.get('verdict'))}</td>"
            f"<td>{fmt(c.get('mean'))}</td><td>{fmt(c.get('delta'))}</td><td>{fmt(c.get('spread'))}</td>"
            f"<td>{esc(c.get('veto_reason'))}</td><td>{esc(c.get('runner'))}</td>"
            "</tr>"
            for c in cands)
        parts.append(
            "<h2>Latest burst</h2><table><thead><tr><th>candidate</th><th>axis</th><th>status</th>"
            "<th>verdict</th><th>mean</th><th>delta</th><th>spread</th><th>veto reason</th><th>runner</th>"
            "</tr></thead><tbody>" + rows + "</tbody></table>")
    poisons = payload.get("poisons")
    if isinstance(poisons, dict) and poisons.get("entries"):
        rows = "".join(
            f"<tr><td>{esc(e.get('name'))}</td><td>{esc(e.get('failure_mode'))}</td>"
            f"<td>{esc(e.get('status'))}</td><td>{fmt(e.get('mean'))}</td></tr>"
            for e in poisons["entries"])
        parts.append(
            f"<h2>Poison report</h2><p class=\"muted\">threshold {fmt(poisons.get('threshold'))} · "
            f"{esc(poisons.get('escaped_count'))} escaped</p>"
            "<table><thead><tr><th>poison</th><th>failure mode</th><th>status</th><th>mean</th>"
            "</tr></thead><tbody>" + rows + "</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts)
