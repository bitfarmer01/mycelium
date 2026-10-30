"""Graceful-shutdown support (workstream A): track in-flight work and drain it on exit.

Feature 6's guarantee: SIGTERM mid-write leaves zero uncommitted ledger events.
The registry holds every request task (added by RequestContextMiddleware) and any
background task spawned through it; the app's shutdown hook awaits them — bounded
by a timeout so a hung writer delays shutdown but never blocks it forever.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# Upper bound for the shutdown drain. Generous enough for any ledger write;
# small enough that a hung writer cannot wedge process shutdown.
DEFAULT_DRAIN_TIMEOUT_S = 15.0


class PendingTasks:
    """A set of live asyncio tasks, auto-removed as each completes."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def add(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def spawn(self, coro) -> asyncio.Task:
        """Schedule a background task and register it for the shutdown drain."""
        task = asyncio.create_task(coro)
        self.add(task)
        return task

    def __len__(self) -> int:
        return len(self._tasks)

    async def drain(self, timeout: float = DEFAULT_DRAIN_TIMEOUT_S) -> int:
        """Await every registered task, bounded by `timeout`.

        Returns the number of tasks still unfinished when the budget ran out
        (they will be cancelled with the loop) so the caller can log them.
        """
        pending = [t for t in self._tasks if not t.done()]
        if not pending:
            return 0
        _, still_pending = await asyncio.wait(pending, timeout=timeout)
        if still_pending:
            names = [t.get_name() for t in still_pending]
            log.warning("shutdown drain timed out with %d task(s) unfinished: %s", len(names), names)
        return len(still_pending)
