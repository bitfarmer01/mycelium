"""voice router: speech-to-text and text-to-speech."""
from __future__ import annotations

import asyncio
import importlib
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.routers.common import _mod

log = logging.getLogger("mycelium.server")

router = APIRouter()


@router.post("/stt")
async def stt(audio: UploadFile = File(...)):
    voice = _mod("voice")
    data = await audio.read()
    try:
        text = await asyncio.to_thread(voice.stt, data, audio.content_type or "audio/webm")
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
