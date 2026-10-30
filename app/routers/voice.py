"""voice router: speech-to-text and text-to-speech."""
from __future__ import annotations

import asyncio
import importlib
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.routers.common import _mod

log = logging.getLogger("mycelium.server")

router = APIRouter()


@router.post("/stt")
async def stt(audio: UploadFile = File(...)):
    voice = _mod("voice")
    data = await audio.read()
    # Feature 53: refuse bad uploads (413/415) before any transcription spend.
    try:
        mime = voice.validate_stt_upload(data, audio.content_type, audio.filename)
    except voice.SttUploadError as e:
        log.warning("stt upload rejected (HTTP %s): %s", e.status_code, e)
        raise HTTPException(status_code=e.status_code, detail=str(e))
    try:
        text = await asyncio.to_thread(voice.stt, data, mime)
    except Exception as e:
        log.warning("stt failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="speech-to-text unavailable")
    return {"text": text}


@router.get("/tts")
async def tts(text: str, quality: str = "flash"):
    try:
        voice = importlib.import_module("core.voice")
        audio = await asyncio.to_thread(voice.tts, text, quality)
    except Exception as e:
        log.warning("tts failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="text-to-speech unavailable")
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/tts/stream")
async def tts_stream(text: str, quality: str = "flash"):
    """Feature 51: sentence-by-sentence synthesis. The first chunk — one mp3 —
    is flushed as soon as sentence one is synthesized, so the client can start
    playback before the full answer is spoken. `quality` selects the
    ElevenLabs model (feature 55): flash (default) or v3."""
    voice = _mod("voice")

    def chunks():
        try:
            yield from voice.tts_stream(text, quality=quality)
        except Exception as e:
            log.warning("tts stream failed: %s", type(e).__name__)

    return StreamingResponse(chunks(), media_type="audio/mpeg")


class PrefsIn(BaseModel):
    user: str
    volume: float | None = None
    speed: float | None = None


@router.get("/voice/prefs")
async def get_voice_prefs(user: str = "default"):
    """Feature 57: per-user volume/speed stored in SurrealDB. Documented
    defaults when the user has never saved a preference."""
    prefs = _mod("prefs")
    try:
        return await prefs.get_prefs(user)
    except prefs.PrefsError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # db down: prefs degrade, the rest of the API lives on
        raise HTTPException(status_code=503, detail=f"db unavailable: {type(e).__name__}") from e


@router.put("/voice/prefs")
async def put_voice_prefs(body: PrefsIn):
    """Feature 57: persist volume/speed. A field left out keeps its stored value."""
    prefs = _mod("prefs")
    try:
        return await prefs.set_prefs(body.user, body.volume, body.speed)
    except prefs.PrefsError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {type(e).__name__}") from e
