"""Quality selector (feature 55): a named selector switches the ElevenLabs
model param, flash by default.

The mock replaces the ElevenLabs HTTP boundary (`_tts_request`), so every
assertion below sees exactly the model id the selector resolved to.
"""

from __future__ import annotations

import httpx
import pytest

from core import voice

TEST_VOICE = "test-voice-id"


def _capture(monkeypatch, payload: bytes = b"mp3") -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    def fake_request(text: str, vid: str, quality: str) -> bytes:
        calls.append((text, vid, quality))
        return payload

    monkeypatch.setattr(voice, "_tts_request", fake_request)
    monkeypatch.setattr(voice, "_voice_id", TEST_VOICE)  # skips the voices HTTP lookup
    return calls


def test_default_quality_is_flash(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("hello")

    assert calls[0][2] == "eleven_flash_v2_5"


def test_selector_switches_the_model_param(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("hello", quality="flash")
    voice.tts("hello", quality="v3")

    assert {q for _, _, q in calls} == {"eleven_flash_v2_5", "eleven_v3"}


def test_selector_is_case_insensitive_and_whitespace_tolerant(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("hello", quality="V3")
    voice.tts("hello", quality=" v3 ")

    assert len(calls) == 1, "both spellings resolve to the same model and cache entry"
    assert calls[0][2] == "eleven_v3"


def test_raw_model_id_passes_through(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("hello", quality="eleven_multilingual_v2")

    assert calls[0][2] == "eleven_multilingual_v2"


def test_stream_honors_the_selector(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    list(voice.tts_stream("One sentence. Two sentences.", quality="v3"))

    assert len(calls) == 2
    assert {q for _, _, q in calls} == {"eleven_v3"}


async def test_route_default_is_flash(monkeypatch):
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()
    from app.server import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/tts", params={"text": "spoken answer"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert calls[0][2] == "eleven_flash_v2_5", "route default must be flash"


async def test_route_forwards_v3(monkeypatch):
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()
    from app.server import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/tts", params={"text": "spoken answer", "quality": "v3"})

    assert r.status_code == 200
    assert calls[0][2] == "eleven_v3"
