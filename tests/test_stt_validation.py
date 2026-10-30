"""STT upload hardening (feature 53): oversized and wrong-format uploads are
refused with 413/415 and a clear message before any ElevenLabs transcription
spend; valid webm/m4a are accepted and reach the transcriber.

Unit tests run the validator directly; route tests go through the ASGI app
with core.voice.stt monkeypatched, so no network and no API key are needed.
"""

from __future__ import annotations

import httpx
import pytest

from core import voice

# --------------------------------------------------------------------------- unit


def test_valid_webm_accepted() -> None:
    mime = voice.validate_stt_upload(b"abc", "audio/webm;codecs=opus", "audio.webm")
    assert mime == "audio/webm"


def test_valid_m4a_accepted() -> None:
    for ct in ("audio/mp4", "audio/m4a", "audio/x-m4a"):
        assert voice.validate_stt_upload(b"abc", ct, "clip.m4a").startswith("audio/")


def test_octet_stream_with_known_filename_accepted() -> None:
    """Some clients claim a generic Content-Type; the filename extension decides."""
    mime = voice.validate_stt_upload(b"abc", "application/octet-stream", "audio.webm")
    assert mime == "audio/webm"


def test_wrong_format_rejected_415() -> None:
    for ct, name in (("text/plain", "notes.txt"), ("application/json", None), ("application/octet-stream", "blob.bin")):
        with pytest.raises(voice.SttUploadError) as exc:
            voice.validate_stt_upload(b"abc", ct, name)
        assert exc.value.status_code == 415
        assert "format" in str(exc.value)


def test_oversized_rejected_413(monkeypatch) -> None:
    monkeypatch.setattr(voice, "STT_MAX_BYTES", 1024)
    with pytest.raises(voice.SttUploadError) as exc:
        voice.validate_stt_upload(b"x" * 1025, "audio/webm", "audio.webm")
    assert exc.value.status_code == 413
    assert "too large" in str(exc.value)


def test_empty_upload_rejected_415() -> None:
    with pytest.raises(voice.SttUploadError) as exc:
        voice.validate_stt_upload(b"", "audio/webm", "audio.webm")
    assert exc.value.status_code == 415


def test_missing_content_type_rejected_without_filename() -> None:
    with pytest.raises(voice.SttUploadError) as exc:
        voice.validate_stt_upload(b"abc", None, None)
    assert exc.value.status_code == 415
    assert "unknown" in str(exc.value)


# --------------------------------------------------------------------------- route


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _stub_transcriber(monkeypatch, text: str = "hello") -> list[tuple[bytes, str]]:
    calls: list[tuple[bytes, str]] = []

    def fake_stt(data: bytes, mime: str) -> str:
        calls.append((data, mime))
        return text

    monkeypatch.setattr(voice, "stt", fake_stt)
    return calls


async def test_route_accepts_valid_webm(client, monkeypatch) -> None:
    calls = _stub_transcriber(monkeypatch)
    async with client as c:
        r = await c.post("/stt", files={"audio": ("audio.webm", b"abc", "audio/webm")})
    assert r.status_code == 200
    assert r.json() == {"text": "hello"}
    assert calls and calls[0][1] == "audio/webm"


async def test_route_rejects_wrong_format_415(client, monkeypatch) -> None:
    _stub_transcriber(monkeypatch)
    async with client as c:
        r = await c.post("/stt", files={"audio": ("notes.txt", b"abc", "text/plain")})
    assert r.status_code == 415
    assert "format" in r.json()["detail"].lower()


async def test_route_rejects_oversized_413(client, monkeypatch) -> None:
    _stub_transcriber(monkeypatch)
    monkeypatch.setattr(voice, "STT_MAX_BYTES", 8)
    async with client as c:
        r = await c.post("/stt", files={"audio": ("audio.webm", b"x" * 9, "audio/webm")})
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]


async def test_route_415_short_circuits_before_transcriber(client, monkeypatch) -> None:
    """A rejected upload must never reach the ElevenLabs call — zero spend."""
    calls = _stub_transcriber(monkeypatch)
    async with client as c:
        await c.post("/stt", files={"audio": ("notes.txt", b"abc", "text/plain")})
    assert calls == []
