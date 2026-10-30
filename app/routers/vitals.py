"""vitals router: system health snapshot for the console panel."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os

from fastapi import APIRouter

from app import metrics
from app.routers.common import ROOT

router = APIRouter()

BASELINE_PATH = ROOT / "data" / "baseline_runs.json"
SLOT_PATH = ROOT / "prompts" / "slot.md"


def _slot_info() -> tuple[str | None, str | None]:
    version = shash = None
    try:
        growth = importlib.import_module("core.growth")
        try:
            version = growth.slot_version()
        except Exception:
            pass
        try:
            shash = growth.slot_hash(growth.load_slot())
        except Exception:
            pass
    except Exception:
        pass
    if shash is None:
        try:
            shash = hashlib.sha256(SLOT_PATH.read_bytes()).hexdigest()[:12]
        except Exception:
            pass
    return version, shash


async def _count(table: str):
    try:
        from core import db

        rows = await asyncio.wait_for(db.q(f"SELECT count() AS n FROM {table} GROUP ALL"), timeout=3)
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return None


def _retrieval_latency() -> dict:
    """Retrieval latency histogram (feature 18): p50/p95 over the bounded recent window."""
    try:
        from core.retrieve import latency

        return latency.summary()
    except Exception:  # noqa: BLE001  vitals must render even if retrieval is broken
        return {"count": 0, "p50_ms": None, "p95_ms": None}


@router.get("/vitals")
async def vitals():
    baseline = None
    try:
        if BASELINE_PATH.is_file():
            baseline = json.loads(BASELINE_PATH.read_text())
    except Exception:
        baseline = None
    tables = ("chunk", "question", "lesson", "candidate")
    counts = dict(zip(tables, await asyncio.gather(*(_count(t) for t in tables))))
    version, shash = _slot_info()
    return {
        "slot_version": version,
        "slot_hash": shash,
        "baseline": baseline,
        "counts": counts,
        "retrieval": _retrieval_latency(),
        "runner": "daytona" if os.environ.get("DAYTONA_API_KEY") else "local",
        "metrics": metrics.snapshot(),
    }
