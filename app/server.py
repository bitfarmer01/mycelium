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

from app.routers import api, exam, learning, llm, retrieval, vitals, voice
from app.routers.common import STATIC_DIR

load_dotenv()

log = logging.getLogger("mycelium.server")


@contextlib.asynccontextmanager
async def lifespan(_app):
    task = None
    try:
        from core.exam import ExamScheduler, scheduler_loop

        scheduler = ExamScheduler()  # interval from EXAM_INTERVAL_MINUTES; 0 = off
        if scheduler.interval > 0:
            task = asyncio.create_task(scheduler_loop(scheduler))
            log.info("scheduled exam bursts every %s min", scheduler.interval)
    except Exception as e:  # noqa: BLE001  the API must boot even if the exam module is broken
        log.warning("exam scheduler unavailable: %s", type(e).__name__)
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Mycelium", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.include_router(api.router)
app.include_router(retrieval.router)
app.include_router(llm.router)
app.include_router(learning.router)
app.include_router(exam.router)
app.include_router(voice.router)
app.include_router(vitals.router)
