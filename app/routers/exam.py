"""exam router: reflection and self-improvement triggers."""
from __future__ import annotations

from fastapi import APIRouter

from app.routers.common import _audio_url, _run

router = APIRouter()


@router.post("/reflect")
async def reflect():
    out = await _run("reflect", "reflect")
    if isinstance(out, dict):
        spoken = out.get("spoken")
        if not spoken:
            spoken = " ".join(s.get("text", "") for s in out.get("sentences") or [] if isinstance(s, dict)).strip()
        out["audio_url"] = _audio_url(spoken)
    return out


@router.post("/improve")
async def improve():
    return await _run("growth", "improve")


@router.get("/exam/history")
async def exam_history(limit: int = 100):
    """Persisted exam record (feature 41): candidates with fitness deltas and decisions, oldest first."""
    return await _run("exam", "history", limit)
