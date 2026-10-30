"""TTS cache (feature 52): a repeated spoken string costs one API call.

The mock counter replaces the ElevenLabs HTTP boundary (`_tts_request`) and the
voice lookup, so every assertion below is exact, not statistical.
"""

from __future__ import annotations

from core import voice

TEST_VOICE = "test-voice-id"


def _counter(monkeypatch, payload: bytes = b"mp3-bytes") -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    def fake_request(text: str, vid: str, quality: str) -> bytes:
        calls.append((text, vid, quality))
        return payload

    monkeypatch.setattr(voice, "_tts_request", fake_request)
    monkeypatch.setattr(voice, "_voice_id", TEST_VOICE)  # skips the voices HTTP lookup
    return calls


def test_same_spoken_string_costs_one_api_call(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    first = voice.tts("Mycelium answers from your own wiki.")
    second = voice.tts("Mycelium answers from your own wiki.")

    assert first == second
    assert len(calls) == 1, "second identical call must come from the cache"


def test_cache_key_distinguishes_quality(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    flash = voice.tts("same text")
    v3 = voice.tts("same text", quality="eleven_v3")

    assert len(calls) == 2
    assert flash == v3, "same payload factory, but two distinct cache entries"


def test_cache_key_distinguishes_voice(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("same text")
    monkeypatch.setattr(voice, "_voice_id", "other-voice")
    voice.tts("same text")

    assert len(calls) == 2
    assert {vid for _, vid, _ in calls} == {TEST_VOICE, "other-voice"}


def test_cache_key_distinguishes_text(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("first sentence")
    voice.tts("second sentence")

    assert len(calls) == 2


def test_empty_text_never_reaches_the_api(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    try:
        voice.tts("   ")
    except voice.VoiceError:
        pass
    else:  # pragma: no cover - guard for a future behavior change
        raise AssertionError("blank text should raise VoiceError")
    assert calls == []


def test_failed_synthesis_is_not_cached(monkeypatch) -> None:
    calls = _counter(monkeypatch)
    voice.tts_cache_clear()

    def failing(text: str, vid: str, quality: str) -> bytes:
        calls.append((text, vid, quality))
        raise voice.VoiceError("tts failed: HTTP 503")

    monkeypatch.setattr(voice, "_tts_request", failing)
    try:
        voice.tts("retry me")
    except voice.VoiceError:
        pass

    monkeypatch.setattr(voice, "_tts_request", lambda t, v, q: b"ok")
    assert voice.tts("retry me") == b"ok"
    assert len(calls) == 1
