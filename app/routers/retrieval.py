"""retrieval router: judge document upload and ingest.

Feature 7: /upload returns 202 immediately once files are saved; the slow part
(chunking, embedding, storing) runs as a background task tracked by a job id,
and GET /upload/status/{job_id} reports pending/running/done/failed plus the
full ingest result once finished.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.routers.common import ROOT

router = APIRouter()

UPLOAD_DIR = ROOT / "data" / "docs" / "uploads"
# Hard limits for judge uploads: a laptop embeds roughly 20-60 chunks/s, so 800 chunks (~280k words) stays near a minute.
UPLOAD_MAX_FILES = 10
UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # per file
UPLOAD_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # per request
UPLOAD_MAX_CHUNKS = 800  # per request, checked before embedding
UPLOAD_DIR_MAX_BYTES = 500 * 1024 * 1024  # everything ever uploaded
_upload_lock = asyncio.Lock()  # one ingest at a time; a second upload gets 429 instead of queueing behind the embedder

# Feature 7: bounded job table — old finished jobs fall off so memory stays flat.
_JOBS_MAX = 100
_jobs: dict[str, dict] = {}


def evict_jobs(jobs: dict[str, dict], max_jobs: int) -> None:
    """Keep the table bounded: drop oldest ids (lexicographic on the hex id) beyond the cap."""
    while len(jobs) > max_jobs:
        del jobs[min(jobs, key=lambda j: j)]


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


def _spawn(request: Request, coro) -> asyncio.Task:
    """Background the ingest, registered for the shutdown drain when the app lifespan is live."""
    pending = getattr(request.app.state, "pending", None)
    if pending is not None:
        return pending.spawn(coro)
    return asyncio.create_task(coro)


@router.post("/upload", status_code=202)
async def upload(request: Request, files: list[UploadFile] = File(...)):
    """Judges drop their own documents here: saved under data/docs/uploads/ at once (202),
    chunked/embedded/stored in the background; GET /upload/status/{job_id} flips to done."""
    ing = _ingest()
    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"at most {UPLOAD_MAX_FILES} files per upload")
    if _upload_lock.locked():
        raise HTTPException(status_code=429, detail="another upload is being indexed; try again in a minute")
    saved, rejected = await _save_uploads(ing, files)
    job_id = os.urandom(6).hex()
    job: dict = {"job_id": job_id, "status": "pending", "files": [{"name": p.name} for p in saved],
                 "rejected": rejected, "result": None, "error": None}
    _jobs[job_id] = job
    evict_jobs(_jobs, _JOBS_MAX)
    _spawn(request, _run_ingest(ing, job, saved))
    return JSONResponse(status_code=202, content={k: job[k] for k in ("job_id", "status", "files", "rejected")})


@router.get("/upload/status/{job_id}")
async def upload_status(job_id: str):
    """Follow-up status: `status` flips to done (with the full ingest result) or failed (with a reason)."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown upload job (expired or never existed)")
    return job


async def _run_ingest(ing, job: dict, saved: list[Path]) -> None:
    async with _upload_lock:
        job["status"] = "running"
        try:
            job["result"] = await _ingest_saved(ing, saved)
            job["status"] = "done"
        except HTTPException as e:
            job["status"], job["error"] = "failed", e.detail
        except Exception as e:  # noqa: BLE001 — a failed job must say why, never crash the app
            job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"


async def _read_capped(f: UploadFile, cap: int) -> bytes | None:
    """Read at most cap bytes; None if the file is larger (never buffers more than cap + 1 MB)."""
    buf = bytearray()
    while chunk := await f.read(1024 * 1024):
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


async def _save_uploads(ing, files: list[UploadFile]) -> tuple[list[Path], list[dict]]:
    """Fast half of the upload: type/size checks and writes to disk. Returns (saved paths, rejections)."""
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
    return saved, rejected


async def _ingest_saved(ing, saved: list[Path]) -> dict:
    """Slow half of the upload: chunk, embed, store. Runs in the background (feature 7)."""
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
    rejected: list[dict] = []
    for p in saved:
        if p.name not in per_file:
            rejected.append({"name": p.name, "reason": "no extractable text (scanned PDF or empty sheet?)"})
    return {"files": list(per_file.values()), "rejected": rejected, "chunks": len(chunks),
            "ms": int((asyncio.get_running_loop().time() - t0) * 1000)}
