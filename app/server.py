"""FastAPI composition root: the static console plus one router per segment.

Core modules are imported lazily per request (app/routers/common.py) so a
missing one only 503s its own route.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import metrics
from app.middleware import BearerAuthMiddleware, RequestContextMiddleware
from app.routers import api, exam, learning, llm, ops, retrieval, search, vitals, voice
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


app = FastAPI(
    title="Mycelium",
    version="0.1.0",
    description=(
        "A context layer over a company's knowledge that learns and proves it. "
        "Answers questions with citations, explains its interpretation, learns "
        "from corrections, and evaluates its own instruction changes."
    ),
    lifespan=lifespan,
)
# Feature 8: bearer auth is innermost so 401s still carry request IDs and feed
# metrics; CORS is outermost so preflights authenticate at the browser, not the API.
app.add_middleware(BearerAuthMiddleware)
app.add_middleware(RequestContextMiddleware)
# Feature 64: one latency/error sample per request feeds the vitals panel.
app.add_middleware(metrics.MetricsMiddleware)
_cors_origins = [o.strip() for o in os.environ.get("MYCELIUM_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")

# Feature 10: tags are attached here, at include time, so each segment's router
# file stays untouched and the OpenAPI grouping lives in one A-owned place.
app.include_router(api.router, tags=["console"])
app.include_router(retrieval.router, tags=["retrieval"])
app.include_router(search.router, tags=["retrieval"])
app.include_router(llm.router, tags=["ask"])
app.include_router(learning.router, tags=["learning"])
app.include_router(exam.router, tags=["exam"])
app.include_router(voice.router, tags=["voice"])
app.include_router(vitals.router, tags=["vitals"])
app.include_router(ops.router, tags=["ops"])

_ASK_EXAMPLE = {"text": "who owns billing?", "session_id": "sess-123"}


def _openapi_with_examples() -> dict:
    """Default schema plus request examples; never raises on a missing key."""
    schema = _base_openapi()
    try:
        request_body = schema["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]
        request_body["example"] = _ASK_EXAMPLE
        request_body["examples"] = {"typical": {"summary": "A question with a session", "value": _ASK_EXAMPLE}}
    except (KeyError, TypeError):  # route moved or renamed: /docs must still render
        pass
    return schema


_base_openapi = app.openapi
app.openapi = _openapi_with_examples
