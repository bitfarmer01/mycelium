"""Browser speechSynthesis fallback (feature 58): with the ElevenLabs API key
unset or the API erroring, the console speaks via speechSynthesis and shows a
visible fallback badge while it does.

Server side, an unset key makes /tts return 502 — the Audio element then fires
onerror and audio.js routes playback to browserSpeak(). Console wiring is
asserted statically (same style as test_silent_mode.py); the visible badge is
exercised in the browser with agent-browser, recorded as PR evidence.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def test_console_has_visible_fallback_badge() -> None:
    """index.html carries the badge: hidden by default, labeled, tooltip-explained."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="voiceFallback"' in html, "console has no fallback badge element"
    badge = html[html.find('id="voiceFallback"') - 200 : html.find('id="voiceFallback"') + 300]
    assert "hidden" in badge, "badge must start hidden"
    assert "title=" in badge, "badge needs a tooltip explaining the fallback"


def test_browser_speak_engages_the_badge() -> None:
    """Every browserSpeak() call turns the badge on — the badge is the visible
    signal that speechSynthesis (not ElevenLabs) produced the audio."""
    audio = (JS_DIR / "audio.js").read_text(encoding="utf-8")
    fn = audio[audio.find("function browserSpeak") : audio.find("function stopAudio")]
    assert fn.count("setFallback(true)") == 2, (
        "browserSpeak must show the badge on both the speak path and the catch path"
    )


def test_normal_playback_and_stop_clear_the_badge() -> None:
    """A working ElevenLabs play clears the badge, and stopAudio always does —
    a stale badge must never outlive the fallback it signals."""
    audio = (JS_DIR / "audio.js").read_text(encoding="utf-8")
    play_block = audio[audio.find("currentAudio = a;") :]
    assert "setFallback(false)" in play_block, "successful playback must clear the badge"
    stop_block = audio[audio.find("function stopAudio") : audio.find("/* play audio_url")]
    assert "setFallback(false)" in stop_block, "stopAudio must clear the badge"
    assert audio.find("setFallback(false)") < audio.find("new Audio("), (
        "speak() clears any stale badge (via stopAudio) before the next playback attempt"
    )


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_tts_returns_502_without_api_key(client, monkeypatch) -> None:
    """The unset-key condition that drives the console fallback: /tts answers
    502 (never a 5xx crash), so Audio.onerror fires and browserSpeak takes over."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    async with client as c:
        r = await c.get("/tts", params={"text": "fallback badge sanity"})
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]
