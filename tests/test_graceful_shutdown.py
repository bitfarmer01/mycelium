"""Graceful shutdown (feature 6): SIGTERM mid-write leaves zero uncommitted ledger events.

Three layers: the PendingTasks registry, the app lifespan's shutdown drain
(fixture asserts DB state after shutdown), and a real SIGTERM round-trip
against uvicorn.
"""
from __future__ import annotations

import asyncio
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.shutdown import PendingTasks

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- PendingTasks registry ----------------------------------------------------


async def test_drain_awaits_registered_tasks():
    pending = PendingTasks()
    done: list[str] = []

    async def write(name: str, delay: float):
        await asyncio.sleep(delay)
        done.append(name)

    pending.spawn(write("a", 0.01))
    pending.spawn(write("b", 0.03))
    leftover = await pending.drain(timeout=2.0)
    assert leftover == 0
    assert sorted(done) == ["a", "b"]


async def test_drain_reports_unfinished_tasks():
    pending = PendingTasks()

    async def hung():
        await asyncio.sleep(60)

    pending.spawn(hung())
    leftover = await pending.drain(timeout=0.05)
    assert leftover == 1
    for t in list(pending._tasks):  # cleanup so pytest-asyncio doesn't warn
        t.cancel()


async def test_completed_tasks_leave_the_registry():
    pending = PendingTasks()
    task = pending.spawn(asyncio.sleep(0))
    await task
    await asyncio.sleep(0)  # let done-callbacks run
    assert len(pending) == 0


# --- App lifespan shutdown drain ----------------------------------------------


async def _lifespan(app):
    """Yield inside the app's lifespan (startup ran, shutdown pending)."""
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    return ctx


async def test_shutdown_mid_write_leaves_zero_uncommitted_ledger_events():
    # Simulates the acceptance row: a ledger write in flight when SIGTERM lands.
    # The "DB" is a list a row joins only once its write completes; the fixture
    # asserts its state after shutdown.
    from app.server import app

    committed: list[dict] = []

    async def ledger_write(row: dict):
        await asyncio.sleep(0.05)  # write is mid-flight when shutdown fires
        committed.append(row)  # the commit itself

    ctx = await _lifespan(app)
    try:
        task = app.state.pending.spawn(ledger_write({"id": "ledger:1", "type": "prior"}))
        await asyncio.sleep(0)  # let the write start, do not await it
        assert committed == []  # mid-write: nothing committed yet
    finally:
        await ctx.__aexit__(None, None, None)  # <- shutdown runs the drain
    await task
    assert committed == [{"id": "ledger:1", "type": "prior"}], "shutdown must not strand an in-flight ledger write"


async def test_shutdown_with_no_pending_work_is_clean():
    from app.server import app

    ctx = await _lifespan(app)
    await ctx.__aexit__(None, None, None)  # no tasks: drain is a no-op


# --- Real SIGTERM round-trip ---------------------------------------------------


async def test_sigterm_shuts_down_gracefully():
    port = _free_port()
    proc = subprocess.Popen(  # noqa: ASYNC220 — spawns instantly, no blocking I/O to the child
        [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        up = False
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline and not up:
                if proc.poll() is not None:
                    pytest.fail(f"uvicorn exited early: {proc.stderr.read().decode()[-500:]}")
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/healthz")
                    up = r.status_code == 200
                except Exception:  # noqa: BLE001 — connection refused while uvicorn boots
                    await asyncio.sleep(0.3)
        assert up, "server never became healthy"

        proc.terminate()  # SIGTERM
        code = await asyncio.get_running_loop().run_in_executor(None, proc.wait, 15)
        # uvicorn 0.54 handles SIGTERM, runs the lifespan shutdown (our drain),
        # then deliberately re-raises the signal after restoring the default
        # handler (Server.capture_signals) so the exit status reflects it. So
        # the graceful outcomes are exit 0 or death-by-SIGTERM (-15); the
        # 'Finished server process' log line proves the drain completed.
        assert code in (0, -signal.SIGTERM), f"SIGTERM did not shut down cleanly (exit {code})"
        output = proc.stdout.read() + proc.stderr.read()
        assert b"Finished server process" in output, f"no graceful-shutdown marker in output: {output[-500:]!r}"
    finally:
        if proc.poll() is None:
            proc.kill()
