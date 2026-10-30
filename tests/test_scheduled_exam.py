"""Feature 43: config-driven interval triggers a burst on a test clock; manual /improve still works."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest


class TestClock:
    """Advancing fake clock for scheduler tests."""

    __test__ = False  # not a test class; pytest otherwise warns on collection

    def __init__(self, start: dt.datetime | None = None):
        self.now = start or dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += dt.timedelta(minutes=minutes)


@pytest.fixture
def state_path(tmp_path) -> Path:
    return tmp_path / "exam_last_burst.txt"


@pytest.fixture
def scheduler(state_path):
    """Factory binding a fresh ExamScheduler to one shared advancing clock."""
    from core.exam import ExamScheduler

    clock = TestClock()
    bursts: list[dt.datetime] = []

    async def improve_fn():
        bursts.append(clock())
        return {"candidates": [], "slot_version": "1.0"}

    def make(interval=60, with_improve_fn=True):
        return ExamScheduler(interval_minutes=interval, clock=clock,
                             improve_fn=improve_fn if with_improve_fn else None, state_path=state_path)

    make.clock = clock
    make.bursts = bursts
    return make


async def test_interval_zero_means_scheduled_bursts_off(scheduler):
    s = scheduler(interval=0)
    assert s.due() is False
    assert await s.tick() is None
    assert scheduler.bursts == []


async def test_never_run_counts_as_due_and_records_the_burst(scheduler, state_path):
    s = scheduler(interval=60)
    assert s.due() is True
    out = await s.tick()
    assert out == {"candidates": [], "slot_version": "1.0"}
    assert len(scheduler.bursts) == 1
    assert state_path.exists(), "tick must persist the run so the interval starts counting"


async def test_interval_elapsed_triggers_next_burst(scheduler):
    s = scheduler(interval=60)
    await s.tick()
    s.clock.advance(30)
    assert await s.tick() is None, "half the interval has not elapsed"
    s.clock.advance(31)  # now 61 min since the recorded run
    out = await s.tick()
    assert out is not None and len(scheduler.bursts) == 2


async def test_interval_is_config_driven(monkeypatch, state_path):
    from core.exam import ExamScheduler

    async def improve_fn():
        return {}

    monkeypatch.setenv("EXAM_INTERVAL_MINUTES", "45")
    s = ExamScheduler(clock=TestClock(), improve_fn=improve_fn, state_path=state_path)
    assert s.interval == 45 and s.due() is True

    monkeypatch.setenv("EXAM_INTERVAL_MINUTES", "garbage")
    assert ExamScheduler(clock=TestClock(), improve_fn=improve_fn, state_path=state_path).interval == 0


def test_scheduled_burst_defaults_to_the_ceiling_path(scheduler):
    """The scheduler's burst runs improve_burst (budget-aware, feature 45), not raw growth.improve."""
    from core import exam

    assert scheduler(with_improve_fn=False).improve_fn is exam.improve_burst


async def test_manual_improve_route_still_works(monkeypatch):
    """Manual promotion burst is untouched: /improve still binds growth.improve, not the budgeted path."""
    calls = []

    async def improve():
        calls.append("improve")
        return {"candidates": ["c1"]}

    monkeypatch.setattr("core.growth.improve", improve)

    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/improve")
    assert r.status_code == 200 and r.json() == {"candidates": ["c1"]}
    assert calls == ["improve"]
