"""FastAPI surface. Core modules are imported lazily per request so a missing one only 503s its own route."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path

from app.routers import api, exam, learning, llm, retrieval, voice
from app.routers.common import STATIC_DIR
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "data" / "baseline_runs.json"
SLOT_PATH = ROOT / "prompts" / "slot.md"

app = FastAPI(title="Mycelium")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.include_router(api.router)
app.include_router(retrieval.router)
app.include_router(llm.router)
app.include_router(learning.router)
app.include_router(exam.router)
app.include_router(voice.router)


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


@app.get("/vitals")
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
        "runner": "daytona" if os.environ.get("DAYTONA_API_KEY") else "local",
    }
