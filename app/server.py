"""FastAPI surface. Core modules are imported lazily per request so a missing one only 503s its own route."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import uuid
from pathlib import Path

from app.routers import api
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

UPLOAD_DIR = ROOT / "data" / "docs" / "uploads"
# Hard limits for judge uploads: a laptop embeds roughly 20-60 chunks/s, so 800 chunks (~280k words) stays near a minute.
UPLOAD_MAX_FILES = 10
UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # per file
UPLOAD_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # per request
UPLOAD_MAX_CHUNKS = 800  # per request, checked before embedding
UPLOAD_DIR_MAX_BYTES = 500 * 1024 * 1024  # everything ever uploaded
_upload_lock = asyncio.Lock()  # one ingest at a time; a second upload gets 429 instead of queueing behind the embedder


def _ingest():
    try:
        return importlib.import_module("scripts.ingest")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ingest unavailable: {type(e).__name__}")


def _safe_name(name: str) -> str:
    base = Path(name or "upload").name
    stem, ext = os.path.splitext(base)
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem).strip("._") or "upload"
    return stem[:80] + ext.lower()


@app.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Judges drop their own documents here: saved under data/docs/uploads/, chunked, embedded and searchable at once."""
    ing = _ingest()
    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"at most {UPLOAD_MAX_FILES} files per upload")
    if _upload_lock.locked():
        raise HTTPException(status_code=429, detail="another upload is being indexed; try again in a minute")
    async with _upload_lock:
        return await _ingest_uploads(ing, files)


async def _read_capped(f: UploadFile, cap: int) -> bytes | None:
    """Read at most cap bytes; None if the file is larger (never buffers more than cap + 1 MB)."""
    buf = bytearray()
    while chunk := await f.read(1024 * 1024):
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


async def _ingest_uploads(ing, files: list[UploadFile]) -> dict:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in UPLOAD_DIR.rglob("*") if p.is_file())
    saved, rejected, total = [], [], 0
    for f in files:
        name = _safe_name(f.filename)
        if Path(name).suffix not in ing.EXTS:
            rejected.append({"name": f.filename, "reason": "unsupported type (md, txt, html, pdf, csv, tsv, xlsx)"})
            continue
        data = await _read_capped(f, UPLOAD_MAX_BYTES)
        if not data:
            rejected.append({"name": f.filename, "reason": "empty or larger than 20 MB"})
            continue
        if total + len(data) > UPLOAD_MAX_TOTAL_BYTES or used + total + len(data) > UPLOAD_DIR_MAX_BYTES:
            rejected.append({"name": f.filename, "reason": "upload size limit reached (50 MB per upload, 500 MB overall)"})
            continue
        total += len(data)
        dest = UPLOAD_DIR / name
        dest.write_bytes(data)
        saved.append(dest)
    t0 = asyncio.get_running_loop().time()
    try:
        chunks, sources = await asyncio.to_thread(ing.build_chunks, saved, {}, UPLOAD_MAX_CHUNKS) if saved else ([], [])
    except ing.TooManyChunks as e:
        for p in saved:
            p.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=f"{e}; upload fewer or smaller files")
    try:
        await ing.store(chunks, sources, replace_paths=[str(p.resolve()) for p in saved])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"database unavailable: {type(e).__name__}")
    per_file: dict[str, dict] = {}
    for c in chunks:
        name = Path(c["metadata"]["path"]).name
        row = per_file.setdefault(name, {"name": name, "kind": c["metadata"].get("kind"), "chunks": 0, "title": c["title"]})
        row["chunks"] += 1
    for p in saved:
        if p.name not in per_file:
            rejected.append({"name": p.name, "reason": "no extractable text (scanned PDF or empty sheet?)"})
    return {"files": list(per_file.values()), "rejected": rejected, "chunks": len(chunks),
            "ms": int((asyncio.get_running_loop().time() - t0) * 1000)}


class AskIn(BaseModel):
    text: str
    session_id: str | None = None


class CorrectIn(BaseModel):
    question_id: str
    to_role: str


class ConfirmIn(BaseModel):
    question_id: str


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
