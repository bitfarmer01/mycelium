"""FastAPI surface. Core modules are imported lazily per request so a missing one only 503s its own route."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path

from app.routers import api, llm, retrieval
from app.routers.common import STATIC_DIR, _audio_url, _mod, _run
from core.contracts import ROLES
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

log = logging.getLogger("mycelium.server")

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "data" / "baseline_runs.json"
SLOT_PATH = ROOT / "prompts" / "slot.md"

app = FastAPI(title="Mycelium")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.include_router(api.router)
app.include_router(retrieval.router)
app.include_router(llm.router)


class CorrectIn(BaseModel):
    question_id: str
    to_role: str


class ConfirmIn(BaseModel):
    question_id: str


@app.post("/correct")
async def correct(body: CorrectIn):
    if body.to_role not in ROLES:
        raise HTTPException(status_code=422, detail=f"to_role must be one of {list(ROLES)}")
    return await _run("learn", "correct", body.question_id, body.to_role)


@app.post("/confirm")
async def confirm(body: ConfirmIn):
    return await _run("learn", "confirm", body.question_id)


@app.post("/tick")
async def tick():
    return {"decayed": await _run("learn", "decay_tick")}


@app.post("/reflect")
async def reflect():
    out = await _run("reflect", "reflect")
    if isinstance(out, dict):
        spoken = out.get("spoken")
        if not spoken:
            spoken = " ".join(s.get("text", "") for s in out.get("sentences") or [] if isinstance(s, dict)).strip()
        out["audio_url"] = _audio_url(spoken)
    return out


@app.post("/improve")
async def improve():
    return await _run("growth", "improve")


@app.post("/stt")
async def stt(audio: UploadFile = File(...)):
    voice = _mod("voice")
    data = await audio.read()
    try:
        text = await asyncio.to_thread(voice.stt, data, audio.content_type or "audio/webm")
    except Exception as e:
        log.warning("stt failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="speech-to-text unavailable")
    return {"text": text}


@app.get("/tts")
async def tts(text: str):
    try:
        voice = importlib.import_module("core.voice")
        audio = await asyncio.to_thread(voice.tts, text)
    except Exception as e:
        log.warning("tts failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="text-to-speech unavailable")
    return Response(content=audio, media_type="audio/mpeg")


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
