"""llm router: the question-answering endpoint (readings + synthesis)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.routers.common import _audio_url, _run

router = APIRouter()


class AskIn(BaseModel):
    text: str
    session_id: str | None = None


@router.post("/ask")
async def ask(body: AskIn):
    sid = body.session_id or str(uuid.uuid4())
    out = await _run("reflex", "ask", body.text, sid)
    if isinstance(out, dict):
        out.setdefault("session_id", sid)
        out["audio_url"] = _audio_url(out.get("spoken"))
    return out
