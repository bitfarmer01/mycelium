"""FastAPI composition root: the static console plus one router per segment.

Core modules are imported lazily per request (app/routers/common.py) so a
missing one only 503s its own route.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.middleware import RequestContextMiddleware
from app.routers import api, exam, learning, llm, ops, retrieval, vitals, voice
from app.routers.common import STATIC_DIR
from app.shutdown import DEFAULT_DRAIN_TIMEOUT_S, PendingTasks

log = logging.getLogger(__name__)

load_dotenv()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Feature 6: shutdown drains in-flight requests and background writes so
    # SIGTERM mid-write leaves zero uncommitted ledger events.
    app.state.pending = PendingTasks()
    scheduler_task = None
    try:
        from core.exam import ExamScheduler, scheduler_loop

        scheduler = ExamScheduler()  # interval from EXAM_INTERVAL_MINUTES; 0 = off
        if scheduler.interval > 0:
            scheduler_task = app.state.pending.spawn(scheduler_loop(scheduler))
            log.info("scheduled exam bursts every %s min", scheduler.interval)
    except Exception as e:  # noqa: BLE001  the API must boot even if the exam module is broken
        log.warning("exam scheduler unavailable: %s", type(e).__name__)
    try:
        yield
    finally:
        # Cancel the scheduler first: it is an endless loop, not a write to drain.
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        leftover = await app.state.pending.drain(timeout=DEFAULT_DRAIN_TIMEOUT_S)
        if leftover == 0:
            log.info("shutdown drain complete: all in-flight work committed")


app = FastAPI(title="Mycelium", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.include_router(api.router)
app.include_router(retrieval.router)
app.include_router(llm.router)
app.include_router(learning.router)
app.include_router(exam.router)
app.include_router(voice.router)
app.include_router(vitals.router)
app.include_router(ops.router)
