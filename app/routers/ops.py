"""ops router: liveness and readiness probes.

/healthz is a pure process check and answers 200 unconditionally.
/readyz answers 200 only when SurrealDB answers a bounded probe.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["ops"])

READY_TIMEOUT_S = 2.0


@router.get("/healthz")
async def healthz():
    """Liveness: the process is up. Never touches external services."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz():
    """Readiness: SurrealDB reachable. 503 on error or timeout so the
    orchestrator (and demo scripts) can distinguish 'up' from 'usable'."""
    try:
        from core import db

        await asyncio.wait_for(db.q("RETURN 1"), timeout=READY_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — any DB failure (down, auth, timeout) means not ready
        raise HTTPException(status_code=503, detail=f"not ready: {type(e).__name__}")
    return {"status": "ready"}
