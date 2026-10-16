"""ElevenLabs adapters: tts(text)->mp3 bytes, stt(audio, mime)->text. Optional feature; callers fall back on failure."""
from __future__ import annotations

import os

import httpx

BASE = "https://api.elevenlabs.io"
TTS_MODEL = "eleven_flash_v2_5"
STT_MODEL = "scribe_v1"
TIMEOUT = 30.0

_voice_id: str | None = None


class VoiceError(RuntimeError):
    """Raised on any voice failure. Never carries the API key."""


def _headers() -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY not set")
    return {"xi-api-key": key}


def _voice(client: httpx.Client) -> str:
    global _voice_id
    if _voice_id:
        return _voice_id
    override = os.environ.get("ELEVENLABS_VOICE_ID")
    if override:
        _voice_id = override
        return _voice_id
    r = client.get(f"{BASE}/v1/voices", headers=_headers())
    if r.status_code != 200:
        raise VoiceError(f"voices lookup failed: HTTP {r.status_code}")
    voices = r.json().get("voices") or []
    if not voices:
        raise VoiceError("no voices available")
    _voice_id = voices[0]["voice_id"]
    return _voice_id


def tts(text: str) -> bytes:
    """Text to mp3 bytes."""
    if not text or not text.strip():
        raise VoiceError("empty text")
    with httpx.Client(timeout=TIMEOUT) as client:
        vid = _voice(client)
        r = client.post(
            f"{BASE}/v1/text-to-speech/{vid}",
            params={"output_format": "mp3_44100_128"},
            headers=_headers(),
            json={"text": text, "model_id": TTS_MODEL},
        )
    if r.status_code != 200:
        raise VoiceError(f"tts failed: HTTP {r.status_code}")
    return r.content


def stt(audio: bytes, mime: str) -> str:
    """Audio bytes to transcript text."""
    if not audio:
        raise VoiceError("empty audio")
    ext = (mime or "audio/webm").split("/")[-1].split(";")[0] or "webm"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(
            f"{BASE}/v1/speech-to-text",
            headers=_headers(),
            data={"model_id": STT_MODEL},
            files={"file": (f"audio.{ext}", audio, mime or "audio/webm")},
        )
    if r.status_code != 200:
        raise VoiceError(f"stt failed: HTTP {r.status_code}")
    return str(r.json().get("text", "")).strip()
