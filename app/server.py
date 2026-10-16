"""FastAPI surface. Core modules are imported lazily per request so a missing one only 503s its own route."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import importlib
import json
import logging
import os
import uuid
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.contracts import ROLES

load_dotenv()

log = logging.getLogger("mycelium.server")

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
BASELINE_PATH = ROOT / "data" / "baseline_runs.json"
SLOT_PATH = ROOT / "prompts" / "slot.md"

app = FastAPI(title="Mycelium")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")

PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>Mycelium</title></head>
<body style="font-family:system-ui;padding:2rem"><h1>Mycelium</h1>
<p>The page is not delivered yet. The API is live: try <a href="/vitals">/vitals</a>.</p></body></html>"""


def _mod(name: str):
    """Import core.<name> or raise 503."""
    try:
        return importlib.import_module(f"core.{name}")
    except Exception as e:  # missing or broken sibling module
        raise HTTPException(status_code=503, detail=f"core.{name} unavailable: {type(e).__name__}")


def _jsonable(v):
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return dataclasses.asdict(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        from core.db import plain

        return plain(v)
    except Exception:
        return str(v)


async def _call(fn, *args):
    res = fn(*args)
    if asyncio.iscoroutine(res):
        res = await res
    return res


async def _run(name: str, attr: str, *args):
    mod = _mod(name)
    fn = getattr(mod, attr, None)
    if fn is None:
        raise HTTPException(status_code=503, detail=f"core.{name}.{attr} unavailable")
    return _jsonable(await _call(fn, *args))


def _audio_url(spoken) -> str | None:
    if not spoken or not isinstance(spoken, str):
        return None
    return "/tts?text=" + quote(spoken, safe="")


class AskIn(BaseModel):
    text: str
    session_id: str | None = None


class CorrectIn(BaseModel):
    question_id: str
    to_role: str


class ConfirmIn(BaseModel):
    question_id: str


@app.get("/")
async def index():
    page = STATIC_DIR / "index.html"
    if page.is_file():
        return FileResponse(str(page), media_type="text/html")
    return HTMLResponse(PLACEHOLDER)


@app.post("/ask")
async def ask(body: AskIn):
    sid = body.session_id or str(uuid.uuid4())
    out = await _run("reflex", "ask", body.text, sid)
    if isinstance(out, dict):
        out.setdefault("session_id", sid)
        out["audio_url"] = _audio_url(out.get("spoken"))
    return out


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


@app.get("/config")
async def config():
    try:
        from core import db

        rows = await db.q("SELECT * FROM config:main")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {type(e).__name__}")
    if not rows:
        raise HTTPException(status_code=404, detail="config:main not found")
    return _jsonable(rows[0])


@app.websocket("/events")
async def events(ws: WebSocket):
    await ws.accept()
    send_lock = asyncio.Lock()

    async def send(msg: dict):
        async with send_lock:
            await ws.send_text(json.dumps(_jsonable(msg), default=str))

    async def poll_ledger():
        from core import db

        seen: set[str] = set()
        first = True
        while True:
            try:
                rows = await db.q("SELECT * FROM ledger ORDER BY ts DESC LIMIT 20")
                for row in reversed(rows):
                    key = str(row.get("id")) if isinstance(row, dict) else str(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not first:
                        await send({"table": "ledger", "action": "CREATE", "row": row})
                first = False
            except (WebSocketDisconnect, RuntimeError) as e:
                if isinstance(e, WebSocketDisconnect):
                    raise
                log.warning("ledger poll failed: %s", type(e).__name__)
            except Exception as e:
                log.warning("ledger poll failed: %s", type(e).__name__)
            await asyncio.sleep(2)

    async def relay(table: str):
        try:
            from core import db

            async for msg in db.live(table):
                await send(msg)
        except WebSocketDisconnect:
            raise
        except Exception as e:
            log.warning("live(%s) failed: %s", table, type(e).__name__)
            if table == "ledger":
                await poll_ledger()

    tasks = [asyncio.create_task(relay("ledger")), asyncio.create_task(relay("candidate"))]

    async def watch_disconnect():
        try:
            while True:
                await ws.receive_text()
        except Exception:
            return

    watcher = asyncio.create_task(watch_disconnect())
    try:
        await watcher
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
