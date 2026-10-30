"""retrieval router: judge document upload and ingest."""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

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


@router.post("/upload")
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
